"""Claiming and settling scheduled jobs (task P5-06, spec §13.1).

The queue's shape (`P1-01`), for the same reasons and with the same mechanism:
`FOR UPDATE SKIP LOCKED` picks a due job, a **lease** holds it rather than a
status flip, and the database is the dispatcher. Two schedulers running by
accident — a systemd timer and a container, a deploy overlapping a restart — get
different jobs rather than both running the same backup.

**A lease, not a status.** A scheduler that dies mid-job leaves the claim to
expire; a status flip would leave the job "running" forever, and the fix would
be somebody editing a row at 2am.

**Missed runs are run once, not caught up.** A machine that was off for a day
leaves a daily job overdue by 24 hours. Rescheduling from *now* rather than from
the old `next_run_at` means one run and then the normal cadence — the
alternative is a burst of catch-up runs the moment the machine returns, which is
the worst time for it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import ScheduledJob

log = get_logger(__name__)

#: How long a claim is held before another scheduler may take the job. Longer
#: than any job here should run; a job that genuinely takes longer than this
#: wants its own lease, not a bigger default.
DEFAULT_LEASE_SECONDS = 3600

#: Consecutive failures before a job is backed off rather than retried on
#: schedule. A broken job that runs every five minutes for a week is a log nobody
#: can read and, if it sends alerts, a channel nobody reads either.
MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_MULTIPLIER = 6

#: Truncated before storage. A job that fails by printing a stack trace should
#: not make the timetable unreadable in the UI that has to display it.
MAX_ERROR_CHARS = 2000


@dataclasses.dataclass(frozen=True)
class Claim:
    job_id: int
    name: str
    module: str
    args: tuple[str, ...]
    interval_seconds: int


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def claim_due_job(
    sess: AsyncSession,
    *,
    scheduler_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: dt.datetime | None = None,
) -> Claim | None:
    """Take one job that is due, or None. Flushes; does not commit.

    ``SKIP LOCKED`` rather than a transaction that waits: a second scheduler
    should move on to the next job, not queue behind the first.
    """
    moment = now or _now()
    row = (
        await sess.execute(
            select(ScheduledJob)
            .where(
                ScheduledJob.enabled.is_(True),
                ScheduledJob.next_run_at <= moment,
                # An unexpired claim belongs to someone else. Expired means the
                # holder died, and the job is free.
                (ScheduledJob.claimed_until.is_(None)) | (ScheduledJob.claimed_until <= moment),
            )
            .order_by(ScheduledJob.next_run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()

    if row is None:
        return None

    row.claimed_by = scheduler_id
    row.claimed_until = moment + dt.timedelta(seconds=lease_seconds)
    await sess.flush()

    return Claim(
        job_id=row.job_id,
        name=row.name,
        module=row.module,
        args=tuple(row.args or ()),
        interval_seconds=row.interval_seconds,
    )


async def settle_job(
    sess: AsyncSession,
    job_id: int,
    *,
    status: str,
    duration_ms: int,
    error: str | None = None,
    now: dt.datetime | None = None,
) -> None:
    """Record the outcome and schedule the next run. Flushes; does not commit."""
    moment = now or _now()
    row = await sess.get(ScheduledJob, job_id)
    if row is None:  # pragma: no cover - deleted mid-run
        return

    failures = 0 if status == "ok" else row.consecutive_failures + 1

    # Rescheduled from *now*, not from the previous `next_run_at`. See the
    # module docstring: catching up produces a burst at the worst moment.
    interval = row.interval_seconds
    if failures >= MAX_CONSECUTIVE_FAILURES:
        # Backed off rather than disabled. Disabling needs a person to notice
        # and re-enable; backing off keeps trying at a rate that does not drown
        # the logs, and recovers on its own when whatever broke is fixed.
        interval *= BACKOFF_MULTIPLIER
        log.warning(
            "scheduled job backed off",
            extra={"job": row.name, "failures": failures, "interval_seconds": interval},
        )

    row.last_run_at = moment
    row.last_status = status
    row.last_duration_ms = duration_ms
    row.last_error = error[:MAX_ERROR_CHARS] if error else None
    row.consecutive_failures = failures
    row.next_run_at = moment + dt.timedelta(seconds=interval)
    row.claimed_by = None
    row.claimed_until = None

    await sess.flush()
    log.info(
        "scheduled job settled",
        extra={
            "job": row.name,
            "status": status,
            "duration_ms": duration_ms,
            "next_run_at": row.next_run_at.isoformat(),
        },
    )


async def release_claims(sess: AsyncSession, scheduler_id: str) -> int:
    """Hand back anything this scheduler holds, on the way out.

    A graceful shutdown that left its claims would make every job it was holding
    wait out the full lease before another scheduler could take it — which on a
    deploy is a gap nobody asked for.
    """
    result = await sess.execute(
        update(ScheduledJob)
        .where(ScheduledJob.claimed_by == scheduler_id)
        .values(claimed_by=None, claimed_until=None)
    )
    return int(result.rowcount or 0)


async def timetable(sess: AsyncSession) -> list[ScheduledJob]:
    """Every job, for the UI and for `/status`."""
    rows = await sess.execute(select(ScheduledJob).order_by(ScheduledJob.name))
    return list(rows.scalars())
