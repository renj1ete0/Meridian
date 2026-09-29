"""`/api/explore/graph/*` — the graph workspace's reads (tasks P6-01–P6-03).

Under `/api/explore`, so on the read-only role (§12.6, scaffold §4): the prefix
is the role boundary, and a test asserts nothing under it accepts a write. The
queries themselves live in `meridian_core.graphview`; this module only parses
parameters and turns a missing node into a 404 that names it.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from meridian_core import graphview
from meridian_core.schemas.enums import SourceTier
from meridian_core.schemas.graphview import (
    ContestedListRead,
    GraphFilters,
    GraphNodeDetailRead,
    NeighbourhoodRead,
    NodeSearchRead,
    PathRead,
)

from ..deps import ReadSession

router = APIRouter(prefix="/api/explore/graph", tags=["explore", "graph"])


@router.get("/contested", response_model=ContestedListRead)
async def graph_contested(
    sess: ReadSession,
    limit: Annotated[int, Query(ge=1, le=graphview.MAX_CONTESTED)] = graphview.DEFAULT_CONTESTED,
) -> ContestedListRead:
    """The contested list (§12.5's third entry point): each disagreement once,
    newest first, both sides with their first passage."""
    return await graphview.contested_pairs(sess, limit=limit)


@router.get("/nodes/{entity_id}/neighbourhood", response_model=NeighbourhoodRead)
async def graph_neighbourhood(
    entity_id: int,
    sess: ReadSession,
    topic: Annotated[list[str] | None, Query()] = None,
    tier: Annotated[list[SourceTier] | None, Query()] = None,
    published_from: dt.date | None = None,
    published_to: dt.date | None = None,
    contested_only: bool = False,
    attribute: str | None = None,
    limit: Annotated[int, Query(ge=1, le=graphview.MAX_NEIGHBOURS)] = graphview.DEFAULT_NEIGHBOURS,
) -> NeighbourhoodRead:
    """One node and its depth-1 neighbours, ranked by support and capped (§12.2).

    A reversed date range is refused rather than answered with an empty graph:
    "no neighbours" would read as a fact about the corpus when it is a fact
    about the request.
    """
    if published_from and published_to and published_from > published_to:
        raise HTTPException(
            status_code=422,
            detail=f"published_from {published_from} is after published_to {published_to}.",
        )
    filters = GraphFilters(
        topics=topic or [],
        tiers=tier or [],
        published_from=published_from,
        published_to=published_to,
        contested_only=contested_only,
        attribute=attribute or None,
    )
    try:
        return await graphview.neighbourhood(sess, entity_id, filters, limit=limit)
    except graphview.NodeNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from None


@router.get("/nodes/{entity_id}", response_model=GraphNodeDetailRead)
async def graph_node(entity_id: int, sess: ReadSession) -> GraphNodeDetailRead:
    """The panel beside the canvas: attributes, evidence, contested pairs, notes."""
    try:
        return await graphview.node_detail(sess, entity_id)
    except graphview.NodeNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from None


@router.get("/search", response_model=NodeSearchRead)
async def graph_search(
    sess: ReadSession,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=graphview.MAX_MATCHES)] = graphview.DEFAULT_MATCHES,
) -> NodeSearchRead:
    """Nodes by name or alias, for the workspace's search box."""
    return await graphview.search_nodes(sess, q, limit=limit)


@router.get("/path", response_model=PathRead)
async def graph_path(
    sess: ReadSession,
    source: int,
    target: int,
    max_depth: Annotated[
        int, Query(ge=1, le=graphview.MAX_PATH_DEPTH)
    ] = graphview.DEFAULT_PATH_DEPTH,
) -> PathRead:
    """One shortest route between two nodes (§12.2 path mode, task P6-03)."""
    try:
        return await graphview.shortest_path(sess, source, target, max_depth=max_depth)
    except graphview.NodeNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from None
