"""Reading and writing the persisted robots.txt cache (task P1-29).

Deliberately knows nothing about parsing. It moves three fields and two
timestamps, so that the worker owns what a robots.txt *means* and this owns
where it is kept — and the parser can change without a migration.

**Every function here swallows database errors.** A cache is an optimisation,
and an optimisation that can stop the crawl is worse than no cache: the failure
mode of a strict version is a Postgres hiccup turning into "this worker refuses
every origin", which is monitoring causing the outage it was meant to shorten.
A failed load is a miss, and a failed save is a fetch that will happen again.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import RobotsCacheEntry

log = get_logger(__name__)

#: The three ways a read of robots.txt ends. `missing` and `unreachable` both
#: store no body and mean opposite things — the first permits the origin, the
#: second refuses it until the file can be read — which is why the outcome is a
#: column rather than inferred from `body IS NULL`.
OK = "ok"
MISSING = "missing"
UNREACHABLE = "unreachable"


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

    An expired row is left where it is rather than deleted. Deleting on read
    would make this function write, which means a read-only session cannot call
    it and a crawl cannot warm its cache from the read replica it will one day
    have — and the row is about to be overwritten by the fetch that follows.
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


async def save(sess: AsyncSession, entry: CachedRobots) -> bool:
    """Write one entry, replacing whatever was there. Commits.

    Commits on purpose, and it is the reason this takes a session rather than
    joining the caller's transaction: the caller is a crawl loop whose
    transaction spans a page fetch, and a robots entry held unwritten until that
    commits is lost every time the page fails.
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
        log.warning(
            "robots cache not written", extra={"origin": entry.origin, "detail": str(exc)}
        )
        return False


async def purge_expired(sess: AsyncSession, *, now: dt.datetime) -> int:
    """Drop entries that have expired. Returns how many.

    Not scheduled anywhere, and not needed for size: rows are keyed by origin
    and overwritten in place, so the table is bounded by the number of distinct
    origins the crawl has ever touched — the same order as `fetch_policy`. It
    exists for the other reason to want it, which is starting a crawl from a
    clean slate without dropping the table.
    """
    result = await sess.execute(
        delete(RobotsCacheEntry).where(RobotsCacheEntry.expires_at <= now)
    )
    await sess.commit()
    return result.rowcount or 0


async def entries(sess: AsyncSession, *, limit: int = 100) -> list[CachedRobots]:
    """Everything cached, newest first. For inspection, not for the crawl."""
    rows = await sess.scalars(
        select(RobotsCacheEntry).order_by(RobotsCacheEntry.fetched_at.desc()).limit(limit)
    )
    return [CachedRobots(r.origin, r.outcome, r.body, r.fetched_at, r.expires_at) for r in rows]
