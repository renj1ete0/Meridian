"""Paging and honesty over `meridian_core.search` (task P2-07, spec §12.5).

Two things the library deliberately does not do, because they are boundary
concerns rather than retrieval ones.

## The vector arm does not run, and the response says so

`meridian_core.search` takes its query vector from the caller so that importing
it does not drag 2.3GB of model weights into every process that wants to read
the corpus. That leaves the API with a decision, and this module is where it is
made: **this service has no embedder, so search here is lexical-only.**

Three options were on the table.

*Depend on `sentence-transformers`.* Rejected. It makes the API image
gigabytes, adds tens of seconds to cold start, and puts a model in the request
path of a service whose job is answering HTTP in milliseconds. `P2-01` already
established that embedding is a separate pass for exactly these reasons.

*Accept a client-supplied vector.* Rejected, and not only on taste. A 1024-float
array is unwieldy on a GET, and accepting one on an unauthenticated read
endpoint means accepting arbitrary attacker-chosen vectors into a pgvector
distance operator. Nothing needs it: the frontend has no embedder either.

*Call an embedding sidecar over HTTP.* This is the right answer and it is not
built. It is the pattern `crawl4ai` and `searxng` already follow — a service on
`egress`, a client that returns None when its URL is unset, and a word on the
health line. When that exists, :func:`embed_query` is the seam it plugs into
and nothing else in this module changes.

Until then every response carries ``degraded: true`` and a reason. §12.5 asks
for hybrid search; a response that quietly delivered half of one and said
nothing would make the corpus look thinner than it is, and the reader would
conclude something false about the corpus rather than about the API.

## Paging a fused ranking is not paging a table

RRF can only order what the two arms handed it. A hit beyond the candidate pool
was never a candidate, so an offset past the pool is not "later results", it is
a different question nobody asked. :func:`paged_search` therefore refuses a
window that reaches past the pool rather than returning an empty page that looks
like the end of the results.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.logging import get_logger
from meridian_core.schemas.search import SearchHitRead, SearchResponse
from meridian_core.search import DEFAULT_CANDIDATES, SearchFilters, search

log = get_logger(__name__)

#: Why the vector arm did not run. A sentence rather than a code, because it is
#: displayed to a person deciding whether to trust a thin result set.
NO_EMBEDDER = (
    "This API has no embedder, so only the lexical arm ran. Results are matched "
    "on words rather than meaning (P2-07)."
)

#: Why nothing ran at all.
NO_QUERY = "No query text and no vector, so neither arm ran."


class WindowTooDeep(ValueError):
    """A page was requested from beyond the candidate pool.

    Its own type so the route can turn it into a 422 rather than a 500: the
    request is invalid, not the server broken.
    """


def check_window(limit: int, offset: int, candidates: int) -> None:
    """Refuse a page the fused ranking cannot honestly produce.

    RRF orders only what the two arms handed it, so a hit beyond ``candidates``
    was never a candidate. Returning an empty page there would be
    indistinguishable from "you have reached the end of the results", and the
    caller would stop paging believing it had seen everything.

    Refusing is the honest option and the useful one: a client that hits this
    should widen the pool or narrow the query, and both are actions it can only
    take if it is told.
    """
    if offset + limit > candidates:
        raise WindowTooDeep(
            f"offset {offset} + limit {limit} reaches past the candidate pool of "
            f"{candidates}. Fused ranking cannot order hits that were never "
            f"candidates — narrow the query or raise `candidates`."
        )


def _degraded_reason(arms: frozenset[str], has_vector: bool) -> str | None:
    """Which absence to report, or None when both arms ran."""
    if arms == {"lexical", "vector"}:
        return None
    if not arms:
        return NO_QUERY
    return NO_EMBEDDER if not has_vector else None


async def embed_query(query: str) -> Sequence[float] | None:
    """The query vector, or None when this deployment cannot produce one.

    The seam described in the module docstring. Returns None today, and
    returning None is a supported state rather than a failure — the same shape
    as `Crawl4aiClient.from_env()`, where an absent dependency degrades the
    service instead of stopping it.
    """
    return None


async def paged_search(
    sess: AsyncSession,
    query: str,
    *,
    filters: SearchFilters,
    limit: int,
    offset: int,
    candidates: int = DEFAULT_CANDIDATES,
) -> SearchResponse:
    """One page of fused hits, with an account of how they were found.

    ``has_more`` comes from asking for one hit more than the page needs, not
    from a second counting query. The fused ranking has no cheap total, and a
    total computed a different way than the page would eventually disagree with
    the page — at which point the number nobody can reproduce is the one people
    stop trusting.
    """
    check_window(limit, offset, candidates)
    vector = await embed_query(query)

    # One extra, to answer "is there another page" without a second query.
    window = offset + limit + 1
    result = await search(
        sess,
        query,
        query_vector=vector,
        filters=filters,
        limit=window,
        candidates=candidates,
    )

    page = result.hits[offset : offset + limit]
    has_more = len(result.hits) > offset + limit

    log.info(
        "explore search",
        extra={
            "query_chars": len(query),
            "arms": sorted(result.arms),
            "hits": len(page),
            "limit": limit,
            "offset": offset,
            "lexical_candidates": result.lexical_candidates,
            "vector_candidates": result.vector_candidates,
        },
    )

    return SearchResponse(
        hits=[SearchHitRead.model_validate(hit) for hit in page],
        # Sorted: a set's iteration order is not stable, and a field that
        # reshuffles between identical requests breaks caching and diffing.
        arms=sorted(result.arms),
        degraded=result.degraded,
        degraded_reason=_degraded_reason(result.arms, vector is not None),
        limit=limit,
        offset=offset,
        has_more=has_more,
        candidate_pool=candidates,
        lexical_candidates=result.lexical_candidates,
        vector_candidates=result.vector_candidates,
    )
