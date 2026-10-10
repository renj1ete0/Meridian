"""Gaps (task P6-36): the ranked list on Explore, the actions on Admin.

The list is read-only; the seed and boost actions go through the queue and
:mod:`meridian_core.steering` and each writes a `steering_log` row (§10.1).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException

from meridian_core import gaps
from meridian_core.db import session_ro
from meridian_core.logging import get_logger
from meridian_core.schemas.gaps import (
    GapActionResult,
    GapBoost,
    GapRead,
    GapSeed,
    GapSourceRead,
    GapsRead,
)
from meridian_core.timefmt import display_zone, format_instant

from ..cache import Kept
from ..deps import AdminAllowed, WriteSession

log = get_logger(__name__)

explore_router = APIRouter(prefix="/api/explore", tags=["explore"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


#: How long a computed gap list is served before a background refresh (`B-121`).
#: Gaps move with the crawl, over hours; computing them counts every on-topic
#: passage and took several seconds on each visit.
GAPS_TTL_S = 900

KEPT_GAPS: Kept[GapsRead] = Kept(GAPS_TTL_S)


async def compute_gaps() -> GapsRead:
    """The gap list on its own read-only session, so it can refresh in the background."""
    async with session_ro() as sess:
        found, statuses = await gaps.find_gaps(sess)
    return GapsRead(
        gaps=[GapRead.model_validate(g) for g in found],
        sources=[GapSourceRead.model_validate(s) for s in statuses],
        computed_at=_now(),
    )


@explore_router.get("/gaps", response_model=GapsRead)
async def list_gaps() -> GapsRead:
    """Every gap the registered sources find, most severe first.

    Kept for :data:`GAPS_TTL_S` and refreshed behind the reader; ``computed_at``
    says when this list was worked out.
    """
    return await KEPT_GAPS.get(compute_gaps)


def _refuse(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError) and "already queued" in str(exc):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@admin_router.post("/gaps/seed", response_model=GapActionResult, status_code=201)
async def seed_from_gap(body: GapSeed, _: AdminAllowed, sess: WriteSession) -> GapActionResult:
    try:
        task = await gaps.seed_query(
            sess, topic=body.topic, query=body.query, gap_id=body.gap_id, now=_now()
        )
    except (LookupError, ValueError) as exc:
        await sess.rollback()
        raise _refuse(exc) from exc
    await sess.commit()
    log.info("gap seeded", extra={"gap": body.gap_id, "topic": body.topic, "task_id": task.task_id})
    return GapActionResult(
        kind="seed_query",
        topic=body.topic,
        task_id=task.task_id,
        detail=f"queued as a search seed · task {task.task_id}",
        undo="remove it in Admin › Seeds while it is still pending",
    )


@admin_router.post("/gaps/boost", response_model=GapActionResult)
async def boost_from_gap(body: GapBoost, _: AdminAllowed, sess: WriteSession) -> GapActionResult:
    from meridian_core import steering

    try:
        expires = await gaps.boost_topic(
            sess,
            topic=body.topic,
            factor=body.factor,
            days=body.days,
            gap_id=body.gap_id,
            now=_now(),
        )
    except (LookupError, ValueError, steering.InfeasibleWeights) as exc:
        await sess.rollback()
        raise _refuse(exc) from exc
    zone = await display_zone(sess)
    await sess.commit()
    log.info("gap boosted", extra={"gap": body.gap_id, "topic": body.topic})
    return GapActionResult(
        kind="boost_topic",
        topic=body.topic,
        expires_at=expires,
        detail=f"boost ×{body.factor:g} until {format_instant(expires, zone)}",
        undo="expires by itself; clear it sooner in Admin › Pins & boosts",
    )
