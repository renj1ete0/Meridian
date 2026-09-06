"""Claiming and releasing queue tasks (spec §5.1, §6.1, §13.4).

The queue is the decoupling point between planes — the worker and the
orchestrator never call each other, they only leave rows here (§2 principle 2) —
so the claim has to be correct under concurrency without either side knowing the
other exists.

Three properties the implementation exists for:

**No task is claimed twice.** ``FOR UPDATE SKIP LOCKED`` does the work: a
competing claimer skips a locked row rather than blocking on it, so N workers
drain the queue in parallel without coordination and without a queue server.

**A dead worker does not strand its task.** Claiming takes a *lease* rather than
flipping status. A crashed worker leaves a claim that simply expires; nothing has
to notice the crash, which is the only design that survives "runs unattended for
weeks".

**A failing domain stops spinning the queue.** Failures set ``next_attempt_at``
to an exponentially backed-off time, so a dead site costs one attempt per backoff
window instead of one per loop iteration (§13.4).
"""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import QueueTask

DEFAULT_LEASE_SECONDS = 900  # 15 min — longer than any single fetch should take
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_S = 5


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def backoff_delay_s(
    attempts: int,
    base_s: int = DEFAULT_BACKOFF_BASE_S,
    max_s: int = 3600,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter, capped.

    Jittered because synchronised retries are how a transient outage turns into
    a thundering herd the moment the domain recovers: without it every task that
    failed together also retries together. Full jitter (a draw from ``[0, d]``
    rather than ``d ± ε``) spreads them properly.
    """
    if attempts < 0:
        raise ValueError("attempts must not be negative")
    ceiling = min(base_s * (2**attempts), max_s)
    return (rng or random).uniform(0, ceiling)


async def claim_next(
    sess: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    topics: list[str] | None = None,
) -> QueueTask | None:
    """Claim the highest-priority eligible task, or return None if there is none.

    Eligible means: pending, past its backoff time, and either unclaimed or
    holding a lease that has expired. Ordered by priority then age, so
    tier-upranked results (§5.2) are fetched first and nothing starves.

    The row lock is held only for the duration of this statement — the claim is
    committed before any fetching starts, because holding a transaction open
    across a network fetch would pin a connection for the whole request.
    """
    now = _now()
    lease_cutoff = now - dt.timedelta(seconds=lease_seconds)

    stmt = (
        select(QueueTask)
        .where(
            QueueTask.status == "pending",
            or_(QueueTask.next_attempt_at.is_(None), QueueTask.next_attempt_at <= now),
            or_(QueueTask.claimed_at.is_(None), QueueTask.claimed_at < lease_cutoff),
        )
        .order_by(QueueTask.priority.desc(), QueueTask.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if topics:
        stmt = stmt.where(QueueTask.topic.in_(topics))

    task = (await sess.execute(stmt)).scalar_one_or_none()
    if task is None:
        return None

    task.claimed_at = now
    task.claimed_by = worker_id
    await sess.flush()
    return task


async def release(sess: AsyncSession, task: QueueTask) -> None:
    """Drop the lease without consuming an attempt.

    For shutdown, not for failure: a worker stopping cleanly should hand the task
    straight back rather than making it wait out a backoff it did not earn.
    """
    task.claimed_at = None
    task.claimed_by = None
    await sess.flush()


async def advance(sess: AsyncSession, task: QueueTask, status: str) -> None:
    """Move a task along the status flow and drop its lease."""
    task.status = status
    task.claimed_at = None
    task.claimed_by = None
    if status == "fetched":
        task.fetched_at = _now()
    await sess.flush()


async def fail(
    sess: AsyncSession,
    task: QueueTask,
    error: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_s: int = DEFAULT_BACKOFF_BASE_S,
    rng: random.Random | None = None,
) -> bool:
    """Record a failure. Returns True if the task will be retried.

    Past ``max_retries`` the task is marked ``failed`` and left in place rather
    than deleted — the error text is the only record of why a URL never made it
    in, and §12.5's health line depends on being able to see it.
    """
    task.attempts += 1
    task.error = error[:2000]
    task.claimed_at = None
    task.claimed_by = None

    if task.attempts > max_retries:
        task.status = "failed"
        task.next_attempt_at = None
        await sess.flush()
        return False

    delay = backoff_delay_s(task.attempts, base_s=backoff_base_s, rng=rng)
    task.next_attempt_at = _now() + dt.timedelta(seconds=delay)
    await sess.flush()
    return True


async def reclaim_expired(sess: AsyncSession, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> int:
    """Clear leases held past their expiry. Returns how many were released.

    Not strictly required — :func:`claim_next` already ignores an expired lease —
    but running it on startup makes abandoned work visible in the queue rather
    than only implicit in a timestamp comparison.
    """
    cutoff = _now() - dt.timedelta(seconds=lease_seconds)
    result = await sess.execute(
        update(QueueTask)
        .where(QueueTask.claimed_at.is_not(None), QueueTask.claimed_at < cutoff)
        .values(claimed_at=None, claimed_by=None)
    )
    await sess.flush()
    return result.rowcount or 0
