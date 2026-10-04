"""Paging and degraded-mode reporting over `meridian_core.search` (task P2-07, §12.5).

The query vector comes from the embedding service (`P2-17`); without one, search is
lexical-only and every response says so. A page past the candidate pool is refused rather
than returned empty. See docs/features/search.md.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.answer import (
    COVERAGE_RULE,
    PRIMARY_TIERS,
    STRONG_MIN_PUBLISHERS,
    group_hits,
    leading_topics,
)
from meridian_core.embedder import EmbeddingUnavailable, RemoteEmbedder
from meridian_core.logging import get_logger
from meridian_core.schemas.answer import AnswerGroupRead, AnswerRead
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

#: Configured and not answering. Deliberately different wording from
#: `NO_EMBEDDER`, because the two are different facts and the reader acts on
#: them differently: "this deployment has no embedder" is a choice somebody
#: made, and "the embedder is down" is an outage somebody should fix. Reporting
#: an outage as a deployment choice is how a broken dependency goes unnoticed
#: for a week — and it is the exact mistake this field exists to prevent one
#: level up, where an empty result set is not allowed to look like an empty
#: corpus.
EMBEDDER_DOWN = (
    "The embedding service is configured but did not answer, so only the "
    "lexical arm ran. Results are matched on words rather than meaning. This is "
    "an outage, not a limitation of this deployment."
)


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


def _degraded_reason(
    arms: frozenset[str], has_vector: bool, *, embedder_configured: bool
) -> str | None:
    """Which absence to report, or None when both arms ran."""
    if arms == {"lexical", "vector"}:
        return None
    if not arms:
        return NO_QUERY
    if has_vector:
        return None
    # Absent and broken are not the same answer. See `EMBEDDER_DOWN`.
    return EMBEDDER_DOWN if embedder_configured else NO_EMBEDDER


async def embed_query(query: str) -> Sequence[float] | None:
    """The query vector, or None when this deployment cannot produce one.

    `P2-17` fills the seam: the vector comes from the embedding sidecar, which
    runs the same model the corpus was embedded with. That sameness is the whole
    requirement — a vector from a different model is not merely less accurate
    against this corpus, it is meaningless, and comparing it computes without
    erroring and ranks the result confidently.

    None stays a supported state rather than becoming a failure. No sidecar
    configured means lexical-only, reported through `degraded`; a sidecar that
    is configured and *down* also returns None here, and the difference is in
    the log rather than in a 500 — a search that still answers on one arm is
    better for the caller than an error page, provided it says so.
    """
    embedder = RemoteEmbedder.from_env()
    if embedder is None:
        return None

    try:
        return await embedder.embed_one(query)
    except EmbeddingUnavailable as exc:
        # Configured and not answering is an outage, and it is logged as one.
        # Reporting it to the caller as "no embedder" would hide a broken
        # dependency behind what looks like a deployment choice.
        log.warning("embedder unavailable; falling back to lexical", extra={"reason": str(exc)})
        return None
    finally:
        await embedder.aclose()


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
    # Asked separately from the vector, because "no vector" has two causes and
    # the caller is told which.
    embedder_configured = RemoteEmbedder.from_env() is not None

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
        degraded_reason=_degraded_reason(
            result.arms, vector is not None, embedder_configured=embedder_configured
        ),
        limit=limit,
        offset=offset,
        has_more=has_more,
        candidate_pool=candidates,
        lexical_candidates=result.lexical_candidates,
        vector_candidates=result.vector_candidates,
    )


async def answer_search(
    sess: AsyncSession,
    query: str,
    *,
    filters: SearchFilters,
    candidates: int,
    top: int,
) -> AnswerRead:
    """The whole candidate pool, grouped by place (see `meridian_core.answer`).

    One search, not one per country: the grouping reads `places` off the hits
    the fused ranking already produced, so the answer and the passage list are
    the same evidence arranged two ways. The ranking itself is unchanged.
    """
    vector = await embed_query(query)
    embedder_configured = RemoteEmbedder.from_env() is not None
    # Fusion yields at most one entry per candidate from each arm, so this
    # limit takes the whole pool.
    result = await search(
        sess,
        query,
        query_vector=vector,
        filters=filters,
        limit=2 * candidates,
        candidates=candidates,
    )
    groups, unplaced = group_hits(result.hits, top=top)
    log.info(
        "explore answer",
        extra={
            "query_chars": len(query),
            "arms": sorted(result.arms),
            "hits": len(result.hits),
            "groups": len(groups),
        },
    )
    return AnswerRead(
        query=query,
        groups=[AnswerGroupRead.model_validate(group) for group in groups],
        unplaced=AnswerGroupRead.model_validate(unplaced) if unplaced else None,
        coverage_rule=COVERAGE_RULE,
        strong_min_publishers=STRONG_MIN_PUBLISHERS,
        strong_needs_tiers=sorted(PRIMARY_TIERS),
        topics=leading_topics(result.hits),
        passages_considered=len(result.hits),
        sources_considered=len({hit.source_id for hit in result.hits}),
        candidate_pool=candidates,
        arms=sorted(result.arms),
        degraded=result.degraded,
        degraded_reason=_degraded_reason(
            result.arms, vector is not None, embedder_configured=embedder_configured
        ),
    )
