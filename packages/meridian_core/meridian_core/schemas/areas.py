"""Areas on the wire (task P6-30). Mirrors ``meridian_core.areaview``."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
