"""The fetch attempt log (task P1-19, spec §12.5, §13.4).

Every fetch writes one row here, successful or not. That sounds like logging and
is not: `fetch_policy.consecutive_failures` is a counter that resets, and
`queue.error` holds only the most recent message, so between them nothing can
answer "what is the fetch success rate today" or "has this domain been serving
nothing but 404s for a week". §12.5 asks for exactly those, for exactly this
reason — *"without this the Pi can crawl 404s for a week unnoticed"*.

Three functions, one per thing the log is for:

- :func:`record_attempt` writes the row. Called on every path, including the
  refusals that never touched the network — a robots denial or a blocked domain
  is a thing the crawler *did*, and a log that only holds requests that went out
  cannot distinguish a quiet crawler from a stuck one.
- :func:`fetch_health` derives the health line's rate from it.
- :func:`prune_attempts` keeps it bounded. One row per request is high volume by
  design; after a few weeks the aggregate rates are what matter, not the rows.

The caller owns the transaction. Nothing here commits: the attempt row and the
policy consequence it triggers (:func:`meridian_core.policy.apply_fetch_outcome`)
belong to the same commit, because a log saying a domain failed five times and a
policy row that never counted them is worse than either alone.
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

# What counts as the crawler having got what it asked for. A 304 does: the
# conditional request worked and the stored copy is current, which is the
# outcome that feature exists to produce. Counting it as a failure would make
# the success rate fall as caching got *better*.
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

    ``domain`` is the registrable domain of the URL that was *requested*, not of
    the one finally reached. A redirect off-site is still a thing this domain
    did, and attributing it elsewhere would hide the domain that caused it from
    its own per-domain rate.
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

    Deleted in batches so that a prune which has not run for a long time is
    still a series of bounded statements rather than one that locks the table
    for the duration. The transaction is the caller's: this flushes each batch
    and commits none of them.
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
