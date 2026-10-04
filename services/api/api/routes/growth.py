"""`/api/explore/growth`: how the corpus grew (task `B-140`, ADRs 0005 and 0010).

On the read-only role. Kept per window and topic filter for a few minutes and refreshed behind
the reader, since growth moves over hours and counting passages per topic takes seconds on a
large corpus. See docs/features/growth.md.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from meridian_core.db import session_ro
from meridian_core.growth import growth
from meridian_core.schemas.growth import GrowthRead
from meridian_core.timefmt import display_zone

from ..cache import KeptByKey

router = APIRouter(prefix="/api/explore", tags=["explore"])

Range = Literal["7d", "30d", "all"]
RANGES: dict[str, int | None] = {"7d": 7, "30d": 30, "all": None}

#: Seconds an answer is served before a background refresh.
GROWTH_TTL_S = 300.0

KEPT_GROWTH: KeptByKey[tuple[str, tuple[str, ...]], GrowthRead] = KeptByKey(GROWTH_TTL_S)


async def compute_growth(window: Range, topics: tuple[str, ...]) -> GrowthRead:
    async with session_ro() as sess:
        zone = await display_zone(sess)
        return await growth(sess, zone=zone, days=RANGES[window], topics=list(topics) or None)


@router.get("/growth", response_model=GrowthRead)
async def explore_growth(
    range: Range = "30d",  # noqa: A002 - the URL's word for it
    topic: Annotated[list[str] | None, Query(max_length=50)] = None,
) -> GrowthRead:
    """Pages, passages, sites, the graph and the map, day by day in the display zone."""
    topics = tuple(sorted({t for t in topic or [] if t.strip()}))
    return await KEPT_GROWTH.get((range, topics), lambda: compute_growth(range, topics))
