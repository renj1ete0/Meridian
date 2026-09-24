"""Areas on the wire (task P6-30). Mirrors ``meridian_core.areaview``."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AreaBuildRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    build_id: int
    computed_at: dt.datetime
    passages: int
    regions: int
    areas: int
    leaves: int


class AreaRead(BaseModel):
    """One cluster of passages.

    ``name`` is its three most distinctive terms joined — a description of
    what sets its passages apart, not a topic somebody chose. ``weak`` and
    ``stale`` are measurements with their thresholds in ``reasons``, not
    verdicts about the material.
    """

    model_config = ConfigDict(from_attributes=True)

    area_id: int
    level: int
    parent_id: int | None
    name: str
    terms: list[str]
    passages: int
    sources: int
    tier_mix: dict[str, int]
    newest_at: dt.datetime | None
    x: float
    y: float
    children: int
    weak: bool
    stale: bool
    reasons: list[str]


class AreaCrumb(BaseModel):
    area_id: int
    level: int
    name: str


class AreasRead(BaseModel):
    """One level of the map: the children of ``parent``, or the regions.

    ``build`` is None when no build exists yet; ``passages_needed`` then says
    how many searchable passages a first build waits for.
    """

    build: AreaBuildRead | None
    level: int
    parent: AreaRead | None
    path: list[AreaCrumb]
    areas: list[AreaRead]
    #: Bridges among ``areas`` (P6-31).
    links: list[AreaLinkRead]
    levels: int
    passages_needed: int
    stale_after_days: int
    weak_below_sources: int


class AreaPassage(BaseModel):
    chunk_id: int
    source_id: int
    title: str | None
    url: str
    source_tier: str
    snippet: str


class AreaDetailRead(BaseModel):
    area: AreaRead
    path: list[AreaCrumb]
    #: The passages nearest the area's centre: what it is most typically about.
    passages: list[AreaPassage]


class AreaJumpHit(BaseModel):
    area: AreaRead
    path: list[AreaCrumb]
    #: ``name``: the query is one of the area's distinctive terms.
    #: ``passages``: passages matching the query sit in this area (``hits``).
    match: Literal["name", "passages"]
    hits: int


class AreaJumpRead(BaseModel):
    query: str
    hits: list[AreaJumpHit]


class AreaLinkRead(BaseModel):
    """A bridge as a line on the map (task P6-31): counts only, kinds apart.

    ``cited_claims`` are edges a source states whose evidence spans the two
    areas — the only kind that says the two connect. ``similar_pairs`` counts
    passage pairs that are merely near in meaning. The map draws them
    differently and never adds one to the other.
    """

    area_a: int
    area_b: int
    cited_claims: int
    cited_sources: int
    similar_pairs: int
    similarity: float
    shared_terms: list[str]


class BridgeClaimRead(BaseModel):
    edge_id: int
    from_node: int
    from_name: str
    relation_type: str
    to_node: int
    to_name: str
    #: Distinct sources behind the edge's own passages.
    sources: int
    citations: int
    tiers: list[str]


class BridgePassageRead(BaseModel):
    chunk_id: int
    source_id: int
    title: str | None
    source_tier: str
    snippet: str


class BridgePairRead(BaseModel):
    #: Cosine between the two passages' vectors.
    score: float
    a: BridgePassageRead
    b: BridgePassageRead


class BridgeRead(BaseModel):
    """What connects two areas, in the three kinds, never merged.

    Empty lists are an answer: two areas with nothing recorded between them
    come back with no claims and no pairs rather than a 404.
    """

    a: AreaCrumb
    b: AreaCrumb
    claims: list[BridgeClaimRead]
    cited_sources: int
    similar: list[BridgePairRead]
    shared_terms: list[str]
    similarity: float


# ---------------------------------------------------------------------------
# Steering from the map (task P6-35)


class AreaTopicShare(BaseModel):
    #: A source's primary topic, or None for passages about none of them.
    topic: str | None
    passages: int


class AreaSteeringRead(BaseModel):
    """What steering this area would move, before anybody moves it.

    ``topic`` is the topic that holds at least ``dominant_share`` of the
    area's passages, or None: an area about no topic has no weight to turn.
    """

    area_id: int
    topic: str | None
    dominant_share: float
    topics: list[AreaTopicShare]
    more_factor: float
    less_factor: float
    boost_days: int
    search: str


class MapSteerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["more", "less", "watch"]


class MapSuggestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    topic: str | None = None


class MapSteerRead(BaseModel):
    """What a steer did, where to undo it, and the rows it wrote."""

    model_config = ConfigDict(from_attributes=True)

    action: str
    area_id: int | None
    topic: str | None
    boost_factor: float | None
    boost_expires_at: dt.datetime | None
    seed_task_ids: list[int]
    view_id: int | None
    message: str
    undo: str
