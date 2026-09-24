"""`/api/explore/route` — a route across claims and resemblance (task P6-32).

Under `/api/explore`, so on the read-only role (§12.6). The search lives in
`meridian_core.route`; this module parses the two ends, hands the search the
embedding sidecar for ends that name no node, and turns a missing node into a
404 that names it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from meridian_core import graphview
from meridian_core import route as routing
from meridian_core.schemas.route import Allow, RouteRead

from ..deps import ReadSession
from ..search_service import embed_query

router = APIRouter(prefix="/api/explore", tags=["explore", "graph"])


def _end(entity_id: int | None, term: str | None, which: str) -> routing.Endpoint:
    if entity_id is not None:
        return routing.Endpoint(entity_id=entity_id)
    if term and term.strip():
        return routing.Endpoint(term=term.strip())
    raise HTTPException(
        status_code=422, detail=f"Give the {which} as a node ({which}) or a term ({which}_q)."
    )


@router.get("/route", response_model=RouteRead)
async def find_route(
    sess: ReadSession,
    source: int | None = None,
    target: int | None = None,
    source_q: Annotated[str | None, Query(max_length=200)] = None,
    target_q: Annotated[str | None, Query(max_length=200)] = None,
    max_depth: Annotated[
        int, Query(ge=1, le=routing.MAX_ROUTE_DEPTH)
    ] = routing.DEFAULT_ROUTE_DEPTH,
    allow: Allow = "cited_and_similar",
) -> RouteRead:
    """The best route between two ends, every hop labelled cited or similar.

    The claims-only answer is always in `cited_only`, so "no cited route
    within N hops" is part of every response rather than a second request.
    """
    a = _end(source, source_q, "source")
    b = _end(target, target_q, "target")
    try:
        return await routing.route(sess, a, b, max_depth=max_depth, allow=allow, embed=embed_query)
    except graphview.NodeNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from None
