"""The scheduler's heartbeat (task `B-19`, spec §13.4).

`B-15` put the scheduler in both compose files and left it unprobed, because
`P5-08`'s heartbeat is written by `worker.main`'s loop and this is a different
process. The trap found there is the reason this file exists: **omitting
`healthcheck:` does not give a container none.** It inherits the image's, and
the worker image's probe imports `worker.main` and checks poppler — which
passes for as long as the package tree is intact, so a wedged scheduler would
have read `healthy` for ever *and* suppressed the restart that no probe would
have left to `restart: unless-stopped`.

So the loop writes the heartbeat itself, and the two placements are the whole
design:

- **after each claim**, not before it. `worker.main` beats before its work
  because a lane wedged inside a fetch should stop beating within the
  iteration. Here the database round trip that claims a job is the thing that
  can hang, so a beat before it would be refreshed by a scheduler that never
  gets an answer.
- **throughout a running job**, because a backfill legitimately takes half an
  hour and a probe that failed during normal work would restart the scheduler
  in the middle of the job it was reporting on — monitoring causing the outage
  it exists to detect.

Nothing here touches Postgres: the loop's database calls are stubbed, because
the claim in `meridian_core.schedule` has its own integration tests against a
real server and what is under test here is when the file gets written.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from worker.liveness import age_seconds, is_alive
from worker.scheduler import MAX_BEAT_SECONDS, Scheduler


@pytest.fixture
def heartbeat(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the real heartbeat at a file this test owns."""
    path = tmp_path / "scheduler.alive"
    monkeypatch.setenv("MERIDIAN_LIVENESS_PATH", str(path))
    return path


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch):
    """Stub the loop's teardown, which is all it touches besides the claim."""

    @contextlib.asynccontextmanager
    async def fake_session(_role):
        yield object()

    async def released(_sess, _id):
        return 0

    async def disposed():
        return None

    monkeypatch.setattr("worker.scheduler.session", fake_session)
    monkeypatch.setattr("worker.scheduler.release_claims", released)
    monkeypatch.setattr("worker.scheduler.dispose_engines", disposed)


@pytest.fixture
def beats(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record when the loop beat, without going near the filesystem."""
    recorded: list[float] = []
    monkeypatch.setattr("worker.scheduler.beat", lambda: recorded.append(time.monotonic()))
    return recorded


# --------------------------------------------------------------------------
# Where the beat goes


async def test_the_loop_beats_even_when_nothing_is_due(
    monkeypatch: pytest.MonkeyPatch, beats: list[float], no_database
) -> None:
    """An idle scheduler is a working scheduler, and must not look dead.

    Four of the five seeded jobs are daily, so "nothing due" is the state this
    process is in almost all of the time.
    """

    async def nothing_due(_self):
        return None

    monkeypatch.setattr(Scheduler, "_claim", nothing_due)

    await Scheduler(poll_seconds=1, max_jobs=1).run()

    assert beats, "an idle poll left the heartbeat to go stale"


async def test_the_beat_comes_after_the_claim_not_before(
    monkeypatch: pytest.MonkeyPatch, beats: list[float], no_database
) -> None:
    """The ordering is the point: a beat before the claim would be refreshed by
    a scheduler hung on a database that never answers."""
    claims: list[float] = []

    async def slow_claim(_self):
        await asyncio.sleep(0.05)
        claims.append(time.monotonic())
        return None

    monkeypatch.setattr(Scheduler, "_claim", slow_claim)

    await Scheduler(poll_seconds=1, max_jobs=1).run()

    assert claims and beats
    assert beats[0] > claims[0], "beat recorded before the claim returned"


async def test_a_claim_that_never_returns_stops_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch, heartbeat, no_database
) -> None:
    """The failure the probe exists for. A scheduler awaiting a database that
    will not answer is indistinguishable from an idle one *except* through the
    heartbeat going stale."""

    async def wedged(_self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(Scheduler, "_claim", wedged)

    task = asyncio.create_task(Scheduler(poll_seconds=1).run())
    await asyncio.sleep(0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert age_seconds() is None, "a wedged claim still produced a heartbeat"


# --------------------------------------------------------------------------
# The beat that runs alongside a job


async def test_a_job_outlasting_the_threshold_keeps_the_scheduler_alive(
    heartbeat,
) -> None:
    """The one that would restart the stack mid-backfill if it were wrong.

    `is_alive` is asked with a threshold shorter than the simulated job, which
    is the real shape: the worker's default is 300s and an embedding backfill
    runs for considerably longer.
    """
    scheduler = Scheduler(poll_seconds=1)

    async with scheduler._beating():
        await asyncio.sleep(0.4)
        started = age_seconds()
        # Past the beat interval, so an unrepeated beat would now be stale.
        await asyncio.sleep(1.2)
        still_fresh = is_alive(max_age_s=1)

    assert started is not None, "no heartbeat was written while the job ran"
    assert still_fresh, "the heartbeat went stale during a job that was still running"


async def test_the_beating_stops_when_the_job_does(heartbeat) -> None:
    """A beater left running would keep a dead scheduler looking alive, which
    is the same false green this task exists to remove."""
    scheduler = Scheduler(poll_seconds=1)

    async with scheduler._beating():
        await asyncio.sleep(0.2)

    settled = age_seconds()
    await asyncio.sleep(0.5)

    assert age_seconds() >= settled + 0.4, "something is still beating after the job ended"


def test_the_beat_interval_cannot_outrun_the_staleness_window() -> None:
    """A long `--poll-seconds` must not let the file go stale on its own — the
    probe would then be reporting the poll interval rather than the process."""
    assert Scheduler(poll_seconds=3600)._beat_seconds == MAX_BEAT_SECONDS
    assert Scheduler(poll_seconds=2)._beat_seconds == 2
    assert Scheduler(poll_seconds=0)._beat_seconds >= 1, "a zero poll must not spin"
