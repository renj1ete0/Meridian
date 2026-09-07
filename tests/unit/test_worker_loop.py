"""The loop's mechanics, without a database (task P1-15, spec §13.4).

The parts tested here are the ones that decide whether a worker survives a bad
week: does a lane keep going after an exception, does a database outage back off
instead of ending the run, does shutdown actually stop it, and does the retry
count reaching the attempt log mean what it says. A fake crawler and a fake
session factory are enough for all of that, and `test_worker_run.py` covers the
same loop against a real Postgres.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from contextlib import asynccontextmanager

import pytest

from worker.fetch import FetchResult
from worker.main import (
    ERROR_BACKOFF_S,
    Claim,
    Worker,
    WorkerSettings,
    _backoff_for,
    default_worker_id,
)


class FakeCrawler:
    """Records what it was asked to fetch and answers from a script."""

    def __init__(self, results: list[FetchResult | Exception] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[dict[str, object]] = []
        self.limiter = _NoLimiter()

    async def fetch(self, url, *, task_id=None, attempt_number=1):
        self.calls.append({"url": url, "task_id": task_id, "attempt_number": attempt_number})
        answer = self._results.pop(0) if self._results else ok(url)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _NoLimiter:
    tracked_domains = 0


def ok(url: str = "https://example.test/a") -> FetchResult:
    return FetchResult(requested_url=url, final_url=url, outcome="success", status_code=200)


def outcome(name: str, status: int | None = None, url: str = "https://example.test/a"):
    return FetchResult(requested_url=url, final_url=url, outcome=name, status_code=status)


@dataclasses.dataclass
class FakeTask:
    """Enough of a QueueTask for `advance` / `fail` / `abandon` to act on."""

    task_id: int
    url_or_query: str = "https://example.test/a"
    attempts: int = 0
    topic: str | None = None
    status: str = "pending"
    error: str | None = None
    next_attempt_at: object = None
    claimed_at: object = None
    claimed_by: str | None = None
    fetched_at: object = None


class FakeSession:
    """A session over an in-memory task table, with scripted failures."""

    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def get(self, _model, task_id):
        return self._store.tasks.get(task_id)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


class FakeStore:
    """The database stand-in: hands out tasks, and can be told to break."""

    def __init__(self, tasks: list[FakeTask] | None = None) -> None:
        self.tasks = {t.task_id: t for t in (tasks or [])}
        self._claimable = list(tasks or [])
        self.claim_error: Exception | None = None
        self.claim_calls = 0
        self.sessions_opened = 0

    @asynccontextmanager
    async def factory(self):
        self.sessions_opened += 1
        yield FakeSession(self)

    def next_claim(self) -> FakeTask | None:
        self.claim_calls += 1
        if self.claim_error is not None:
            raise self.claim_error
        return self._claimable.pop(0) if self._claimable else None


def build(store: FakeStore, crawler, monkeypatch, **overrides) -> Worker:
    """A Worker whose claim goes to the fake store instead of Postgres."""
    settings = WorkerSettings(
        worker_id="test-worker",
        concurrency=overrides.pop("concurrency", 1),
        idle_sleep_s=overrides.pop("idle_sleep_s", 0.01),
        housekeeping_interval_s=overrides.pop("housekeeping_interval_s", 0),
        **overrides,
    )
    worker = Worker(crawler, settings=settings, session_factory=store.factory)

    async def fake_claim(sess, **kwargs):
        task = store.next_claim()
        if task is not None:
            task.claimed_by = kwargs.get("worker_id")
        return task

    async def noop(*args, **kwargs):
        return 0

    monkeypatch.setattr("worker.main.claim_next", fake_claim)
    monkeypatch.setattr("worker.main.reclaim_expired", noop)
    monkeypatch.setattr("worker.main.release_worker_claims", noop)
    return worker


# --------------------------------------------------------------------------
# The retry counter — the trap P1-15 names by name
# --------------------------------------------------------------------------


async def test_attempt_number_is_one_past_the_finished_attempts(monkeypatch) -> None:
    """`attempts` counts finished attempts, so this one is `attempts + 1`.

    Passing `task.attempts` straight through would file every retry in
    `fetch_attempts` as attempt 1, and a URL that has failed all week would be
    indistinguishable from one that has just been queued — which is the single
    question the attempt log exists to answer.
    """
    store = FakeStore([FakeTask(task_id=7, attempts=2)])
    crawler = FakeCrawler()
    worker = build(store, crawler, monkeypatch, max_tasks=1)

    await worker.run()

    assert crawler.calls == [
        {"url": "https://example.test/a", "task_id": 7, "attempt_number": 3},
    ]


async def test_a_first_attempt_is_numbered_one(monkeypatch) -> None:
    store = FakeStore([FakeTask(task_id=1, attempts=0)])
    crawler = FakeCrawler()
    worker = build(store, crawler, monkeypatch, max_tasks=1)

    await worker.run()

    assert crawler.calls[0]["attempt_number"] == 1


# --------------------------------------------------------------------------
# Settling a task
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result,expected_status",
    [
        (outcome("success", 200), "fetched"),
        (outcome("not_modified", 304), "done"),
        (outcome("robots_denied"), "failed"),
        (outcome("http_error", 404), "failed"),
    ],
)
async def test_the_outcome_decides_the_next_status(
    monkeypatch, result: FetchResult, expected_status: str
) -> None:
    task = FakeTask(task_id=3)
    store = FakeStore([task])
    worker = build(store, FakeCrawler([result]), monkeypatch, max_tasks=1)

    await worker.run()

    assert task.status == expected_status


async def test_a_transient_failure_is_retried_rather_than_failed(monkeypatch) -> None:
    task = FakeTask(task_id=4)
    store = FakeStore([task])
    worker = build(store, FakeCrawler([outcome("timeout")]), monkeypatch, max_tasks=1)

    stats = await worker.run()

    assert task.status == "pending"
    assert task.attempts == 1
    assert task.next_attempt_at is not None  # backed off, not immediately eligible
    assert stats.retried == 1 and stats.abandoned == 0


async def test_a_refusal_is_abandoned_without_spending_retries(monkeypatch) -> None:
    """`abandon` is what stops robots.txt being asked the same question thrice."""
    task = FakeTask(task_id=5)
    store = FakeStore([task])
    worker = build(store, FakeCrawler([outcome("robots_denied")]), monkeypatch, max_tasks=1)

    stats = await worker.run()

    assert task.status == "failed"
    assert task.attempts == 1  # counted honestly, but never retried
    assert task.next_attempt_at is None
    assert stats.abandoned == 1 and stats.retried == 0


async def test_the_error_text_names_the_outcome(monkeypatch) -> None:
    """`queue.error` is the only record of why a URL never made it in."""
    task = FakeTask(task_id=6)
    store = FakeStore([task])
    result = dataclasses.replace(outcome("unsafe_target"), detail="private address")
    worker = build(store, FakeCrawler([result]), monkeypatch, max_tasks=1)

    await worker.run()

    assert task.error == "unsafe_target: private address"


async def test_a_lease_is_dropped_however_the_task_ends(monkeypatch) -> None:
    """A settled task that keeps its lease is invisible to every other worker."""
    tasks = [FakeTask(task_id=i) for i in (10, 11, 12)]
    store = FakeStore(tasks)
    results = [outcome("success", 200), outcome("timeout"), outcome("robots_denied")]
    worker = build(store, FakeCrawler(results), monkeypatch, max_tasks=3)

    await worker.run()

    assert [t.claimed_by for t in tasks] == [None, None, None]
    assert [t.claimed_at for t in tasks] == [None, None, None]


# --------------------------------------------------------------------------
# Surviving things
# --------------------------------------------------------------------------


async def test_an_exception_from_the_fetch_does_not_end_the_lane(monkeypatch) -> None:
    """The bug that stops a crawl on Saturday and is noticed on Monday."""
    tasks = [FakeTask(task_id=1), FakeTask(task_id=2)]
    store = FakeStore(tasks)
    crawler = FakeCrawler([RuntimeError("something nobody predicted"), ok()])
    worker = build(store, crawler, monkeypatch, max_tasks=2)

    stats = await worker.run()

    assert stats.errored == 1
    assert stats.fetched == 1
    assert len(crawler.calls) == 2  # it went back for the second task


async def test_a_task_that_raised_is_handed_back_with_a_backoff(monkeypatch) -> None:
    """Left claimed, it would sit out a full lease before anyone retried it."""
    task = FakeTask(task_id=1)
    store = FakeStore([task])
    worker = build(store, FakeCrawler([RuntimeError("boom")]), monkeypatch, max_tasks=1)

    await worker.run()

    assert task.status == "pending"
    assert task.claimed_by is None
    assert task.attempts == 1
    assert task.next_attempt_at is not None


async def test_a_database_outage_backs_the_lane_off_instead_of_ending_it(monkeypatch) -> None:
    """Postgres restarting must cost a minute, not the rest of the week."""
    store = FakeStore()
    store.claim_error = ConnectionError("server closed the connection unexpectedly")
    worker = build(store, FakeCrawler(), monkeypatch)

    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 3:
            worker.stop()

    monkeypatch.setattr(worker, "_sleep", record_sleep)

    stats = await asyncio.wait_for(worker.run(), timeout=5)

    assert store.claim_calls >= 3  # it kept trying
    assert stats.errored >= 3
    assert slept == [ERROR_BACKOFF_S[0], ERROR_BACKOFF_S[1], ERROR_BACKOFF_S[2]]


async def test_the_error_backoff_climbs_and_then_plateaus() -> None:
    assert _backoff_for(1) == ERROR_BACKOFF_S[0]
    assert _backoff_for(len(ERROR_BACKOFF_S)) == ERROR_BACKOFF_S[-1]
    # Capped, not indexed off the end: a day of outage must not raise IndexError.
    assert _backoff_for(10_000) == ERROR_BACKOFF_S[-1]


async def test_a_claim_that_fails_does_not_spend_the_task_budget(monkeypatch) -> None:
    """Otherwise a bounded run could end having fetched nothing at all."""
    store = FakeStore([FakeTask(task_id=1)])
    store.claim_error = ConnectionError("down")
    crawler = FakeCrawler()
    worker = build(store, crawler, monkeypatch, max_tasks=1)

    async def recover(_seconds: float) -> None:
        store.claim_error = None

    monkeypatch.setattr(worker, "_sleep", recover)

    stats = await asyncio.wait_for(worker.run(), timeout=5)

    assert stats.claimed == 1
    assert len(crawler.calls) == 1


async def test_a_task_deleted_mid_flight_is_not_an_error(monkeypatch) -> None:
    """An Admin queue purge while a fetch is in the air."""
    task = FakeTask(task_id=1)
    store = FakeStore([task])
    crawler = FakeCrawler()
    worker = build(store, crawler, monkeypatch, max_tasks=1)
    del store.tasks[1]  # gone by the time the settle looks for it

    stats = await worker.run()

    assert stats.errored == 0
    assert stats.fetched == 0


# --------------------------------------------------------------------------
# Stopping
# --------------------------------------------------------------------------


async def test_an_empty_queue_idles_rather_than_spinning(monkeypatch) -> None:
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, idle_sleep_s=0.05)

    async def stop_soon() -> None:
        await asyncio.sleep(0.12)
        worker.stop()

    _, stats = await asyncio.gather(stop_soon(), worker.run())

    assert stats.claimed == 0
    # Idling, not hot-looping: a few polls in 120ms, not thousands.
    assert store.claim_calls < 20


async def test_stop_wakes_an_idling_lane_immediately(monkeypatch) -> None:
    """Shutdown must not wait out a full idle interval per lane."""
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, idle_sleep_s=30, concurrency=3)

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        worker.stop()

    async def both() -> None:
        await asyncio.gather(stop_soon(), worker.run())

    await asyncio.wait_for(both(), timeout=5)


async def test_a_fetch_in_flight_finishes_before_shutdown(monkeypatch) -> None:
    """Stopping mid-request would leave a task claimed and unrecorded."""
    task = FakeTask(task_id=1)
    store = FakeStore([task])

    class SlowCrawler(FakeCrawler):
        async def fetch(self, url, *, task_id=None, attempt_number=1):
            worker.stop()  # signalled while the request is in the air
            await asyncio.sleep(0.05)
            return await super().fetch(url, task_id=task_id, attempt_number=attempt_number)

    worker = build(store, SlowCrawler(), monkeypatch)

    stats = await asyncio.wait_for(worker.run(), timeout=5)

    assert stats.fetched == 1
    assert task.status == "fetched"


async def test_held_leases_are_released_on_the_way_out(monkeypatch) -> None:
    """Otherwise every deploy leaves a queue that looks busy for 15 minutes."""
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, max_tasks=0 or None)
    worker.stop()

    released: list[str] = []

    async def record(sess, worker_id):
        released.append(worker_id)
        return 2

    monkeypatch.setattr("worker.main.release_worker_claims", record)

    await worker.run()

    assert released == ["test-worker"]


async def test_leases_are_released_even_after_the_lanes_blow_up(monkeypatch) -> None:
    """The `finally` matters more than the happy path it guards."""
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch)
    released: list[str] = []

    async def record(sess, worker_id):
        released.append(worker_id)
        return 0

    monkeypatch.setattr("worker.main.release_worker_claims", record)

    async def explode(_index: int) -> None:
        raise RuntimeError("lane died")

    monkeypatch.setattr(worker, "_lane", explode)

    with pytest.raises(RuntimeError):
        await worker.run()

    assert released == ["test-worker"]


# --------------------------------------------------------------------------
# Budget and concurrency
# --------------------------------------------------------------------------


async def test_lanes_racing_on_the_last_slot_cannot_overshoot(monkeypatch) -> None:
    """`max_tasks` is a budget, not a suggestion — reserved before the claim."""
    store = FakeStore([FakeTask(task_id=i) for i in range(20)])
    crawler = FakeCrawler()
    worker = build(store, crawler, monkeypatch, concurrency=8, max_tasks=5)

    stats = await asyncio.wait_for(worker.run(), timeout=5)

    assert stats.claimed == 5
    assert len(crawler.calls) == 5


async def test_lanes_run_concurrently(monkeypatch) -> None:
    """Four lanes over one crawler, not four sequential fetches."""
    store = FakeStore([FakeTask(task_id=i) for i in range(4)])
    in_flight = 0
    peak = 0

    class CountingCrawler(FakeCrawler):
        async def fetch(self, url, *, task_id=None, attempt_number=1):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.02)
                return await super().fetch(url, task_id=task_id, attempt_number=attempt_number)
            finally:
                in_flight -= 1

    worker = build(store, CountingCrawler(), monkeypatch, concurrency=4, max_tasks=4)

    await asyncio.wait_for(worker.run(), timeout=5)

    assert peak > 1


async def test_only_url_tasks_are_claimed(monkeypatch) -> None:
    """A `query` or `doi` row belongs to a handler that does not exist yet.

    Claiming one would leave two bad options — fail a task that is not broken,
    or hand it back and claim it again forever — so the filter goes in the
    query. This asserts the loop actually passes it.
    """
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch)
    seen: dict[str, object] = {}

    async def capture(sess, **kwargs):
        seen.update(kwargs)
        worker.stop()
        return None

    monkeypatch.setattr("worker.main.claim_next", capture)

    await asyncio.wait_for(worker.run(), timeout=5)

    assert seen["task_types"] == ["url"]


async def test_topics_are_passed_through_when_configured(monkeypatch) -> None:
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, topics=("transit", "housing"))
    seen: dict[str, object] = {}

    async def capture(sess, **kwargs):
        seen.update(kwargs)
        worker.stop()
        return None

    monkeypatch.setattr("worker.main.claim_next", capture)

    await asyncio.wait_for(worker.run(), timeout=5)

    assert seen["topics"] == ["transit", "housing"]
    assert seen["worker_id"] == "test-worker"


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------


async def test_housekeeping_prunes_and_logs_the_health_line(monkeypatch) -> None:
    """`fetch_attempts` gains a row per request and nothing else bounds it."""
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch)
    pruned_with: dict[str, object] = {}

    async def fake_prune(sess, *, older_than_days, **kwargs):
        pruned_with["older_than_days"] = older_than_days
        return 41

    async def fake_health(sess, **kwargs):
        class H:
            attempts = 100
            success_rate = 0.9
            by_outcome = {"success": 90, "timeout": 10}

        return H()

    async def fake_depth(sess):
        return {"pending": 12, "failed": 3}

    monkeypatch.setattr("worker.main.prune_attempts", fake_prune)
    monkeypatch.setattr("worker.main.fetch_health", fake_health)
    monkeypatch.setattr("worker.main.queue_depth", fake_depth)

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("worker.main")
    # The level has to be set, not assumed: the health line is INFO, the root
    # logger defaults to WARNING, and a level check drops a record before any
    # handler sees it. Without this the test passes only when some earlier test
    # happened to call configure_logging first.
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        await worker.housekeep()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert pruned_with["older_than_days"] == worker.settings.attempt_retention_days
    line = next(r for r in records if r.getMessage() == "health")
    assert line.pending == 12
    assert line.fetch_success_rate == 0.9
    assert line.attempts_pruned == 41
    assert line.by_outcome == {"success": 90, "timeout": 10}


async def test_a_failing_housekeeping_tick_does_not_end_the_worker(monkeypatch) -> None:
    """A prune that cannot run is a smaller problem than a crawl that stopped."""
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, housekeeping_interval_s=0.01)

    ticks = 0

    async def broken() -> None:
        nonlocal ticks
        ticks += 1
        if ticks >= 3:
            worker.stop()
        raise RuntimeError("prune failed")

    monkeypatch.setattr(worker, "housekeep", broken)

    await asyncio.wait_for(worker.run(), timeout=5)

    assert ticks >= 3


async def test_housekeeping_is_off_when_the_interval_is_zero(monkeypatch) -> None:
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, housekeeping_interval_s=0)
    calls = 0

    async def counted() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(worker, "housekeep", counted)
    worker.stop()

    await worker.run()

    assert calls == 0


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_worker_ids_are_unique_per_process() -> None:
    """The hostname alone collides the moment two workers share a box."""
    assert default_worker_id() != default_worker_id()


def test_settings_read_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_WORKER_ID", "pi-1")
    monkeypatch.setenv("MERIDIAN_WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("MERIDIAN_WORKER_IDLE_SLEEP_S", "0.5")
    monkeypatch.setenv("MERIDIAN_WORKER_TOPICS", "transit, housing ,")
    monkeypatch.setenv("MERIDIAN_WORKER_MAX_TASKS", "3")

    settings = WorkerSettings.from_env()

    assert settings.worker_id == "pi-1"
    assert settings.concurrency == 2
    assert settings.idle_sleep_s == 0.5
    assert settings.topics == ("transit", "housing")
    assert settings.max_tasks == 3


def test_an_unset_environment_falls_back_to_defaults(monkeypatch) -> None:
    for var in (
        "MERIDIAN_WORKER_ID",
        "MERIDIAN_WORKER_CONCURRENCY",
        "MERIDIAN_WORKER_IDLE_SLEEP_S",
        "MERIDIAN_WORKER_TOPICS",
        "MERIDIAN_WORKER_MAX_TASKS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = WorkerSettings.from_env()

    assert settings.topics is None
    assert settings.max_tasks is None  # 0 means unbounded, not "stop immediately"
    assert settings.concurrency >= 1


@pytest.mark.parametrize(
    "var,value",
    [
        ("MERIDIAN_WORKER_CONCURRENCY", "nonsense"),
        ("MERIDIAN_WORKER_CONCURRENCY", "0"),  # a worker with no lanes is a bug
        ("MERIDIAN_WORKER_IDLE_SLEEP_S", "-1"),
        ("MERIDIAN_WORKER_IDLE_SLEEP_S", "soon"),
        ("MERIDIAN_ATTEMPT_RETENTION_DAYS", "-5"),
    ],
)
def test_a_misconfigured_environment_fails_loudly_at_startup(monkeypatch, var, value) -> None:
    """Better than a worker that silently runs with one lane and no retention."""
    monkeypatch.setenv(var, value)
    with pytest.raises(RuntimeError, match=var):
        WorkerSettings.from_env()


async def test_stats_survive_a_run_and_name_the_outcomes(monkeypatch) -> None:
    store = FakeStore([FakeTask(task_id=i) for i in range(3)])
    results = [outcome("success", 200), outcome("not_modified", 304), outcome("timeout")]
    worker = build(store, FakeCrawler(results), monkeypatch, max_tasks=3)

    stats = await worker.run()

    assert stats.as_dict()["outcomes"] == {"success": 1, "not_modified": 1, "timeout": 1}
    assert (stats.fetched, stats.unchanged, stats.retried) == (1, 1, 1)


def test_a_claim_is_plain_data_not_an_orm_row() -> None:
    """The claim outlives the session that made it, so it must not be attached.

    Holding the ORM instance would mean either a lazy load against a closed
    session or a connection pinned for the whole fetch, and there are more lanes
    than there are pool slots to spare.
    """
    claim = Claim(task_id=1, url="https://example.test/a", attempts=0, topic=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.task_id = 2  # type: ignore[misc]


async def test_cancellation_is_not_swallowed(monkeypatch) -> None:
    """A caught CancelledError turns SIGTERM into a process that needs killing."""
    store = FakeStore([FakeTask(task_id=1)])

    class HangingCrawler(FakeCrawler):
        async def fetch(self, url, *, task_id=None, attempt_number=1):
            await asyncio.sleep(3600)

    worker = build(store, HangingCrawler(), monkeypatch)
    run = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    run.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(run, timeout=5)
    assert run.cancelled()


async def test_a_lane_that_raises_takes_the_other_lanes_down_with_it(monkeypatch) -> None:
    """`gather` returns on the first exception and leaves the rest running.

    Without the cancel in `run`'s `finally`, a caller that got an exception out
    of `run()` would have three lanes still claiming and fetching behind its
    back — and `_release_claims` would then drop the leases on tasks those lanes
    were in the middle of.
    """
    store = FakeStore()
    worker = build(store, FakeCrawler(), monkeypatch, concurrency=3, idle_sleep_s=30)
    running: set[int] = set()
    real_lane = worker._lane

    async def lane(index: int) -> None:
        if index == 0:
            raise RuntimeError("lane 0 died")
        running.add(index)
        try:
            await real_lane(index)
        finally:
            running.discard(index)

    monkeypatch.setattr(worker, "_lane", lane)

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(worker.run(), timeout=5)

    assert running == set(), "run() returned with lanes still going"
