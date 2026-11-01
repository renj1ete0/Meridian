"""What is new in the evidence since an instant, by topic and kind (task B-237).

Read-only, for `/api/explore/new` and the `whats_new` MCP tool, which call this one function
so they cannot disagree. Sources are counted with :func:`stats.kept_sources`, the landing's
rule; passages with search's own predicate. No language model: the excerpt is the passage the
topic labeller scored highest. See docs/features/whats-new.md.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Float, Text, and_, any_, cast, exists, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .annotations import ANNOTATES
from .models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    ChunkTopics,
    Edge,
    Entity,
    Source,
    TopicConfig,
)
from .references import is_reference_list
from .schemas.whatsnew import (
    NewBasis,
    NewCitationRead,
    NewExcerptRead,
    NewFindingRead,
    NewKindCountRead,
    NewSourceRead,
    NewTopicRead,
    WhatsNewRead,
)
from .search import SearchFilters, passage_conditions, site_key
from .stats import kept_sources
from .timefmt import display_zone
from .trust import READABLE_STATES

#: Sources listed per topic by default, and at most.
DEFAULT_PER_TOPIC = 5
MAX_PER_TOPIC = 20

#: Findings listed by default, and at most.
DEFAULT_FINDINGS = 10
MAX_FINDINGS = 50

#: How far back a window reaches; an earlier ``since`` is moved up to it. Growth answers longer.
MAX_WINDOW = dt.timedelta(days=366)

#: Characters an excerpt or a cited passage is cut to: one line on the page.
EXCERPT_CHARS = 240

#: A passage shorter than this is a heading or a caption, passed over for the excerpt.
MIN_EXCERPT_CHARS = 60

#: Best-scored passages per source looked at for the excerpt.
EXCERPT_CANDIDATES = 4

#: Followed for links but never chunked: counted, not listed (nothing in one to read).
UNLISTED_KINDS = ("listing",)


def one_line(text: str, limit: int = EXCERPT_CHARS) -> str:
    """``text`` on one line, cut at a word under ``limit`` characters with an ellipsis."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


def pick_excerpt(candidates: Sequence[tuple[int, str, int | None]]) -> NewExcerptRead | None:
    """The first candidate that reads as prose: not a reference list, not a heading.

    ``candidates`` are ``(chunk_id, text, page_or_offset)``, best first. A source whose
    candidates are all short falls back to the first that is not a reference list.
    """
    prose = [c for c in candidates if not is_reference_list(c[1])]
    chosen = next((c for c in prose if len(c[1].strip()) >= MIN_EXCERPT_CHARS), None)
    chosen = chosen or (prose[0] if prose else None)
    if chosen is None:
        return None
    return NewExcerptRead(chunk_id=chosen[0], text=one_line(chosen[1]), page_or_offset=chosen[2])


def _leading(labels_column, shown: Sequence[str]):
    """The first of a row's topic labels that is shown, in label order (best first)."""
    labels = (
        func.unnest(labels_column).table_valued("label", with_ordinality="ord").render_derived()
    )
    return (
        select(labels.c.label)
        .where(labels.c.label == any_(cast(array(list(shown)), ARRAY(Text))))
        .order_by(labels.c.ord)
        .limit(1)
        .scalar_subquery()
    )


async def whats_new(
    sess: AsyncSession,
    *,
    since: dt.datetime,
    as_of: dt.datetime | None = None,
    basis: NewBasis = "added",
    topics: Sequence[str] | None = None,
    doc_kinds: Sequence[str] | None = None,
    per_topic: int = DEFAULT_PER_TOPIC,
    findings: int = DEFAULT_FINDINGS,
    cleared_only: bool = False,
) -> WhatsNewRead:
    """New sources since ``since``, grouped by their leading topic, and new findings.

    ``basis="added"`` means entered the corpus after ``since``; ``"published"`` means dated on
    or after its calendar day in the display zone and not after today (a later date is a
    parsing error). ``cleared_only`` narrows sources, excerpts and citations to what screening
    cleared, for anything handing them to a model (§2.5). A window wider than
    :data:`MAX_WINDOW` is narrowed to it. Raises ``ValueError`` for a naive instant or a
    ``since`` after ``as_of``.
    """
    as_of = as_of or dt.datetime.now(dt.UTC)
    if since.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("since and as_of must carry an offset")
    if since > as_of:
        raise ValueError("since is after as_of")
    # Not refused: a reader back after a long absence gets the last year, and `since` in the
    # answer says so.
    since = max(since, as_of - MAX_WINDOW)
    per_topic = max(0, min(per_topic, MAX_PER_TOPIC))
    findings = max(0, min(findings, MAX_FINDINGS))

    configs = list(
        await sess.execute(
            select(TopicConfig.topic, TopicConfig.description)
            .where(TopicConfig.status != "archived")
            .order_by(TopicConfig.weight.desc(), TopicConfig.topic)
        )
    )
    if topics:
        wanted = set(topics)
        configs = [c for c in configs if c[0] in wanted]
    shown = [c[0] for c in configs]
    passages = passage_conditions(SearchFilters(cleared_only=cleared_only))

    window = [Source.created_at <= as_of, *kept_sources()]
    if basis == "added":
        window.append(Source.created_at > since)
        newest_first = (Source.created_at.desc(), Source.source_id.desc())
    else:
        zone = await display_zone(sess)
        first_day = since.astimezone(ZoneInfo(zone)).date()
        last_day = as_of.astimezone(ZoneInfo(zone)).date()
        window += [Source.publication_date >= first_day, Source.publication_date <= last_day]
        newest_first = (
            Source.publication_date.desc(),
            Source.created_at.desc(),
            Source.source_id.desc(),
        )
    if doc_kinds:
        window.append(Source.doc_kind.in_(list(doc_kinds)))
    if cleared_only:
        window.append(Source.trust_state.in_(READABLE_STATES))

    lead = _leading(Source.topic_labels, shown) if shown else cast(literal(None), Text)
    new = (
        select(
            Source.source_id,
            Source.doc_kind,
            lead.label("topic"),
            func.row_number().over(order_by=newest_first).label("recency"),
        )
        .where(*window)
        .subquery()
    )

    counts: dict[str | None, dict[str | None, int]] = {}
    for topic, kind, n in await sess.execute(
        select(new.c.topic, new.c.doc_kind, func.count()).group_by(new.c.topic, new.c.doc_kind)
    ):
        counts.setdefault(topic, {})[kind] = int(n)

    ranked = (
        select(
            new.c.source_id,
            new.c.topic,
            func.row_number().over(partition_by=new.c.topic, order_by=new.c.recency).label("rank"),
        )
        .where(new.c.topic.is_not(None), new.c.doc_kind.is_distinct_from(UNLISTED_KINDS[0]))
        .subquery()
    )
    listed = list(
        await sess.execute(
            select(ranked.c.source_id, ranked.c.topic)
            .where(ranked.c.rank <= per_topic)
            .order_by(ranked.c.topic, ranked.c.rank)
        )
    )
    ids = [row[0] for row in listed]
    sources = (
        {
            s.source_id: s
            for s in await sess.scalars(select(Source).where(Source.source_id.in_(ids)))
        }
        if ids
        else {}
    )
    excerpts = await _excerpts(sess, [(row[0], row[1]) for row in listed], passages)

    by_topic: dict[str, list[NewSourceRead]] = {}
    for source_id, topic in listed:
        source = sources[source_id]
        by_topic.setdefault(topic, []).append(
            NewSourceRead(
                source_id=source.source_id,
                title=source.title,
                url=source.url,
                site=site_key(source.url),
                publisher=source.publisher,
                doc_kind=source.doc_kind,  # type: ignore[arg-type]
                source_tier=source.source_tier,  # type: ignore[arg-type]
                published=source.publication_date,
                added=source.created_at,
                other_topics=[t for t in source.topic_labels or () if t in shown and t != topic],
                excerpt=excerpts.get(source_id),
            )
        )

    groups = []
    for topic, description in configs:
        kinds = counts.get(topic, {})
        groups.append(
            NewTopicRead(
                topic=topic,
                description=description,
                total=sum(kinds.values()),
                by_kind=[
                    NewKindCountRead(doc_kind=k, sources=n)  # type: ignore[arg-type]
                    for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0] or "~"))
                ],
                sources=by_topic.get(topic, []),
            )
        )

    found, found_total = await _findings(
        sess,
        since=since,
        as_of=as_of,
        shown=shown,
        narrowed=bool(topics),
        passages=passages,
        limit=findings,
    )
    return WhatsNewRead(
        since=since,
        as_of=as_of,
        basis=basis,
        topics=groups,
        elsewhere=sum(counts.get(None, {}).values()),
        findings=found,
        findings_total=found_total,
    )


async def _excerpts(
    sess: AsyncSession, wanted: Sequence[tuple[int, str]], passages: list
) -> dict[int, NewExcerptRead]:
    """Each source's best passage for its topic: highest passage score, then reading order.

    Passages the labeller has not scored sort after those it has; within a tie, the earliest.
    """
    if not wanted:
        return {}
    want = (
        func.unnest(
            cast(array([s for s, _ in wanted]), ARRAY(BigInteger)),
            cast(array([t for _, t in wanted]), ARRAY(Text)),
        )
        .table_valued("source_id", "topic")
        .render_derived(name="want")
    )
    score = cast(ChunkTopics.topic_scores[want.c.topic].astext, Float)
    ranked = (
        select(
            Chunk.chunk_id,
            Chunk.source_id,
            Chunk.text,
            Chunk.page_or_offset,
            func.row_number()
            .over(
                partition_by=Chunk.source_id,
                order_by=(score.desc().nulls_last(), Chunk.chunk_index, Chunk.chunk_id),
            )
            .label("rank"),
        )
        .select_from(want)
        .join(Chunk, Chunk.source_id == want.c.source_id)
        .join(Source, Source.source_id == Chunk.source_id)
        .outerjoin(ChunkTopics, ChunkTopics.chunk_id == Chunk.chunk_id)
        .where(*passages)
        .subquery()
    )
    candidates: dict[int, list[tuple[int, str, int | None]]] = {}
    for chunk_id, source_id, text, page in await sess.execute(
        select(ranked.c.chunk_id, ranked.c.source_id, ranked.c.text, ranked.c.page_or_offset)
        .where(ranked.c.rank <= EXCERPT_CANDIDATES)
        .order_by(ranked.c.source_id, ranked.c.rank)
    ):
        candidates.setdefault(source_id, []).append((chunk_id, text, page))
    return {
        source_id: excerpt
        for source_id, rows in candidates.items()
        if (excerpt := pick_excerpt(rows)) is not None
    }


def _citable(chunk_ids_column, passages: list):
    """Whether any of a finding's supporting passages is one a reader may open."""
    return exists().where(
        Chunk.chunk_id == any_(chunk_ids_column),
        Source.source_id == Chunk.source_id,
        *passages,
    )


def _live(entity) -> list:
    """A concept a reader is shown: not a note, not merged away, not withdrawn."""
    return [
        entity.node_type != "annotation",
        entity.redirects_to.is_(None),
        entity.withdrawn_at.is_(None),
    ]


async def _findings(
    sess: AsyncSession,
    *,
    since: dt.datetime,
    as_of: dt.datetime,
    shown: Sequence[str],
    narrowed: bool,
    passages: list,
    limit: int,
) -> tuple[list[NewFindingRead], int]:
    """Links and tags added in the window, newest first, each with a readable citation.

    Placed by their own topic labels; with topics asked for, one on none of them is left out.
    """
    subject, other = aliased(Entity), aliased(Entity)
    link_topic = _leading(Edge.topic_labels, shown) if shown else cast(literal(None), Text)
    links = (
        select(
            literal("link").label("kind"),
            Edge.edge_id.label("finding_id"),
            subject.entity_id.label("subject_id"),
            subject.canonical_name.label("subject"),
            Edge.relation_type.label("relation"),
            other.entity_id.label("object_id"),
            other.canonical_name.label("object"),
            link_topic.label("topic"),
            Edge.created_at.label("added"),
            Edge.supporting_chunk_ids.label("chunks"),
        )
        .join(subject, subject.entity_id == Edge.from_node)
        .join(other, other.entity_id == Edge.to_node)
        .where(
            Edge.created_at > since,
            Edge.created_at <= as_of,
            Edge.relation_type != ANNOTATES,
            *_live(subject),
            *_live(other),
            _citable(Edge.supporting_chunk_ids, passages),
        )
    )
    tag_topic = _leading(subject.topic_labels, shown) if shown else cast(literal(None), Text)
    tags = (
        select(
            literal("tag").label("kind"),
            AttributeValue.value_id.label("finding_id"),
            subject.entity_id.label("subject_id"),
            subject.canonical_name.label("subject"),
            AttributeDefinition.name.label("relation"),
            cast(literal(None), BigInteger).label("object_id"),
            func.coalesce(AttributeValue.value, cast(AttributeValue.value_numeric, Text)).label(
                "object"
            ),
            tag_topic.label("topic"),
            AttributeValue.created_at.label("added"),
            AttributeValue.supporting_chunk_ids.label("chunks"),
        )
        .join(subject, subject.entity_id == AttributeValue.entity_id)
        .join(AttributeDefinition, AttributeDefinition.attribute_id == AttributeValue.attribute_id)
        .where(
            AttributeValue.created_at > since,
            AttributeValue.created_at <= as_of,
            *_live(subject),
            _citable(AttributeValue.supporting_chunk_ids, passages),
        )
    )
    both = links.union_all(tags).subquery()
    placed = [both.c.topic.is_not(None)] if narrowed else []
    total = int(await sess.scalar(select(func.count()).select_from(both).where(*placed)) or 0)
    if limit == 0 or total == 0:
        return [], total
    rows = list(
        await sess.execute(
            select(both)
            .where(*placed)
            .order_by(both.c.added.desc(), both.c.kind, both.c.finding_id.desc())
            .limit(limit)
        )
    )

    # The first supporting passage, in the order synthesis cited them, that a reader may open.
    cited = sorted({c for row in rows for c in row.chunks})
    readable = {
        chunk.chunk_id: (chunk, source)
        for chunk, source in await sess.execute(
            select(Chunk, Source)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id.in_(cited), and_(*passages))
        )
    }
    out = []
    for row in rows:
        first = next((c for c in row.chunks if c in readable), None)
        if first is None:  # screened out between the count and now
            continue
        chunk, source = readable[first]
        out.append(
            NewFindingRead(
                kind=row.kind,
                finding_id=row.finding_id,
                subject_id=row.subject_id,
                subject=row.subject,
                relation=row.relation,
                object_id=row.object_id,
                object=row.object,
                topic=row.topic,
                added=row.added,
                citation=NewCitationRead(
                    chunk_id=chunk.chunk_id,
                    source_id=source.source_id,
                    title=source.title,
                    url=source.url,
                    page_or_offset=chunk.page_or_offset,
                    text=one_line(chunk.text),
                ),
            )
        )
    return out, total
