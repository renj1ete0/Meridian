"""`/api/explore/neighbourhood` — a term's two rings (task P6-33).

Under `/api/explore`, so on the read-only role (§12.6). The query lives in
`meridian_core.neighbourhood`; this module parses parameters, hands it the
embedding sidecar, and turns a missing node into a 404 that names it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from meridian_core import graphview, neighbourhood
from meridian_core.schemas.neighbourhood import TermNeighbourhoodRead

from ..deps import ReadSession
from ..search_service import embed_query

router = APIRouter(prefix="/api/explore", tags=["explore", "graph"])


@router.get("/neighbourhood", response_model=TermNeighbourhoodRead)
async def term_neighbourhood(
    sess: ReadSession,
    q: Annotated[str, Query(max_length=200)] = "",
    entity_id: int | None = None,
) -> TermNeighbourhoodRead:
    """Inner ring: what passages state a link to. Outer ring: what reads alike.

    A term, or a node the reader already picked (`entity_id` wins when both
    are given). Neither is a request with nothing to answer, and is refused
    rather than answered with two empty rings that would read as a finding.
    """
    if entity_id is None and not q.strip():
        raise HTTPException(status_code=422, detail="Give a term (q) or a node (entity_id).")
    try:
        return await neighbourhood.neighbourhood(
            sess, q.strip(), entity_id=entity_id, embed=embed_query
        )
    except graphview.NodeNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from None
