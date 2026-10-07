"""The fetch attempt log (task P1-19, spec §12.5, §13.4).

One row per fetch, refusals that never touched the network included.
:func:`record_attempt` writes it, :func:`fetch_health` derives the health line's
rate, :func:`prune_attempts` bounds it. The caller owns the transaction, so the row
commits with its policy consequence. See docs/features/crawling.md#the-attempt-log.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FetchAttempt
from .schemas.queue import FetchHealth
from .tiering import registrable_domain

# `error_detail` is Text and takes whatever it is given, which is how a
# multi-megabyte upstream error message ends up in the row it was meant to
# describe. `queue.error` truncates at the same width for the same reason.
MAX_DETAIL_CHARS = 2000

# Thirty days of rows answers every question the health line asks — the daily
# rate, the week-long drift, the month-over-month trend — and a Pi has no use
# for the individual requests behind them after that.
DEFAULT_RETENTION_DAYS = 30

# Rows deleted per statement. A prune that has been skipped for a month must not
# build one DELETE over a million rows.
PRUNE_BATCH = 5_000

# What counts as the crawler having got what it asked for. A 304 does, or the
# success rate would fall as caching got better.
SUCCESS_OUTCOMES = frozenset({"success", "not_modified"})


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def record_attempt(
    sess: AsyncSession,
    *,
    domain: str,
    url: str,
    outcome: str,
    status_code: int | None = None,
    error_detail: str | None = None,
    duration_ms: int | None = None,
    bytes_fetched: int | None = None,
    task_id: int | None = None,
    attempt_number: int = 1,
    attempted_at: dt.datetime | None = None,
) -> FetchAttempt:
    """Write one ``fetch_attempts`` row. Flushes; does not commit.

    ``domain`` is the registrable domain of the URL *requested*, not the one finally
    reached, so an off-site redirect is charged to the domain that caused it.
    """
    row = FetchAttempt(
        task_id=task_id,
        domain=registrable_domain(domain),
        url=url,
        attempted_at=attempted_at or _now(),
        outcome=outcome,
        status_code=status_code,
        error_detail=error_detail[:MAX_DETAIL_CHARS] if error_detail else None,
        duration_ms=duration_ms,
        bytes_fetched=bytes_fetched,
        attempt_number=attempt_number,
    )
    sess.add(row)
    await sess.flush()
    return row


async def fetch_health(
    sess: AsyncSession,
    *,
    hours: int = 24,
    domain: str | None = None,
    now: dt.datetime | None = None,
) -> FetchHealth:
    """The fetch half of §12.5's daily health line.

    Counts by outcome rather than only a rate, because the rate alone says
    something is wrong and never what: a run that is 40% ``robots_denied`` needs
    the frontier looked at, and one that is 40% ``timeout`` needs the network.
    """
    if hours <= 0:
        raise ValueError("hours must be positive")

    cutoff = (now or _now()) - dt.timedelta(hours=hours)
    query = (
        select(FetchAttempt.outcome, func.count())
        .where(FetchAttempt.attempted_at >= cutoff)
        .group_by(FetchAttempt.outcome)
    )
    if domain is not None:
        query = query.where(FetchAttempt.domain == registrable_domain(domain))

    by_outcome = {outcome: count for outcome, count in (await sess.execute(query)).all()}
    attempts = sum(by_outcome.values())
    successes = sum(count for o, count in by_outcome.items() if o in SUCCESS_OUTCOMES)

    return FetchHealth(
        window_hours=hours,
        domain=registrable_domain(domain) if domain else None,
        attempts=attempts,
        successes=successes,
        # None, not 0.0, when nothing was attempted. A crawler that fetched
        # nothing and a crawler where everything failed need different
        # responses, and 0% would report the first as the second.
        success_rate=(successes / attempts) if attempts else None,
        by_outcome=by_outcome,
    )


async def prune_attempts(
    sess: AsyncSession,
    *,
    older_than_days: int = DEFAULT_RETENTION_DAYS,
    batch_size: int = PRUNE_BATCH,
    now: dt.datetime | None = None,
) -> int:
    """Delete attempts older than the retention window. Returns how many.

    Deleted in bounded batches; flushes each and commits none.
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must not be negative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    cutoff = (now or _now()) - dt.timedelta(days=older_than_days)
    total = 0
    while True:
        doomed = (
            select(FetchAttempt.attempt_id)
            .where(FetchAttempt.attempted_at < cutoff)
            .limit(batch_size)
            .scalar_subquery()
        )
        result = await sess.execute(delete(FetchAttempt).where(FetchAttempt.attempt_id.in_(doomed)))
        await sess.flush()
        deleted = result.rowcount or 0
        total += deleted
        if deleted < batch_size:
            return total
