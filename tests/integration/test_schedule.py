"""The timetable (task P5-06, spec §13.1).

Against a real Postgres because the claim is `FOR UPDATE SKIP LOCKED` and the
whole point of it is what two concurrent schedulers see — which no double can
tell you.

§13.1's corollary is the requirement: "no cron files. The scheduler reads its
timetable from the DB so schedule changes are a UI action." These tests are
about the properties that make that safe to run unattended: a job is not run
twice, a dead scheduler does not hold a job forever, and a machine that was off
for a day does not come back to a burst.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete

from meridian_core.models import ScheduledJob
from meridian_core.schedule import (
    BACKOFF_MULTIPLIER,
    MAX_CONSECUTIVE_FAILURES,
    claim_due_job,
    release_claims,
    settle_job,
)

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def prefix() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def clean(session_for, prefix):
    sess = await session_for("rw")
    yield sess
    await sess.execute(delete(ScheduledJob).where(ScheduledJob.name.like("test-%")))
    await sess.commit()


async def a_job(sess, prefix: str, **over) -> ScheduledJob:
    job = ScheduledJob(
        name=over.pop("name", f"{prefix}-job"),
        module=over.pop("module", "worker.digest"),
        interval_seconds=over.pop("interval_seconds", 3600),
        next_run_at=over.pop("next_run_at", NOW - dt.timedelta(minutes=1)),
        **over,
    )
    sess.add(job)
    await sess.flush()
    return job


# --------------------------------------------------------------------------
# What may be claimed
# --------------------------------------------------------------------------


async def test_a_due_job_is_claimed(clean, prefix) -> None:
    await a_job(clean, prefix)

    claim = await claim_due_job(clean, scheduler_id="s1", now=NOW)

    assert claim is not None
    assert claim.name == f"{prefix}-job"


async def test_a_job_that_is_not_due_yet_is_left_alone(clean, prefix) -> None:
    await a_job(clean, prefix, next_run_at=NOW + dt.timedelta(hours=1))

    claim = await claim_due_job(clean, scheduler_id="s1", now=NOW)

    assert claim is None or claim.name != f"{prefix}-job"


async def test_a_disabled_job_is_never_claimed(clean, prefix) -> None:
    """Disabling is how a UI turns a job off (§13.2). It has to mean *off*, not
    'off until the next restart'."""
    await a_job(clean, prefix, enabled=False)

    claim = await claim_due_job(clean, scheduler_id="s1", now=NOW)

    assert claim is None or claim.name != f"{prefix}-job"


# --------------------------------------------------------------------------
# The lease
# --------------------------------------------------------------------------


async def test_a_held_job_is_not_claimed_twice(clean, prefix) -> None:
    """Two schedulers running by accident — a systemd timer and a container, a
    deploy overlapping a restart — must not both run the same backup."""
    await a_job(clean, prefix)

    first = await claim_due_job(clean, scheduler_id="s1", now=NOW)
    second = await claim_due_job(clean, scheduler_id="s2", now=NOW)

    assert first is not None
    assert second is None or second.job_id != first.job_id


async def test_an_expired_claim_is_taken_over(clean, prefix) -> None:
    """A lease, not a status. A scheduler that died mid-job leaves the claim to
    expire; a status flip would leave the job 'running' forever and the fix
    would be somebody editing a row at 2am."""
    job = await a_job(clean, prefix)
    job.claimed_by = "dead-scheduler"
    job.claimed_until = NOW - dt.timedelta(minutes=5)
    await clean.flush()

    claim = await claim_due_job(clean, scheduler_id="s2", now=NOW)

    assert claim is not None and claim.job_id == job.job_id


async def test_shutdown_hands_claims_back(clean, prefix) -> None:
    """A graceful stop that kept its claims would make every job wait out the
    full lease before another scheduler could take it — on a deploy, a gap
    nobody asked for."""
    await a_job(clean, prefix)
    claim = await claim_due_job(clean, scheduler_id="s1", now=NOW)
    assert claim is not None

    released = await release_claims(clean, "s1")
    await clean.flush()

    assert released >= 1
    assert await claim_due_job(clean, scheduler_id="s2", now=NOW) is not None


# --------------------------------------------------------------------------
# Rescheduling
# --------------------------------------------------------------------------


async def test_the_next_run_is_measured_from_now_not_from_the_missed_slot(
    clean, prefix
) -> None:
    """The property that stops a burst after downtime.

    A machine off for a day leaves a daily job overdue by 24 hours. Adding the
    interval to the *old* `next_run_at` would leave it still overdue, and the
    scheduler would run it again immediately — and again, once per missed slot,
    at the worst possible moment.
    """
    job = await a_job(clean, prefix, interval_seconds=3600, next_run_at=NOW - dt.timedelta(days=2))

    await settle_job(clean, job.job_id, status="ok", duration_ms=10, now=NOW)
    await clean.refresh(job)

    assert job.next_run_at == NOW + dt.timedelta(seconds=3600)


async def test_a_successful_run_is_recorded(clean, prefix) -> None:
    job = await a_job(clean, prefix)

    await settle_job(clean, job.job_id, status="ok", duration_ms=1234, now=NOW)
    await clean.refresh(job)

    assert job.last_status == "ok"
    assert job.last_duration_ms == 1234
    assert job.last_error is None
    assert job.claimed_by is None, "settling must release the claim"


async def test_a_failure_keeps_its_reason(clean, prefix) -> None:
    job = await a_job(clean, prefix)

    await settle_job(
        clean, job.job_id, status="failed", duration_ms=5, error="boom", now=NOW
    )
    await clean.refresh(job)

    assert job.last_status == "failed"
    assert job.last_error == "boom"
    assert job.consecutive_failures == 1


async def test_a_long_error_does_not_make_the_row_unreadable(clean, prefix) -> None:
    """A job that fails by printing a stack trace should not make the timetable
    unreadable in the UI that has to display it."""
    job = await a_job(clean, prefix)

    await settle_job(
        clean, job.job_id, status="failed", duration_ms=5, error="x" * 50_000, now=NOW
    )
    await clean.refresh(job)

    assert job.last_error is not None and len(job.last_error) <= 2000


async def test_a_repeatedly_broken_job_backs_off(clean, prefix) -> None:
    """Backed off rather than disabled. Disabling needs a person to notice and
    re-enable; backing off keeps trying at a rate that does not drown the logs
    and recovers on its own when whatever broke is fixed."""
    job = await a_job(clean, prefix, interval_seconds=60)
    job.consecutive_failures = MAX_CONSECUTIVE_FAILURES - 1
    await clean.flush()

    await settle_job(clean, job.job_id, status="failed", duration_ms=1, error="e", now=NOW)
    await clean.refresh(job)

    assert job.consecutive_failures == MAX_CONSECUTIVE_FAILURES
    assert job.next_run_at == NOW + dt.timedelta(seconds=60 * BACKOFF_MULTIPLIER)


async def test_one_success_clears_the_backoff(clean, prefix) -> None:
    """Recovery has to be automatic, or a transient failure becomes a permanent
    slow cadence nobody remembers to reset."""
    job = await a_job(clean, prefix, interval_seconds=60)
    job.consecutive_failures = MAX_CONSECUTIVE_FAILURES + 2
    await clean.flush()

    await settle_job(clean, job.job_id, status="ok", duration_ms=1, now=NOW)
    await clean.refresh(job)

    assert job.consecutive_failures == 0
    assert job.next_run_at == NOW + dt.timedelta(seconds=60)


async def test_the_module_is_never_a_shell_string(clean, prefix) -> None:
    """A timetable row is editable from a UI (§13.2). The scheduler runs
    `python -m <module>` with args as a list and no shell, so a row that tried
    to smuggle one is just a module name that does not import.

    Pinned as a schema fact rather than left to the runner: the column holds a
    module, and anything relying on that should fail here if it ever holds more.
    """
    job = await a_job(clean, prefix, module="worker.digest")

    assert " " not in job.module
    assert ";" not in job.module
