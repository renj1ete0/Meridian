"""Hybrid retrieval (task P2-06, spec §12.5).

§12.5 asks for one sentence's worth of behaviour — "hybrid search (pgvector +
``tsvector``, fused by reciprocal rank), with filters applied *before* the
vector search" — and three decisions hide in it.

**Filters go inside both arms, not after them.** The anti-pattern is to take the
top 50 by vector distance, drop the ones that fail the filter, and return the
eleven that survive. That is not a filtered search, it is an unfiltered search
with holes in it, and the holes are invisible: asking for twenty government
sources and receiving four reads as a thin corpus rather than as a bug. So the
filter is a predicate in both arm queries, and :func:`_conditions` is the single
place it is expressed — two copies would eventually disagree, and the arm that
drifted would quietly widen the result set.

**Reciprocal rank, not score fusion.** The two arms produce numbers that are not
comparable: ``ts_rank_cd`` is unbounded and depends on document length, cosine
distance is 0..2. Normalising either into the other's range requires knowing the
distribution, which changes as the corpus grows. RRF needs only the ordering,
which is the part both arms agree is meaningful.

**A missing arm is reported, not hidden.** Retrieval with no query vector is
lexical-only — a legitimate degraded mode, since the embedder is a separate
pass (`P2-01`) and a chunk can exist for hours before it has a vector. But a
caller that believes it ran a hybrid search and actually ran half of one will
conclude the wrong thing about the corpus, so :class:`SearchResult` says which
arms ran and why one did not.

**Near-duplicates are excluded by default and counted anyway.** That is what the
novelty gate's verdict is *for* (`P2-03`). ``duplicate_of`` rides on every hit
so a surface can say why something is missing — §12.5's point that a filtered
near-duplicate and a never-crawled page are indistinguishable otherwise, and
only one of them is worth investigating.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, and_, cast, func, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk, Source

log = get_logger(__name__)

#: The text-search configuration, which must match the one `chunks.search_vector`
#: was generated under (`P2-05`). A query parsed under `simple` against a vector
#: built under `english` does not error — it silently stops matching inflected
#: forms, which reads as a corpus that does not discuss the subject.
#:
#: It has to reach Postgres as a `regconfig`, not as text. Bound as a plain
#: string the call is `websearch_to_tsquery(varchar, varchar)`, which does not
#: exist — there is no implicit cast from varchar to regconfig, and the error is
#: an undefined function rather than a type mismatch.
TS_CONFIG = "english"

#: RRF's damping constant. 60 is the value from the original formulation and is
#: not tuned here: it controls how quickly rank 1 stops dominating rank 2, and
#: tuning it against a corpus this small would be fitting to noise.
RRF_K = 60

#: How deep each arm goes before fusion. Larger than any sane `limit` on
#: purpose — fusion can only reorder what the arms handed it, so a candidate
#: pool the size of the result set makes RRF a no-op.
DEFAULT_CANDIDATES = 100

DEFAULT_LIMIT = 20

#: Media types whose extractor produces pages rather than flat text (§6.6).
#:
#: §5.3 defines `page_or_offset` as "page number for paginated documents,
#: character offset otherwise" — a rule every consumer otherwise has to know and
#: apply itself, from a media type that was not on the hit. `P2-18`: derived
#: once, here, so a client can label the number instead of guessing.
PAGINATED_MEDIA_TYPES = frozenset({"application/pdf"})


def page_unit_for(media_type: str | None) -> str | None:
    """What a hit's ``page_or_offset`` counts, or None when it cannot be known.

    None rather than a default. Guessing "offset" mislabels every PDF and
    guessing "page" mislabels every web page, and a source whose media type was
    never recorded is genuinely unknown — saying so is more useful than a
    confident wrong label on a citation someone will try to follow.
    """
    if media_type is None:
        return None
    return "page" if media_type in PAGINATED_MEDIA_TYPES else "offset"


@dataclasses.dataclass(frozen=True)
class SearchFilters:
    """What to search within. Every field narrows; None or empty means no narrowing.

    Deliberately not a free-form mapping. These become SQL predicates, and the
    set of things it is safe to filter on is a decision this module owns rather
    than one each caller makes.

    **There is no topic filter, and its absence is not an oversight.** §12.5 and
    §12.3 both list topic as a filter, and `sources` does not record one — the
    crawl knows the topic (it is on the queue row that produced the fetch) and
    drops it at `upsert_source`. Filtering by topic through a join back to
    `queue` on the URL would be wrong often enough to be worse than not
    offering it: a URL can be enqueued more than once under different topics,
    and a redirect means the fetched URL is frequently not the queued one.
    `P2-14` puts the topic on the source where it belongs.
    """

    source_tiers: Sequence[str] | None = None
    languages: Sequence[str] | None = None
    published_after: dt.date | None = None
    published_before: dt.date | None = None
    #: Near-duplicates are out unless asked for. The gate marked them for a reason.
    include_duplicates: bool = False
    #: §5.4's junk tier is material a sweep will eventually drop; it should not
    #: be answering questions in the meantime.
    include_junk: bool = False


@dataclasses.dataclass(frozen=True)
class SearchHit:
    """One chunk, with the provenance that makes it citable.

    Carrying the source fields rather than an id is the point. A retrieval
    surface that returns text and leaves the caller to look up where it came
    from is a RAG endpoint, and §2 principle 3 is that nothing is assertable
    without a citation you can follow back to a file.
    """

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


@dataclasses.dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    #: Which arms actually ran. A caller that asked for hybrid and got
    #: ``{"lexical"}`` ran half a search, and should know before it concludes
    #: anything about the corpus.
    arms: frozenset[str]
    lexical_candidates: int
    vector_candidates: int

    @property
    def degraded(self) -> bool:
        return self.arms != {"lexical", "vector"}


def _conditions(filters: SearchFilters) -> list[ColumnElement[bool]]:
    """The filter, as predicates. The single source for both arms.

    Both arms must narrow identically or fusion compares two different
    populations — and the arm that drifted would be the one silently returning
    material the caller excluded.
    """
    # Superseded chunks are never searchable, and this is not a filter a caller
    # may turn off (`P1-32`). They are the text a page used to carry: retrieving
    # one would have the corpus quote a document as saying something it no
    # longer says, with a citation that opens the current page and does not
    # contain the passage.
    where: list[ColumnElement[bool]] = [Chunk.superseded_at.is_(None)]

    if filters.source_tiers:
        where.append(Source.source_tier.in_(list(filters.source_tiers)))
    if filters.languages:
        where.append(Source.language.in_(list(filters.languages)))
    if filters.published_after is not None:
        where.append(Source.publication_date >= filters.published_after)
    if filters.published_before is not None:
        where.append(Source.publication_date <= filters.published_before)
    if not filters.include_duplicates:
        where.append(Chunk.duplicate_of.is_(None))
    if not filters.include_junk:
        where.append(Source.retention_tier != "junk")

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

    ``ts_rank_cd`` rather than ``ts_rank``: cover density accounts for how close
    the matched terms are to each other, which is most of what distinguishes a
    passage about the subject from one that mentions every term once.
    """
    tsquery = func.websearch_to_tsquery(cast(TS_CONFIG, REGCONFIG), query)
    stmt = (
        _arm(filters)
        .where(Chunk.search_vector.op("@@")(tsquery))
        .order_by(func.ts_rank_cd(Chunk.search_vector, tsquery).desc(), Chunk.chunk_id)
        .limit(candidates)
    )
    return list((await sess.execute(stmt)).scalars())


async def _vector(
    sess: AsyncSession, vector: Sequence[float], filters: SearchFilters, candidates: int
) -> list[int]:
    """Chunk ids by cosine distance, nearest first.

    The filter is inside this statement rather than applied to its output, which
    is the whole of §12.5's "filters before vector search". Postgres may satisfy
    it by a filtered index scan or by a sequential scan depending on selectivity;
    what it will not do is hand back a top-k drawn from the unfiltered corpus.
    """
    distance = Chunk.embedding.cosine_distance(list(vector))
    stmt = (
        _arm(filters)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance, Chunk.chunk_id)
        .limit(candidates)
    )
    return list((await sess.execute(stmt)).scalars())


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


async def search(
    sess: AsyncSession,
    query: str,
    *,
    query_vector: Sequence[float] | None = None,
    filters: SearchFilters | None = None,
    limit: int = DEFAULT_LIMIT,
    candidates: int = DEFAULT_CANDIDATES,
    k: int = RRF_K,
) -> SearchResult:
    """Hybrid retrieval over the chunk corpus.

    ``query_vector`` is supplied by the caller rather than computed here on
    purpose: `meridian_core` is imported by the API and the orchestrator, and
    neither should acquire a 2.3GB model dependency because a search function
    wanted one. Omitting it is lexical-only, and :attr:`SearchResult.degraded`
    says so.
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

    # Ties broken by chunk_id so a repeated search returns a stable order. Two
    # chunks found at the same rank by one arm and by neither the other is
    # common in a small corpus, and a result set that reshuffles between
    # identical queries is indistinguishable from one that changed.
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))[:limit]

    rows = (
        await sess.execute(
            select(Chunk, Source)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id.in_(ordered))
        )
    ).all()
    by_id = {chunk.chunk_id: (chunk, source) for chunk, source in rows}

    hits = []
    for chunk_id in ordered:
        found = by_id.get(chunk_id)
        if found is None:  # pragma: no cover - deleted between the two queries
            continue
        chunk, source = found
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
                duplicate_of=chunk.duplicate_of,
                media_type=(source.extra or {}).get("media_type"),
                page_unit=page_unit_for((source.extra or {}).get("media_type")),
                score=scores[chunk_id],
                lexical_rank=lexical_at.get(chunk_id),
                vector_rank=vector_at.get(chunk_id),
            )
        )

    return SearchResult(hits, frozenset(arms), len(lexical), len(vectorial))
