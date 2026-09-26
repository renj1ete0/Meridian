"""Gaps: one ranked list of what the corpus cannot yet answer (task P6-36).

Each gap is a concrete finding with its reason in numbers and an action that
goes through machinery that already exists — a search seed in the queue, a
topic boost through :mod:`meridian_core.steering` — so every action is
reversible and leaves a `steering_log` row (§10.1). Nothing here writes; the
actions are applied by the admin routes.

**The sources are pluggable.** A gap source is an async function from a
session to a list of :class:`Gap`, registered under a name. What exists today:

- ``topic-coverage`` — per topic: thin (few sources), weak (few of them
  government or peer-reviewed), stale (the newest dated one is old). Each
  finding also counts on-topic *passages* (`P2-24`), including those inside
  documents labelled with something else.
- ``place-coverage`` — per topic, the places of the comparison set (§7.2)
  with fewer than a few sources about them (`P2-23`). Unavailable until a
  source has been examined for places.
- ``search-queries`` (`P6-37`) — answered searches that found nothing, or
  only pages already queued, from each query's recorded yield (`B-56`);
  grouped per topic and kind so repeated failures are one row.
- ``search-results`` (`P6-37`) — topics whose search-found pages turned out,
  by content, to be mostly about something else. Per topic: the queue does
  not link a result to the query that found it.
- ``question-set`` — items of the held-out set that score low in the newest
  run file. An operator grade counts; a heuristic proposal is shown as one and
  ranked below any real score.

- ``routes`` — pairs of topics whose most-cited nodes no chain of stated
  links joins within a few hops, using `P6-32`'s claims-only answer: joined
  only by resemblance, or not at all. Bounded to a few pairs per read.

- ``areas`` (`P6-42`) — fields of the newest map build that are mostly about
  the topics yet rest on few sources, hold no government or peer-reviewed
  passage, or have had nothing new in months. A field mostly *off* the topics
  is not a gap in the evidence; the Map shades it instead.

A source the design names and nobody built is listed as *pending* rather than
silently absent, so an empty list is never mistaken for "no gaps of that kind".

**The held-out rule shapes the question-set actions.** `eval/README.md` forbids
using a question as a seed or a steering reason, so a low-scoring item offers
"search it in Find" — reading, not steering — and never "seed this question".
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import false, func, select, text, true, union
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, ChunkTopics, QueueTask, Source, TopicConfig
from .route import DEFAULT_ROUTE_DEPTH, Endpoint
from .route import route as find_route
from .searchseeds import topic_words

#: Fewer on-topic sources than this is thin. A number to argue with, not a law:
#: an evidence question needs "at least three independent sources"
#: (eval rubric), and a topic needs several questions' worth.
THIN_SOURCES = 10

#: Fewer government or peer-reviewed sources than this is weak.
STRONG_TIERS = ("government", "peer_reviewed")
WEAK_STRONG = 3

#: The newest dated source older than this is stale.
STALE_YEARS = 3

#: A boost offered from Gaps: doubled for a week, then gone by itself (§10).
BOOST_FACTOR = 2.0
BOOST_DAYS = 7

#: Topics whose gaps are worth acting on. Archived topics left the pool on
#: purpose; showing them as gaps would argue with a decision.
LIVE_STATUSES = ("active", "maintenance", "paused")


@dataclasses.dataclass(frozen=True)
class Action:
    kind: str  # seed_query | boost_topic | open_search
    label: str
    topic: str | None = None
    query: str | None = None
    factor: float | None = None
    days: int | None = None


@dataclasses.dataclass(frozen=True)
class Gap:
    id: str
    source: str
    kind: str
    subject: str
    title: str
    reason: str
    severity: float
    evidence: dict[str, Any]
    actions: tuple[Action, ...]


@dataclasses.dataclass(frozen=True)
class SourceStatus:
    name: str
    status: str  # ok | unavailable | pending
    note: str | None = None
    gaps: int = 0


GapSource = Callable[[AsyncSession], Awaitable[list[Gap]]]

#: name -> source. Ordered: it is the order statuses are reported in.
SOURCES: dict[str, GapSource] = {}

#: Sources the design names that are not built yet, and the task that builds them.
PENDING: dict[str, str] = {}


class SourceUnavailable(RuntimeError):
    """A source that cannot answer here — reported, not raised to the caller."""


def register(name: str) -> Callable[[GapSource], GapSource]:
    def wrap(fn: GapSource) -> GapSource:
        SOURCES[name] = fn
        PENDING.pop(name, None)
        return fn

    return wrap


def rank(gaps: list[Gap]) -> list[Gap]:
    """Most severe first; ties by id so the list does not reshuffle between reads."""
    return sorted(gaps, key=lambda g: (-g.severity, g.id))


async def find_gaps(
    sess: AsyncSession, *, sources: dict[str, GapSource] | None = None
) -> tuple[list[Gap], list[SourceStatus]]:
    """Every registered source's gaps, ranked, with each source's status."""
    found: list[Gap] = []
    statuses: list[SourceStatus] = []
    for name, source in (sources if sources is not None else SOURCES).items():
        try:
            gaps = await source(sess)
        except SourceUnavailable as exc:
            statuses.append(SourceStatus(name, "unavailable", str(exc)))
            continue
        found.extend(gaps)
        statuses.append(SourceStatus(name, "ok", None, len(gaps)))
    if sources is None:
        statuses.extend(SourceStatus(name, "pending", note) for name, note in PENDING.items())
    return rank(found), statuses


# ---------------------------------------------------------------------------
# Topic coverage
# ---------------------------------------------------------------------------


def _seed_and_boost(topic: str, description: str | None) -> tuple[Action, ...]:
    words = " ".join((description or "").split()[:8]) or topic_words(topic)
    return (
        Action("seed_query", "Seed a search", topic=topic, query=words),
        Action(
            "boost_topic",
            f"Boost ×{BOOST_FACTOR:g} for {BOOST_DAYS} days",
            topic=topic,
            factor=BOOST_FACTOR,
            days=BOOST_DAYS,
        ),
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _elsewhere(passage_sources: int) -> str:
    if not passage_sources:
        return ""
    return (
        f" On-topic passages also sit in {_plural(passage_sources, 'other document')}"
        " labelled with something else."
    )


def coverage_gaps(
    topic: str,
    *,
    description: str | None,
    sources: int,
    strong: int,
    passages: int,
    newest: dt.date | None,
    share: float | None,
    corpus_share: float | None,
    today: dt.date,
    passage_sources: int = 0,
) -> list[Gap]:
    """The coverage findings for one topic. Pure, so the thresholds are testable.

    ``passages`` counts on-topic passages (`P2-24`): chunks labelled with the
    topic themselves, or belonging to a source that is. ``passage_sources``
    counts the documents *not* labelled with the topic that hold at least one
    passage about it — a chapter inside a book about something else.

    **Thin is still judged on sources.** A passage label is one chunk's vector,
    noisier than a document's mean, and a topic that exists in the corpus only
    as asides in other documents is thin in the sense that matters: there is
    no document to cite as being about it. So those documents are counted and
    shown, and do not lift a topic out of "thin".
    """
    evidence = {
        "sources": sources,
        "strong_sources": strong,
        "passages": passages,
        "passage_sources": passage_sources,
        "newest": newest.isoformat() if newest else None,
        "crawl_share": None if share is None else round(share, 3),
        "corpus_share": None if corpus_share is None else round(corpus_share, 3),
    }
    actions = _seed_and_boost(topic, description)
    share_note = ""
    if share is not None and corpus_share is not None:
        share_note = (
            f" {corpus_share:.1%} of on-topic sources against a {share:.0%} share of the crawl."
        )
    out: list[Gap] = []
    if sources < THIN_SOURCES:
        if sources:
            title = f"{_plural(sources, 'source')}, {strong} government or peer-reviewed"
        elif passage_sources:
            title = f"No sources; passages in {_plural(passage_sources, 'other document')}"
        else:
            title = "No sources"
        out.append(
            Gap(
                id=f"topic-thin:{topic}",
                source="topic-coverage",
                kind="thin",
                subject=topic,
                title=title,
                reason=(
                    f"{_plural(sources, 'source')} labelled {topic} by content, "
                    f"{_plural(passages, 'passage')} about it; fewer than {THIN_SOURCES} "
                    "sources is thin." + _elsewhere(passage_sources) + share_note
                ),
                severity=round(0.6 + 0.4 * (1 - sources / THIN_SOURCES), 3),
                evidence=evidence,
                actions=actions,
            )
        )
    elif strong < WEAK_STRONG:
        out.append(
            Gap(
                id=f"topic-weak:{topic}",
                source="topic-coverage",
                kind="weak",
                subject=topic,
                title=f"{sources} sources, {strong} government or peer-reviewed",
                reason=(
                    f"{_plural(sources, 'source')}, {strong} of them government or "
                    f"peer-reviewed; fewer than {WEAK_STRONG} leaves an evidence question "
                    "resting on institutional and informal material."
                    + _elsewhere(passage_sources)
                    + share_note
                ),
                severity=round(0.35 + 0.2 * (1 - strong / WEAK_STRONG), 3),
                evidence=evidence,
                actions=actions,
            )
        )
    if sources and newest is not None and (today - newest).days > 365 * STALE_YEARS:
        out.append(
            Gap(
                id=f"topic-stale:{topic}",
                source="topic-coverage",
                kind="stale",
                subject=topic,
                title=f"Newest dated source {newest.year}",
                reason=(
                    f"The newest dated {topic} source is from {newest.isoformat()}, "
                    f"more than {STALE_YEARS} years ago."
                ),
                severity=0.3,
                evidence=evidence,
                actions=actions,
            )
        )
    return out


@register("topic-coverage")
async def topic_coverage(sess: AsyncSession, *, today: dt.date | None = None) -> list[Gap]:
    from . import steering

    today = today or dt.date.today()
    now = dt.datetime.now(dt.UTC)
    rows = list(
        await sess.scalars(
            select(TopicConfig)
            .where(TopicConfig.status.in_(LIVE_STATUSES))
            .order_by(TopicConfig.topic)
        )
    )
    if not rows:
        return []
    shares = steering.draw_shares(rows, now=now)

    label = func.unnest(Source.topic_labels).table_valued("value").render_derived("t")
    per_topic = {
        topic: (n, strong, newest)
        for topic, n, strong, newest in await sess.execute(
            select(
                label.c.value,
                func.count(),
                func.count().filter(Source.source_tier.in_(STRONG_TIERS)),
                func.max(Source.publication_date),
            )
            .select_from(Source)
            .join(label, true())
            .group_by(label.c.value)
        )
    }
    passages, passage_sources = await _passage_counts(sess)
    on_topic_total = sum(n for n, _, _ in per_topic.values()) or 0

    out: list[Gap] = []
    for row in rows:
        n, strong, newest = per_topic.get(row.topic, (0, 0, None))
        out.extend(
            coverage_gaps(
                row.topic,
                description=row.description,
                sources=n,
                strong=strong,
                passages=int(passages.get(row.topic, 0)),
                passage_sources=int(passage_sources.get(row.topic, 0)),
                newest=newest,
                share=shares.get(row.topic),
                corpus_share=(n / on_topic_total) if on_topic_total else None,
                today=today,
            )
        )
    return out


async def _passage_counts(sess: AsyncSession) -> tuple[dict[str, int], dict[str, int]]:
    """Per topic: on-topic live passages, and the other documents holding some.

    A passage is on-topic when its own labels carry the topic (`P2-24`) or its
    source's do — counted once either way. The second count is the documents
    whose *own* labels do not carry the topic but which hold at least one
    passage that does: where the topic lives only as an aside.
    """
    source_label = func.unnest(Source.topic_labels).table_valued("value").render_derived("sl")
    by_source = (
        select(source_label.c.value.label("topic"), Chunk.chunk_id)
        .select_from(Source)
        .join(source_label, true())
        .join(Chunk, Chunk.source_id == Source.source_id)
        .where(Chunk.superseded_at.is_(None))
    )
    passage_label = func.unnest(ChunkTopics.topic_labels).table_valued("value").render_derived("pl")
    by_passage = (
        select(passage_label.c.value.label("topic"), ChunkTopics.chunk_id)
        .select_from(ChunkTopics)
        .join(passage_label, true())
        .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
        .where(Chunk.superseded_at.is_(None))
    )
    on_topic = union(by_source, by_passage).subquery()
    passages = dict(
        (
            await sess.execute(select(on_topic.c.topic, func.count()).group_by(on_topic.c.topic))
        ).all()
    )
    elsewhere = dict(
        (
            await sess.execute(
                select(passage_label.c.value, func.count(func.distinct(Chunk.source_id)))
                .select_from(ChunkTopics)
                .join(passage_label, true())
                .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
                .join(Source, Source.source_id == Chunk.source_id)
                .where(
                    Chunk.superseded_at.is_(None),
                    ~func.coalesce(Source.topic_labels.any(passage_label.c.value), false()),
                )
                .group_by(passage_label.c.value)
            )
        ).all()
    )
    return passages, elsewhere


# ---------------------------------------------------------------------------
# Place coverage
# ---------------------------------------------------------------------------

#: Fewer sources than this about a topic *and* a place is thin for that pair.
#: Lower than :data:`THIN_SOURCES` because it is a cell of a table, not a row:
#: a comparison needs a few sources per place, not ten.
PLACE_THIN = 3


def place_gaps(
    topic: str,
    place: str,
    place_name: str,
    *,
    sources: int,
    strong: int,
    topic_sources: int,
) -> list[Gap]:
    """The finding for one topic in one place. Pure, so the threshold is testable.

    Ranked below the topic's own coverage gaps on purpose: a topic with nothing
    at all is the first thing to fix, and a table of empty cells under it would
    bury that.
    """
    if sources >= PLACE_THIN:
        return []
    words = f"{topic_words(topic)} {place_name}"
    evidence = {
        "place": place,
        "sources": sources,
        "strong_sources": strong,
        "topic_sources": topic_sources,
    }
    return [
        Gap(
            id=f"place-thin:{topic}:{place}",
            source="place-coverage",
            kind="place_thin",
            subject=f"{topic} · {place_name}",
            title=(
                f"No {topic} sources about {place_name}"
                if sources == 0
                else f"{_plural(sources, 'source')} about {place_name}"
            ),
            reason=(
                f"{_plural(sources, 'source')} labelled {topic} are about {place_name} "
                f"({place}), of {topic_sources} labelled {topic} in all; fewer than "
                f"{PLACE_THIN} leaves this place out of any comparison."
            ),
            severity=round(0.25 + 0.2 * (1 - sources / PLACE_THIN), 3),
            evidence=evidence,
            actions=(
                Action("seed_query", "Seed a search", topic=topic, query=words),
                Action("open_search", "Search it in Find", query=words),
            ),
        )
    ]


@register("place-coverage")
async def place_coverage(sess: AsyncSession) -> list[Gap]:
    """Per topic, the comparison set's places with few sources (`P2-23`, §7.2).

    The comparison set is :func:`meridian_core.places.comparison_set` — the
    places the configuration already names. Unavailable, rather than empty,
    until some source has been examined for places: with nothing examined,
    every cell would read as a gap and none of them would be one.
    """
    from .places import comparison_set

    places = await comparison_set(sess)
    if not places:
        raise SourceUnavailable(
            "no comparison set: no government domain pattern or gazetteer jurisdiction names a "
            "place"
        )
    examined = await sess.scalar(
        select(func.count()).select_from(Source).where(Source.places.is_not(None))
    )
    if not examined:
        raise SourceUnavailable(
            "no source has been examined for places yet — run python -m worker.places --apply"
        )
    rows = list(
        await sess.execute(
            select(TopicConfig.topic, TopicConfig.description)
            .where(TopicConfig.status.in_(LIVE_STATUSES))
            .order_by(TopicConfig.topic)
        )
    )
    if not rows:
        return []

    label = func.unnest(Source.topic_labels).table_valued("value").render_derived("t")
    place = func.unnest(Source.places).table_valued("value").render_derived("p")
    codes = [p.code for p in places]
    cells = {
        (topic, code): (n, strong)
        for topic, code, n, strong in await sess.execute(
            select(
                label.c.value,
                place.c.value,
                func.count(),
                func.count().filter(Source.source_tier.in_(STRONG_TIERS)),
            )
            .select_from(Source)
            .join(label, true())
            .join(place, true())
            .where(place.c.value.in_(codes))
            .group_by(label.c.value, place.c.value)
        )
    }
    per_topic = dict(
        (
            await sess.execute(
                select(label.c.value, func.count())
                .select_from(Source)
                .join(label, true())
                .group_by(label.c.value)
            )
        ).all()
    )

    out: list[Gap] = []
    for topic, _description in rows:
        for p in places:
            n, strong = cells.get((topic, p.code), (0, 0))
            out.extend(
                place_gaps(
                    topic,
                    p.code,
                    p.name,
                    sources=n,
                    strong=strong,
                    topic_sources=int(per_topic.get(topic, 0)),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Fields of the map — clusters on the topics with thin evidence (`P6-42`)
# ---------------------------------------------------------------------------

#: A field counts as being about the topics when at least this share of its
#: examined passages is labelled with one. Below it, thin evidence is not a
#: gap: the field is mostly material the crawl should not grow.
FIELD_ON_TOPIC = 0.5
#: Fewer passages than this is too small a cluster to judge.
FIELD_MIN_PASSAGES = 30


@dataclasses.dataclass(frozen=True)
class FieldStats:
    area_id: int
    #: The field it sits in: the Map opens a level by its parent.
    parent_id: int | None
    name: str
    terms: list[str]
    passages: int
    sources: int
    examined: int | None
    on_topic: int | None
    topic_mix: dict[str, int]
    tier_mix: dict[str, int]
    newest_at: dt.datetime | None


def field_gaps(field: FieldStats, *, now: dt.datetime) -> list[Gap]:
    """The findings for one field of the map. Pure, so the thresholds are testable.

    One gap per field, however many things are wrong with it, with every
    reason stated: three rows for one cluster would crowd out other fields.
    """
    from .areaview import STALE_AFTER_DAYS

    if not field.examined or field.on_topic is None or field.passages < FIELD_MIN_PASSAGES:
        return []  # not measured, or too small to judge
    share = field.on_topic / field.examined
    if share < FIELD_ON_TOPIC:
        return []
    strong = sum(field.tier_mix.get(t, 0) for t in STRONG_TIERS)
    reasons: list[str] = []
    if field.sources < THIN_SOURCES:
        reasons.append(
            f"its {field.passages:,} passages come from {_plural(field.sources, 'source')}; "
            f"fewer than {THIN_SOURCES} is thin"
        )
    if strong == 0:
        reasons.append("none of its passages is from a government or peer-reviewed source")
    stale_days = None if field.newest_at is None else (now - field.newest_at).days
    if stale_days is not None and stale_days > STALE_AFTER_DAYS:
        reasons.append(f"nothing new stored in {stale_days} days")
    if not reasons:
        return []
    topic = (
        max(field.topic_mix.items(), key=lambda kv: (kv[1], kv[0]))[0] if field.topic_mix else None
    )
    words = " ".join(field.terms[:3]) or field.name
    thin = field.sources < THIN_SOURCES
    return [
        Gap(
            id=f"field:{field.area_id}",
            source="areas",
            kind="field_thin" if thin else ("field_weak" if strong == 0 else "field_stale"),
            subject=field.name if topic is None else f"{topic} · {field.name}",
            title=(
                f"{field.name} rests on {_plural(field.sources, 'source')}"
                if thin
                else f"{field.name} has no government or peer-reviewed source"
                if strong == 0
                else f"{field.name} has had nothing new in {stale_days} days"
            ),
            reason=(
                f"A field of the map {round(share * 100)}% about your topics: "
                + "; ".join(reasons)
                + "."
            ),
            severity=round(0.2 + 0.15 * share + (0.1 if thin else 0.0), 3),
            evidence={
                "area_id": field.area_id,
                "parent_id": field.parent_id,
                "passages": field.passages,
                "sources": field.sources,
                "on_topic_share": round(share, 3),
                "strong_passages": strong,
            },
            actions=(
                (Action("seed_query", "Seed a search", topic=topic, query=words),) if topic else ()
            )
            + (Action("open_search", "Search it in Find", query=words),),
        )
    ]


@register("areas")
async def field_coverage(sess: AsyncSession) -> list[Gap]:
    """The newest map build's finest fields that are on the topics and thin (`P6-42`).

    Unavailable, rather than empty, until a build exists and has been measured
    for topics: with nothing measured every field would pass, and "no gaps"
    would be a claim nobody checked.
    """
    from .areaview import area_name, latest_build, usable_terms
    from .models import Area

    build = await latest_build(sess)
    if build is None:
        raise SourceUnavailable("the map has not been built yet")
    deepest = await sess.scalar(select(func.max(Area.level)).where(Area.build_id == build.build_id))
    rows = list(
        await sess.scalars(
            select(Area).where(Area.build_id == build.build_id, Area.level == deepest)
        )
    )
    if not any(r.examined is not None for r in rows):
        raise SourceUnavailable(
            "the newest map build was not measured for topics; the next build will be"
        )
    now = dt.datetime.now(dt.UTC)
    out: list[Gap] = []
    for r in rows:
        out.extend(
            field_gaps(
                FieldStats(
                    area_id=r.area_id,
                    parent_id=r.parent_id,
                    name=area_name(list(r.terms), r.field),
                    terms=usable_terms(list(r.terms)),
                    passages=r.passages,
                    sources=r.sources,
                    examined=r.examined,
                    on_topic=r.on_topic,
                    topic_mix=dict(r.topic_mix or {}),
                    tier_mix=dict(r.tier_mix),
                    newest_at=r.newest_at,
                ),
                now=now,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Search queries — per query, from what each answered search yielded (`B-56`)
# ---------------------------------------------------------------------------
#
# This replaces P6-36's per-topic "search-yield" source rather than sitting
# beside it. That source could only say "no search for this topic ever queued
# a page", because the queue did not record what a query returned. With the
# yield on the row, the same finding falls out of the per-query source as the
# case where every answered query failed (share 1.0, the top severity), and a
# topic whose searches mostly work but where some words find nothing — which
# the per-topic count could never see — becomes visible too. Keeping both
# would list the all-failed topic twice.
#
# Queries answered before `B-56` have NULL yields. They are *not measured*,
# not failures: counting a NULL as zero is the absent-signal-as-zero trap the
# handover warns about.

#: Failing queries quoted in a gap's reason; the rest are counted, not listed.
QUERY_EXAMPLES = 3

#: How search-found sources are judged off-topic: at least this many examined
#: by the content labeller, and fewer than this share labelled with the topic
#: they were searched for.
OFF_TOPIC_MIN = 5
OFF_TOPIC_SHARE = 0.5

#: Query failure kinds, and how bad each is at its worst (every answered search
#: for the topic failed that way). Nothing found outranks found-only-known:
#: the second at least shows the words reach the topic, the crawl has simply
#: been there. Both stay under an empty topic (1.0) and an operator-graded
#: question (0.7–0.9) — a search that missed is a symptom; those are the gap.
QUERY_KINDS: dict[str, tuple[float, float]] = {
    # kind: (floor, span) — severity = floor + span * failing share
    "search_empty": (0.3, 0.3),
    "search_known": (0.2, 0.2),
}


@dataclasses.dataclass(frozen=True)
class QueryYield:
    task_id: int
    query: str
    results: int | None
    queued: int | None


def query_failure(results: int | None, queued: int | None) -> str | None:
    """Why one answered query is a gap, or None when it is not (or unmeasured)."""
    if results is None or queued is None:
        return None  # answered before its yield was recorded
    if results == 0:
        return "search_empty"
    if queued == 0:
        return "search_known"
    return None


def _quoted(queries: list[str]) -> str:
    shown = ", ".join(f"“{q}”" for q in queries[:QUERY_EXAMPLES])
    rest = len(queries) - QUERY_EXAMPLES
    return shown + (f" and {rest} more" if rest > 0 else "")


def query_gaps(
    topic: str, *, description: str | None, answered: int, failing: list[QueryYield]
) -> list[Gap]:
    """The per-query findings for one topic, grouped by kind. Pure, so testable.

    ``answered`` is every measured answered query for the topic; ``failing`` the
    ones that failed, newest first. One gap per (topic, kind), however many
    queries failed that way: fifty empty searches for one topic are one thing to
    act on, and fifty rows would bury every other source's findings.
    """
    by_kind: dict[str, list[QueryYield]] = {}
    for q in failing:
        kind = query_failure(q.results, q.queued)
        if kind is not None:
            by_kind.setdefault(kind, []).append(q)
    out: list[Gap] = []
    for kind, queries in by_kind.items():
        n = len(queries)
        share = n / answered if answered else 1.0
        floor, span = QUERY_KINDS[kind]
        texts = [q.query for q in queries]
        latest = texts[0]
        if kind == "search_empty":
            title = f"“{latest}” found nothing" if n == 1 else f"{n} searches found nothing"
            outcome = "came back with no results"
            advice = "Other words may reach other pages."
            evidence_kept = {"results": sum(q.results or 0 for q in queries)}
        else:
            title = (
                f"“{latest}” found only what we had"
                if n == 1
                else f"{n} searches found only what we had"
            )
            outcome = "returned only pages the crawl already knew"
            advice = "The words reach ground already covered; ask about what lies next to it."
            evidence_kept = {"results": sum(q.results or 0 for q in queries), "results_queued": 0}
        out.append(
            Gap(
                id=f"{kind.replace('_', '-')}:{topic}",
                source="search-queries",
                kind=kind,
                subject=topic,
                title=title,
                reason=(
                    f"{n} of {answered} answered {'search' if answered == 1 else 'searches'} "
                    f"for {topic} {outcome}: "
                    f"{_quoted(texts)}. {advice}"
                ),
                severity=round(floor + span * min(share, 1.0), 3),
                evidence={
                    "failed": n,
                    "searches_done": answered,
                    **evidence_kept,
                    "queries": "; ".join(texts[:QUERY_EXAMPLES]),
                    "task_ids": ", ".join(str(q.task_id) for q in queries[:QUERY_EXAMPLES]),
                },
                # Rephrase, prefilled with the newest failed words so the
                # operator edits rather than starts from nothing. Submitting
                # them unchanged is refused as already queued, which is right.
                actions=(
                    Action("seed_query", "Rephrase and seed", topic=topic, query=latest),
                    _seed_and_boost(topic, description)[1],
                ),
            )
        )
    return out


async def _live_descriptions(sess: AsyncSession) -> dict[str, str | None]:
    return dict(
        (
            await sess.execute(
                select(TopicConfig.topic, TopicConfig.description).where(
                    TopicConfig.status.in_(LIVE_STATUSES)
                )
            )
        ).all()
    )


@register("search-queries")
async def search_queries(sess: AsyncSession) -> list[Gap]:
    """Answered searches that found nothing, or nothing new, grouped per topic.

    A query with no topic is left out: every action here acts on a topic, and
    a gap with nothing to act on is a complaint, not a finding.
    """
    descriptions = await _live_descriptions(sess)
    if not descriptions:
        return []
    answered = dict(
        (
            await sess.execute(
                select(QueueTask.topic, func.count())
                .where(QueueTask.task_type == "query", QueueTask.status == "done")
                .where(QueueTask.topic.in_(list(descriptions)))
                .where(QueueTask.search_results.is_not(None))
                .where(QueueTask.search_queued.is_not(None))
                .group_by(QueueTask.topic)
            )
        ).all()
    )
    failing: dict[str, list[QueryYield]] = {}
    for task_id, topic, query, results, queued in await sess.execute(
        select(
            QueueTask.task_id,
            QueueTask.topic,
            QueueTask.url_or_query,
            QueueTask.search_results,
            QueueTask.search_queued,
        )
        .where(QueueTask.task_type == "query", QueueTask.status == "done")
        .where(QueueTask.topic.in_(list(descriptions)))
        .where((QueueTask.search_results == 0) | (QueueTask.search_queued == 0))
        .order_by(QueueTask.task_id.desc())
    ):
        failing.setdefault(topic, []).append(QueryYield(task_id, query, results, queued))
    out: list[Gap] = []
    for topic in sorted(failing):
        out.extend(
            query_gaps(
                topic,
                description=descriptions[topic],
                answered=int(answered.get(topic, 0)),
                failing=failing[topic],
            )
        )
    return out


def off_topic_gap(
    topic: str, *, description: str | None, examined: int, on_topic: int
) -> Gap | None:
    """Search results that turned out, by their content, to be about something else.

    Per topic, not per query, and that is the queue's limit, not a choice:
    a result row carries its query's topic but not its query (`B-56` stored the
    counts on the query, not a link from each result back to it). So "these
    words find off-topic pages" is not answerable; "searches for this topic
    do" is.
    """
    if examined < OFF_TOPIC_MIN:
        return None
    share = on_topic / examined
    if share >= OFF_TOPIC_SHARE:
        return None
    return Gap(
        id=f"search-off-topic:{topic}",
        source="search-results",
        kind="search_off_topic",
        subject=topic,
        title=f"{on_topic} of {examined} search results are about {topic}",
        reason=(
            f"Of {_plural(examined, 'page')} that searches for {topic} found and the "
            f"content labeller has read, {on_topic} are labelled {topic}; under "
            f"{OFF_TOPIC_SHARE:.0%} means the words are reaching something else. "
            "Narrower words, or a description for the topic, would steer them."
        ),
        severity=round(0.3 + 0.3 * (1 - share / OFF_TOPIC_SHARE), 3),
        evidence={
            "examined": examined,
            "on_topic": on_topic,
            "on_topic_share": round(share, 3),
        },
        # No boost: boosting a topic whose searches land elsewhere spends more
        # of the crawl landing elsewhere.
        actions=(_seed_and_boost(topic, description)[0],),
    )


@register("search-results")
async def search_results(sess: AsyncSession) -> list[Gap]:
    """Topics whose search-found pages are, by content, mostly about other things.

    Joined through the URL: a source is stored under the URL its queue row
    carried, so a `search`-seeded row finds its source exactly. Only sources
    the labeller has examined count (NULL labels are unread, not off-topic),
    and document copies are left out so one page is not counted twice.
    """
    descriptions = await _live_descriptions(sess)
    if not descriptions:
        return []
    on = Source.topic_labels.contains(array([QueueTask.topic]))
    rows = await sess.execute(
        select(QueueTask.topic, func.count(), func.count().filter(on))
        .select_from(QueueTask)
        .join(Source, Source.url == QueueTask.url_or_query)
        .where(QueueTask.task_type == "url", QueueTask.seed_source == "search")
        .where(QueueTask.topic.in_(list(descriptions)))
        .where(Source.topic_labels.is_not(None), Source.duplicate_of.is_(None))
        .group_by(QueueTask.topic)
        .order_by(QueueTask.topic)
    )
    out = []
    for topic, examined, on_topic in rows:
        gap = off_topic_gap(
            topic, description=descriptions[topic], examined=examined, on_topic=on_topic
        )
        if gap is not None:
            out.append(gap)
    return out


# ---------------------------------------------------------------------------
# Routes — topics the graph does not connect by stated links (`P6-32`)
# ---------------------------------------------------------------------------
#
# Which pairs are worth checking: each topic's most-cited node against every
# other topic's. Most-cited means the most passages behind the edges touching
# it — the node a reader of that topic is likeliest to start from — so a
# missing link between two of those is a missing link between the topics,
# not between two obscure names. One node per topic keeps the pair count at
# topics², and `ROUTE_PAIRS` caps it, because each pair is a graph search and
# this runs on every read of the list.

#: Pairs searched per read, most-cited first.
ROUTE_PAIRS = 10

#: How far to look: the route search's own default.
ROUTE_DEPTH = DEFAULT_ROUTE_DEPTH

#: A route through resemblance only is a weaker finding than no route at all;
#: both stay under a thin topic and an operator-graded question. A search cut
#: short by its work bound says less, and is ranked down by this much.
ROUTE_KINDS: dict[str, float] = {"route_none": 0.5, "route_similar_only": 0.4}
ROUTE_TRUNCATED_PENALTY = 0.15

#: Seed words are capped by `GapSeed.query` (200 characters).
SEED_MAX = 200


@dataclasses.dataclass(frozen=True)
class Anchor:
    topic: str
    entity_id: int
    name: str
    support: int


@dataclasses.dataclass(frozen=True)
class RouteCheck:
    """What the route search said about one pair: the claims-only answer and,
    when that found nothing, the mixed one."""

    cited: bool
    found: bool
    hops: int | None
    similar_hops: int
    truncated: bool
    max_depth: int


def route_pairs(anchors: list[Anchor], limit: int = ROUTE_PAIRS) -> list[tuple[Anchor, Anchor]]:
    """Pairs of different topics' anchors, most-cited first, at most ``limit``.

    One node heading two topics is not a pair: it connects them trivially.
    Ordered by combined support, then by topic, so the capped set is the same
    on every read.
    """
    ordered = sorted(anchors, key=lambda a: a.topic)
    pairs = [
        (a, b)
        for i, a in enumerate(ordered)
        for b in ordered[i + 1 :]
        if a.entity_id != b.entity_id
    ]
    pairs.sort(key=lambda p: (-(p[0].support + p[1].support), p[0].topic, p[1].topic))
    return pairs[:limit]


def _seed_words(a: str, b: str) -> str:
    words = " ".join(f"{a} {b}".split())
    return words[:SEED_MAX].rsplit(" ", 1)[0] if len(words) > SEED_MAX else words


def route_gap(a: Anchor, b: Anchor, check: RouteCheck) -> Gap | None:
    """The finding for one pair, or None when a chain of claims joins them."""
    if check.cited:
        return None
    kind = "route_similar_only" if check.found else "route_none"
    within = f"within {_plural(check.max_depth, 'hop')}"
    ends = f"“{a.name}” (most cited under {a.topic}) and “{b.name}” (most cited under {b.topic})"
    if check.found:
        title = f"“{a.name}” and “{b.name}” connect only by resemblance"
        reason = (
            f"No chain of stated links {within} joins {ends}. The shortest route takes "
            f"{_plural(check.hops or 0, 'hop')}, {check.similar_hops} of them only because "
            "two names read alike."
        )
    else:
        title = f"No route between “{a.name}” and “{b.name}”"
        reason = f"Neither stated links nor resemblance join {ends} {within}."
    if check.truncated:
        reason += " The search stopped at its work bound, so this is weaker than it looks."
    severity = ROUTE_KINDS[kind] - (ROUTE_TRUNCATED_PENALTY if check.truncated else 0.0)
    return Gap(
        id=f"route:{a.topic}:{b.topic}",
        source="routes",
        kind=kind,
        subject=f"{a.topic} · {b.topic}",
        title=title,
        reason=reason + " A search naming both ends may find the passage that links them.",
        severity=round(severity, 3),
        evidence={
            "from_node": a.entity_id,
            "to_node": b.entity_id,
            "max_depth": check.max_depth,
            "hops": check.hops,
            "similar_hops": check.similar_hops if check.found else None,
        },
        actions=(
            Action(
                "seed_query",
                "Seed a search naming both",
                topic=a.topic,
                query=_seed_words(a.name, b.name),
            ),
        ),
    )


async def topic_anchors(sess: AsyncSession, topics: list[str]) -> list[Anchor]:
    """Each topic's most-cited live node (not merged away, not a reader's note).

    A reader's note linking to a node is not the corpus citing it, so edges
    to or from a note do not count toward support either.
    """
    if not topics:
        return []
    rows = await sess.execute(
        text(
            """
            WITH claims AS (
                SELECT ed.from_node, ed.to_node, cardinality(ed.supporting_chunk_ids) AS n
                FROM edges ed
                JOIN entities f ON f.entity_id = ed.from_node
                JOIN entities t ON t.entity_id = ed.to_node
                WHERE ed.from_node <> ed.to_node
                  AND NOT f.is_annotation AND NOT t.is_annotation
            ), ends AS (
                SELECT from_node AS node, n FROM claims
                UNION ALL
                SELECT to_node, n FROM claims
            ), support AS (
                SELECT node, sum(n) AS support FROM ends GROUP BY node HAVING sum(n) > 0
            )
            SELECT DISTINCT ON (t.topic) t.topic, e.entity_id, e.canonical_name, s.support
            FROM entities e
            JOIN support s ON s.node = e.entity_id
            CROSS JOIN LATERAL unnest(e.topic_labels) AS t(topic)
            WHERE e.redirects_to IS NULL
              AND NOT e.is_annotation
              AND t.topic = ANY(CAST(:topics AS text[]))
            ORDER BY t.topic, s.support DESC, e.entity_id
            """
        ),
        {"topics": sorted(topics)},
    )
    return [
        Anchor(topic, entity_id, name, int(support)) for topic, entity_id, name, support in rows
    ]


async def check_route(sess: AsyncSession, a: int, b: int, *, max_depth: int) -> RouteCheck:
    """Claims first; the mixed search only when claims found nothing.

    Most pairs of well-cited nodes are joined by claims, and the claims-only
    search is the cheap one: resemblance asks the vector index at every level.
    """
    ends = (Endpoint(entity_id=a), Endpoint(entity_id=b))
    cited = await find_route(sess, *ends, max_depth=max_depth, allow="cited")
    if cited.found:
        return RouteCheck(True, True, cited.hops, 0, cited.truncated, cited.max_depth)
    mixed = await find_route(sess, *ends, max_depth=max_depth, allow="cited_and_similar")
    return RouteCheck(
        False, mixed.found, mixed.hops, mixed.similar_hops, mixed.truncated, mixed.max_depth
    )


async def route_gaps(
    sess: AsyncSession,
    *,
    topics: list[str] | None = None,
    max_pairs: int = ROUTE_PAIRS,
    max_depth: int = ROUTE_DEPTH,
) -> list[Gap]:
    """Pairs of topics whose most-cited nodes no chain of claims joins."""
    if topics is None:
        topics = list(await _live_descriptions(sess))
    out: list[Gap] = []
    for a, b in route_pairs(await topic_anchors(sess, topics), max_pairs):
        gap = route_gap(
            a, b, await check_route(sess, a.entity_id, b.entity_id, max_depth=max_depth)
        )
        if gap is not None:
            out.append(gap)
    return out


@register("routes")
async def routes(sess: AsyncSession) -> list[Gap]:
    return await route_gaps(sess)


# ---------------------------------------------------------------------------
# Acting on a gap — through the queue and steering, so it is logged and undoable
# ---------------------------------------------------------------------------

#: Where a seed from Gaps sits in the queue: the priority `worker.seedsearch`
#: gives its own queries (`QUERY_PRIORITY`), so a person's seed neither jumps
#: nor trails the crawl's own questions. Mirrored, not imported: the worker is
#: a service and `meridian_core` does not import services.
SEED_PRIORITY = 70

#: The steering-log field a Gaps seed is recorded under.
SEED_FIELD = "seed"  # the same field `POST /api/admin/seeds` logs under (`B-55`)

ACTOR = "user"


class HeldOut(ValueError):
    """A question-set item was offered as the reason to steer the crawl."""


def _check_not_held_out(gap_id: str) -> None:
    # eval/README.md: a question is never a seed or a steering reason. The UI
    # never offers it; this is the server refusing it anyway (§2.6).
    if gap_id.startswith("question:"):
        raise HeldOut(
            "question-set items are held out: they may not seed or steer the crawl "
            "(eval/README.md). Fix for the kind of question instead."
        )


async def _topic(sess: AsyncSession, topic: str) -> TopicConfig:
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    return row


async def seed_query(
    sess: AsyncSession, *, topic: str, query: str, gap_id: str, now: dt.datetime
) -> QueueTask:
    """Queue a search for ``topic`` and log why. Flushes; the caller commits.

    Undo: the task can be removed in Admin while it is still pending
    (`DELETE /api/admin/seeds/{id}`); once it has run, its pages are ordinary
    crawl results and nothing is deleted (§2.5).
    """
    from .queueing import enqueue

    _check_not_held_out(gap_id)
    await _topic(sess, topic)
    words = " ".join(query.split())
    if await sess.scalar(select(QueueTask.task_id).where(QueueTask.url_or_query == words)):
        raise ValueError(f"{words!r} is already queued.")
    task = await enqueue(
        sess, words, topic=topic, seed_source=ACTOR, task_type="query", priority=SEED_PRIORITY
    )
    from . import steering

    await steering.record(
        sess,
        actor=ACTOR,
        topic=topic,
        field=SEED_FIELD,
        old=None,
        new=words,
        reason=f"from Gaps: {gap_id} (queue task {task.task_id})",
        now=now,
    )
    await sess.flush()
    return task


async def boost_topic(
    sess: AsyncSession, *, topic: str, factor: float, days: int, gap_id: str, now: dt.datetime
) -> dt.datetime:
    """A temporary boost through :func:`steering.set_boost`, which logs it."""
    from . import steering

    _check_not_held_out(gap_id)
    row = await _topic(sess, topic)
    if row.status != steering.DRAWING:
        raise ValueError(f"{topic!r} is {row.status}; a boost changes nothing until it is active.")
    expires = now + dt.timedelta(days=days)
    await steering.set_boost(
        sess,
        topic,
        factor=factor,
        expires_at=expires,
        actor=ACTOR,
        reason=f"from Gaps: {gap_id}",
        now=now,
    )
    return expires


# ---------------------------------------------------------------------------
# The question set
# ---------------------------------------------------------------------------


def runs_dir() -> Path:
    from .questionset import runs_dir as configured

    return configured(Path.cwd() / "eval" / "runs")


LEXICAL_NOTE = " The run was lexical-only (no embedder), so an empty result is weak evidence."


def _hits_line(n: int) -> str:
    return "No hits in the top results." if n == 0 else f"{_plural(n, 'hit')} in the top results."


def question_gaps(run: dict, run_name: str) -> list[Gap]:
    """Low items of one run. Operator grades are the score; proposals rank below."""
    from .questionset import SCALE

    out = []
    draft = bool(run.get("draft"))
    # A lexical-only run finds almost nothing for a whole question; a low
    # grade from one says more about the run than about the corpus.
    lexical = (run.get("context") or {}).get("mode") == "lexical-only"
    for item in run.get("items") or []:
        op = item.get("operator")
        if isinstance(op, dict) and op.get("grade") in SCALE:
            grade, basis = int(op["grade"]), "operator"
        else:
            proposed = (item.get("proposed") or {}).get("grade")
            # A heuristic cannot judge a gap item (it cannot tell honest
            # absence from failed search), so its proposal there is not a gap.
            if proposed not in SCALE or item.get("kind") == "gap":
                continue
            grade, basis = int(proposed), "proposal"
        if grade > 1:
            continue
        severity = (0.9 - 0.2 * grade) if basis == "operator" else (0.45 - 0.15 * grade)
        who = "graded" if basis == "operator" else "proposed (heuristic, not a score)"
        missing = (op or {}).get("missing") if basis == "operator" else None
        question = item.get("question")
        out.append(
            Gap(
                id=f"question:{item.get('id')}",
                source="question-set",
                kind="question_low",
                subject=str(item.get("id")),
                title=f"{str(item.get('kind')).capitalize()} question, {grade} of 3, {who}",
                reason=(
                    (missing or _hits_line(len(item.get("hits") or [])))
                    + f" From {run_name}"
                    + (", a draft set." if draft else ".")
                    + (LEXICAL_NOTE if lexical else "")
                    + " Fix for the kind of question, not this question (eval/README.md)."
                ),
                severity=round(severity, 3),
                evidence={
                    "grade": grade,
                    "basis": basis,
                    "kind": item.get("kind"),
                    "topics": ", ".join(item.get("topics") or []),
                    "run": run_name,
                },
                actions=(Action("open_search", "Search it in Find", query=question),)
                if question
                else (),
            )
        )
    return out


@register("question-set")
async def question_set(sess: AsyncSession) -> list[Gap]:
    from .questionset import QuestionSetError, previous_run, read_run

    directory = runs_dir()
    latest = previous_run(directory) if directory.is_dir() else None
    if latest is None:
        raise SourceUnavailable(
            f"no run file in {directory} — run scripts/run_question_set.py, or set "
            "MERIDIAN_EVAL_RUNS_DIR"
        )
    try:
        run = read_run(latest)
    except (QuestionSetError, yaml.YAMLError) as exc:
        raise SourceUnavailable(f"{latest.name} could not be read: {exc}") from exc
    return question_gaps(run, latest.name)
