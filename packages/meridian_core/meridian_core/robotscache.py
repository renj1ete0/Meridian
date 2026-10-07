"""Reading and writing the persisted robots.txt cache (task P1-29).

Knows nothing about parsing. Every function swallows database errors, because a
cache must never stop the crawl: a failed load is a miss, a failed save a fetch
that happens again. See docs/features/crawling.md#robots.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Collection

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import RobotsCacheEntry

log = get_logger(__name__)

#: The three ways a read of robots.txt ends. `missing` permits the origin and
#: `unreachable` refuses it, so the outcome is a column, not `body IS NULL`.
OK = "ok"
MISSING = "missing"
UNREACHABLE = "unreachable"

#: How long an `unreachable` verdict stands before robots.txt is asked again. Here
#: because the queue's retry floor for the refusal must not come back sooner.
ERROR_TTL_S = 600


@dataclasses.dataclass(frozen=True)
class CachedRobots:
    origin: str
    outcome: str
    body: str | None
    fetched_at: dt.datetime
    expires_at: dt.datetime

    def is_fresh(self, *, now: dt.datetime) -> bool:
        return self.expires_at > now


async def load(sess: AsyncSession, origin: str, *, now: dt.datetime) -> CachedRobots | None:
    """The entry for this origin, if there is a fresh one.

    An expired row is left in place, so a read-only session can call this.
    """
    try:
        row = await sess.get(RobotsCacheEntry, origin)
    except SQLAlchemyError as exc:
        log.warning(
            "robots cache unreadable; treating as a miss",
            extra={"origin": origin, "detail": str(exc)},
        )
        return None

    if row is None:
        return None
    entry = CachedRobots(row.origin, row.outcome, row.body, row.fetched_at, row.expires_at)
    return entry if entry.is_fresh(now=now) else None


async def load_many(
    sess: AsyncSession, origins: Collection[str], *, now: dt.datetime
) -> dict[str, CachedRobots]:
    """The fresh entries for these origins, in one query (`B-90`).

    For the prefilter. An origin with no fresh entry is absent. Errors are raised to
    the caller's savepoint, not swallowed, so its transaction can be rolled back.
    """
    if not origins:
        return {}
    rows = await sess.scalars(
        select(RobotsCacheEntry).where(
            RobotsCacheEntry.origin.in_(list(origins)), RobotsCacheEntry.expires_at > now
        )
    )
    return {
        row.origin: CachedRobots(row.origin, row.outcome, row.body, row.fetched_at, row.expires_at)
        for row in rows
    }


async def save(sess: AsyncSession, entry: CachedRobots) -> bool:
    """Write one entry, replacing whatever was there. Commits.

    On its own session, so an entry is not lost when the crawl's page fetch fails.
    """
    try:
        row = await sess.get(RobotsCacheEntry, entry.origin)
        if row is None:
            row = RobotsCacheEntry(origin=entry.origin)
            sess.add(row)
        row.outcome = entry.outcome
        row.body = entry.body
        row.fetched_at = entry.fetched_at
        row.expires_at = entry.expires_at
        await sess.commit()
        return True
    except SQLAlchemyError as exc:
        # A fetch that will simply happen again. Rolled back so the session is
        # usable for whatever the caller does next.
        await sess.rollback()
        log.warning("robots cache not written", extra={"origin": entry.origin, "detail": str(exc)})
        return False


async def purge_expired(sess: AsyncSession, *, now: dt.datetime) -> int:
    """Drop entries that have expired. Returns how many.

    Not scheduled: the table is bounded by distinct origins. For a clean start.
    """
    result = await sess.execute(delete(RobotsCacheEntry).where(RobotsCacheEntry.expires_at <= now))
    await sess.commit()
    return result.rowcount or 0


async def entries(sess: AsyncSession, *, limit: int = 100) -> list[CachedRobots]:
    """Everything cached, newest first. For inspection, not for the crawl."""
    rows = await sess.scalars(
        select(RobotsCacheEntry).order_by(RobotsCacheEntry.fetched_at.desc()).limit(limit)
    )
    return [CachedRobots(r.origin, r.outcome, r.body, r.fetched_at, r.expires_at) for r in rows]
