"""Hybrid retrieval: lexical and vector arms fused by reciprocal rank (task P2-06, §12.5).

Filters are one predicate (`_conditions`) applied inside both arms. A missing arm is
reported on the result, and near-duplicates are excluded by default but counted. See
docs/features/search.md for the design and its reasons.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Float, Select, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from .ageing import age_in_days, decay_factor
from .logging import get_logger
from .models import Chunk, ChunkTopics, Source
from .passagetopics import carries_all_topics, on_topic_passage
from .trust import READABLE_STATES
from .vectorindex import MAX_SCAN_TUPLES, indexed_distance, scan_past_filtered

log = get_logger(__name__)

#: The text-search configuration `chunks.search_vector` was generated under (`P2-05`);
#: bound as a `regconfig`, never text. See docs/features/search.md#text-search-configuration.
TS_CONFIG = "english"

#: RRF's damping constant. 60 is the value from the original formulation and is
#: not tuned here: it controls how quickly rank 1 stops dominating rank 2, and
#: tuning it against a corpus this small would be fitting to noise.
RRF_K = 60

#: `hnsw.ef_search` as a multiple of the candidates asked for. pgvector's default
#: caps the rows an index scan returns; see docs/features/search.md#vector-arm-depth.
EF_SEARCH_FACTOR = 2

#: How deep each arm goes before fusion. Larger than any sane `limit` on
#: purpose — fusion can only reorder what the arms handed it, so a candidate
#: pool the size of the result set makes RRF a no-op.
DEFAULT_CANDIDATES = 100

DEFAULT_LIMIT = 20

#: How many matches RUM's own order hands to ``ts_rank_cd`` (`B-65`). The depth was
#: measured; see docs/features/search.md#lexical-ranking.
LEXICAL_POOL = 1000

#: Media types whose extractor produces pages rather than flat text (§6.6), so a hit's
#: `page_or_offset` is a page (`P2-18`). See docs/features/search.md#citations.
PAGINATED_MEDIA_TYPES = frozenset({"application/pdf"})


def page_unit_for(media_type: str | None) -> str | None:
    """What a hit's ``page_or_offset`` counts, or None when the media type is unknown."""
    if media_type is None:
        return None
    return "page" if media_type in PAGINATED_MEDIA_TYPES else "offset"


@dataclasses.dataclass(frozen=True)
class SearchFilters:
    """What to search within. Every field narrows; None or empty means no narrowing.

    Topics match by overlap (`P2-14`), on the source's labels or the passage's own
    (`P2-24`); a source never examined (NULL labels) is excluded by a topic filter only.
    See docs/features/search.md#filters.
    """

    source_tiers: Sequence[str] | None = None
    languages: Sequence[str] | None = None
    #: Match a source carrying any of these topics (§12.5, §12.3).
    topics: Sequence[str] | None = None
    #: Every one of ``topics`` rather than any (`B-72`): where topics meet.
    #: Counted on a live corpus, several hundred sources carry two or more.
    topics_all: bool = False
    #: Match a source about any of these places (`P2-23`), as `sources.places` codes.
    #: Overlap, like topics; NULL (never examined) is excluded. A country finds its cities.
    places: Sequence[str] | None = None
    published_after: dt.date | None = None
    published_before: dt.date | None = None
    #: Near-duplicates are out unless asked for. The gate marked them for a reason.
    include_duplicates: bool = False
    #: §5.4's junk tier is material a sweep will eventually drop; it should not
    #: be answering questions in the meantime.
    include_junk: bool = False
    #: Only what screening has cleared, not merely "not quarantined" (`P4-14`, §2.5).
    #: Off by default; on for anything feeding a model. See docs/features/search.md#filters.
    cleared_only: bool = False

    #: Weight results by how fast their kind of document ages (`P2-20`, §9). Off by
    #: default; see docs/features/search.md#ageing-and-the-baseline.
    age_aware: bool = False

    #: Per-topic half-lives in days, overriding the per-tier table. `None` as a
    #: value means "does not age", which is how a topic exempts itself.
    half_life_overrides: dict[str, int | None] | None = None


@dataclasses.dataclass(frozen=True)
class SearchHit:
    """One chunk, with the source fields that make it citable (§2, principle 3)."""

    chunk_id: int
    source_id: int
    text: str
    page_or_offset: int | None
    chunk_index: int

    url: str
    title: str | None
    source_tier: str
    publication_date: dt.date | None
    language: str | None
    #: Which topics the source belongs to (`P2-14`), so a reader can check a filter.
    topic_labels: list[str] | None

    #: Which topics this passage itself is about (`P2-24`), best first. None when no
    #: pass has examined the passage, ``[]`` when one has and found none.
    passage_topics: list[str] | None

    #: What ``page_or_offset`` counts: ``page``, ``offset``, or None when the
    #: source's media type was never recorded (`P2-18`). Derived here so no
    #: consumer has to re-implement §5.3's rule.
    page_unit: str | None
    media_type: str | None

    duplicate_of: int | None

    score: float
    #: 1-based position within each arm, or None where that arm did not find it.
    #: Kept because "found by both" and "found by one" are different qualities
    #: of hit, and the fused score alone cannot tell them apart.
    lexical_rank: int | None
    vector_rank: int | None

    #: How old the document is in days, or None when it has no date (`P2-20`).
    #: Carried rather than left to the caller to compute, because the caller
    #: would need today's date to agree with the one the decay used.
    age_days: int | None = None

    #: What the age did to the score. 1.0 is no adjustment, and an undated document
    #: always gets 1.0. Shown so a demotion can be audited.
    decay: float = 1.0

    #: The fused score before decay, so the adjustment can be undone by eye.
    score_before_decay: float = 0.0

    #: Which places the source is about (`P2-23`), for the reason the hit
    #: carries its topics: a place filter a reader cannot see on the result is
    #: one they have to trust rather than check. None means never examined.
    places: list[str] | None = None


@dataclasses.dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    #: Which arms actually ran. A caller that asked for hybrid and got
    #: ``{"lexical"}`` ran half a search, and should know before it concludes
    #: anything about the corpus.
    arms: frozenset[str]
    lexical_candidates: int
    vector_candidates: int
    #: How many hits the per-source cap displaced from this page (`B-30`). Zero when
    #: the cap is off, which is the default.
    held_back: int = 0

    @property
    def degraded(self) -> bool:
        return self.arms != {"lexical", "vector"}


def _conditions(filters: SearchFilters) -> list[ColumnElement[bool]]:
    """The filter, as predicates. The single source for both arms.

    Both arms must narrow identically or fusion compares two different
    populations — and the arm that drifted would be the one silently returning
    material the caller excluded.
    """
    # Superseded chunks are never searchable, whatever the caller asks (`P1-32`).
    where: list[ColumnElement[bool]] = [Chunk.superseded_at.is_(None)]

    if filters.source_tiers:
        where.append(Source.source_tier.in_(list(filters.source_tiers)))
    if filters.languages:
        where.append(Source.language.in_(list(filters.languages)))
    if filters.topics:
        # `&&` is array overlap; `= ANY` and `IN` would filter on something else.
        # The passage arm is an EXISTS by primary key: one probe, no row-multiplying join.
        if filters.topics_all:
            where.append(carries_all_topics(filters.topics))
        else:
            where.append(
                or_(
                    Source.topic_labels.op("&&")(list(filters.topics)),
                    on_topic_passage(filters.topics),
                )
            )
    if filters.places:
        where.append(Source.places.op("&&")([p.strip().upper() for p in filters.places]))
    if filters.published_after is not None:
        where.append(Source.publication_date >= filters.published_after)
    if filters.published_before is not None:
        where.append(Source.publication_date <= filters.published_before)
    if not filters.include_duplicates:
        where.append(Chunk.duplicate_of.is_(None))
        # And the document-level verdict (`B-44`): a source that is a copy of
        # an earlier one — the same page under another URL — answers nothing
        # its canonical source does not.
        where.append(Source.duplicate_of.is_(None))
    if not filters.include_junk:
        where.append(Source.retention_tier != "junk")
    if filters.cleared_only:
        where.append(Source.trust_state.in_(READABLE_STATES))

    return where


def _arm(filters: SearchFilters) -> Select:
    """A chunk-and-source join carrying the filter, ready for an arm's ordering."""
    stmt = select(Chunk.chunk_id).join(Source, Source.source_id == Chunk.source_id)
    conditions = _conditions(filters)
    return stmt.where(and_(*conditions)) if conditions else stmt


async def _lexical(
    sess: AsyncSession, query: str, filters: SearchFilters, candidates: int
) -> list[int]:
    """Chunk ids by lexical relevance, best first.

    The RUM index picks the best :data:`LEXICAL_POOL` matches, and ``ts_rank_cd`` orders
    them (`B-65`). See docs/features/search.md#lexical-ranking.
    """
    tsquery = func.websearch_to_tsquery(cast(TS_CONFIG, REGCONFIG), query)
    pool = (
        _arm(filters)
        .add_columns(Chunk.search_vector)
        .where(Chunk.search_vector.op("@@")(tsquery))
        .order_by(Chunk.search_vector.op("<=>", return_type=Float)(tsquery))
        .limit(max(LEXICAL_POOL, candidates))
        .subquery()
    )
    stmt = (
        select(pool.c.chunk_id)
        .order_by(func.ts_rank_cd(pool.c.search_vector, tsquery).desc(), pool.c.chunk_id)
        .limit(candidates)
    )
    return list((await sess.execute(stmt)).scalars())


async def _vector(
    sess: AsyncSession, vector: Sequence[float], filters: SearchFilters, candidates: int
) -> list[int]:
    """Chunk ids by cosine distance, nearest first, filtered inside the statement (§12.5)."""
    # `set_config` because `SET` takes no bind parameters; local, so it cannot leak to
    # the next caller on a pooled connection.
    await sess.execute(
        select(func.set_config("hnsw.ef_search", str(max(candidates * EF_SEARCH_FACTOR, 40)), True))
    )

    # `B-152`: filters run after the index offers candidates, so a narrow filter used to leave
    # this arm nearly empty. Relaxed order comes back almost sorted; sorted again here.
    await scan_past_filtered(sess, relaxed=True, max_tuples=MAX_SCAN_TUPLES)

    distance = indexed_distance(Chunk.embedding, vector)
    stmt = (
        _arm(filters)
        .add_columns(distance)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance, Chunk.chunk_id)
        .limit(candidates)
    )
    rows = (await sess.execute(stmt)).all()
    return [chunk_id for chunk_id, _ in sorted(rows, key=lambda r: (float(r[1]), r[0]))]


def fuse(*ranked: Sequence[int], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal rank fusion over any number of ranked id lists.

    ``score = Σ 1 / (k + rank)``, rank 1-based. Separate from the queries so it
    is testable without a database — the arithmetic is the part most likely to
    be subtly wrong, and the part a Postgres fixture tells you least about.
    """
    scores: dict[int, float] = {}
    for ids in ranked:
        for position, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
    return scores


def cap_per_source(
    ordered: Sequence[int], source_of: dict[int, int], *, cap: int, limit: int
) -> tuple[list[int], int]:
    """Take the best `limit` chunks, no more than `cap` from any one source.

    Held-back hits are backfilled at the end if the page would otherwise be short.
    Returns the ids and how many the cap displaced. See
    docs/features/search.md#per-source-cap.
    """
    kept: list[int] = []
    seen: dict[int, int] = {}
    overflow: list[int] = []

    for chunk_id in ordered:
        source = source_of.get(chunk_id)
        if source is None or seen.get(source, 0) < cap:
            if source is not None:
                seen[source] = seen.get(source, 0) + 1
            kept.append(chunk_id)
        else:
            overflow.append(chunk_id)
        if len(kept) >= limit:
            break

    displaced = sum(1 for chunk_id in overflow if chunk_id in set(ordered[:limit]))
    if len(kept) < limit:
        kept.extend(overflow[: limit - len(kept)])
    return kept[:limit], displaced


async def search(
    sess: AsyncSession,
    query: str,
    *,
    query_vector: Sequence[float] | None = None,
    filters: SearchFilters | None = None,
    limit: int = DEFAULT_LIMIT,
    candidates: int = DEFAULT_CANDIDATES,
    k: int = RRF_K,
    max_per_source: int | None = None,
) -> SearchResult:
    """Hybrid retrieval over the chunk corpus.

    ``max_per_source`` caps how many chunks one document may contribute; off by
    default (`B-30`). ``query_vector`` is the caller's to compute; omitting it is
    lexical-only, and :attr:`SearchResult.degraded` says so. See
    docs/features/search.md#ageing-and-the-baseline.
    """
    filters = filters or SearchFilters()

    query = query.strip()
    if not query and query_vector is None:
        # Not an error. An empty search box is a state a UI has, and raising
        # would make the caller special-case its own first render.
        return SearchResult([], frozenset(), 0, 0)

    arms: set[str] = set()
    lexical: list[int] = []
    vectorial: list[int] = []

    if query:
        lexical = await _lexical(sess, query, filters, candidates)
        arms.add("lexical")
    if query_vector is not None:
        vectorial = await _vector(sess, query_vector, filters, candidates)
        arms.add("vector")

    scores = fuse(lexical, vectorial, k=k)
    if not scores:
        return SearchResult([], frozenset(arms), len(lexical), len(vectorial))

    lexical_at = {chunk_id: i for i, chunk_id in enumerate(lexical, start=1)}
    vector_at = {chunk_id: i for i, chunk_id in enumerate(vectorial, start=1)}

    # Ties broken by chunk_id so a repeated search returns a stable order.
    ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))
    held_back = 0
    if max_per_source is None:
        ordered = ranked[:limit]
    else:
        # One indexed lookup over the fused candidates, because the cap needs
        # each candidate's source *before* the page is cut — the row join below
        # happens after, and by then the decision is made.
        source_of = dict(
            (
                await sess.execute(
                    select(Chunk.chunk_id, Chunk.source_id).where(Chunk.chunk_id.in_(ranked))
                )
            ).all()
        )
        ordered, held_back = cap_per_source(ranked, source_of, cap=max_per_source, limit=limit)

    rows = (
        await sess.execute(
            select(Chunk, Source, ChunkTopics.topic_labels)
            .join(Source, Source.source_id == Chunk.source_id)
            .outerjoin(ChunkTopics, ChunkTopics.chunk_id == Chunk.chunk_id)
            .where(Chunk.chunk_id.in_(ordered))
        )
    ).all()
    by_id = {chunk.chunk_id: (chunk, source, passage) for chunk, source, passage in rows}

    hits = []
    for chunk_id in ordered:
        found = by_id.get(chunk_id)
        if found is None:  # pragma: no cover - deleted between the two queries
            continue
        chunk, source, passage = found
        hits.append(
            SearchHit(
                chunk_id=chunk.chunk_id,
                source_id=chunk.source_id,
                text=chunk.text,
                page_or_offset=chunk.page_or_offset,
                chunk_index=chunk.chunk_index,
                url=source.url,
                title=source.title,
                source_tier=source.source_tier,
                publication_date=source.publication_date,
                language=source.language,
                topic_labels=source.topic_labels,
                passage_topics=list(passage) if passage is not None else None,
                places=source.places,
                duplicate_of=chunk.duplicate_of,
                media_type=(source.extra or {}).get("media_type"),
                page_unit=page_unit_for((source.extra or {}).get("media_type")),
                score=scores[chunk_id] * _decay_for(source, filters),
                age_days=age_in_days(source.publication_date),
                decay=_decay_for(source, filters),
                score_before_decay=scores[chunk_id],
                lexical_rank=lexical_at.get(chunk_id),
                vector_rank=vector_at.get(chunk_id),
            )
        )

    # Re-sorted, because the decay is applied after fusion and a hit that
    # dropped below the one under it would otherwise be listed above it — a
    # result list whose order disagrees with its own scores.
    hits.sort(key=lambda hit: hit.score, reverse=True)

    return SearchResult(hits, frozenset(arms), len(lexical), len(vectorial), held_back=held_back)


def _decay_for(source: Source, filters: SearchFilters) -> float:
    """The age adjustment for one hit, or 1.0 when ageing is off (the default)."""
    if not filters.age_aware:
        return 1.0
    return decay_factor(
        source.publication_date,
        source.source_tier,
        topics=source.topic_labels,
        overrides=filters.half_life_overrides,
    )
