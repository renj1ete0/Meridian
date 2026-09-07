"""The worker loop: claim, fetch, record, repeat (task P1-15, spec §6.1, §13.4).

Everything this module composes already existed and was unused. `queueing.py`
knows how to hand out a task without handing it out twice; `Crawler.fetch` knows
how to get one URL politely and leave a record of it; `attempts.py` knows what
the last day of crawling looked like. What was missing is the thing that runs
them without anybody watching, which is the only mode this system is ever in.

**Lanes, not a loop.** Concurrency is N independent claim-fetch-settle lanes over
one shared `Crawler`. There is no dispatcher and no in-process queue: the
database is the queue, `FOR UPDATE SKIP LOCKED` is the dispatcher, and two lanes
that both go looking at the same moment get different rows. That also means the
unit of concurrency is the same whether it is four lanes in one process or two
processes of two, so scaling out later needs no coordination to be invented.

**Politeness is not the lane's business.** A lane claims whatever is next, which
may be the fourth URL in a row from one domain. `DomainLimiter` is what stops
that becoming four simultaneous requests to one host — per-domain, shared across
lanes, and already enforced inside `Crawler.fetch`. A loop that tried to schedule
around domains itself would be duplicating that, badly.

**Nothing here raises to the top.** A worker that dies on an unexpected exception
is a worker that stopped crawling on Saturday and gets noticed on Monday. Every
lane catches, logs, settles the task it was holding, and goes back for the next
one; a database that has gone away backs the lane off rather than ending it,
because the outage that matters is the one that outlasts the retry. What is
*not* caught is cancellation — that is the shutdown path, and swallowing it
would turn `SIGTERM` into a process that has to be killed.

**Shutdown is graceful once and immediate twice.** The first signal stops the
lanes claiming and lets the fetches in flight finish, so a task is never
abandoned mid-request. Held leases are dropped on the way out rather than left
to expire, because fifteen minutes of a queue that looks busy and is doing
nothing on every deploy is a bad way to learn about lease expiry. A second
signal cancels, for the case where a fetch is wedged and the operator has
stopped being patient.

Restart supervision itself lives outside the process: `Restart=always` in the
systemd unit (§13.4). Nothing in here tries to be its own supervisor.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import signal
import socket
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.attempts import DEFAULT_RETENTION_DAYS, fetch_health, prune_attempts
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask
from meridian_core.queueing import (
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_RETRIES,
    abandon,
    advance,
    claim_next,
    fail,
    queue_depth,
    queue_disposition,
    reclaim_expired,
    release_worker_claims,
)

from .crawl import Crawler
from .fetch import Crawl4aiClient, Fetcher
from .ratelimit import DomainLimiter

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: Task types this loop can actually process. `query`, `doi` and `sitemap` rows
#: belong to handlers that do not exist yet (P1-14, P1-28); claiming one would
#: mean either failing a task that is not broken or handing it back forever.
HANDLED_TASK_TYPES = ["url"]

DEFAULT_CONCURRENCY = 4
DEFAULT_IDLE_SLEEP_S = 5.0
DEFAULT_HOUSEKEEPING_S = 3600.0

#: How long a lane waits after an error it did not expect. Capped low: the
#: failure this exists for is Postgres restarting, and coming back a minute
#: later is the difference between a blip and an outage that needed a human.
ERROR_BACKOFF_S = (1.0, 5.0, 15.0, 30.0, 60.0)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value < 0:
        raise RuntimeError(f"{name} must not be negative, got {value}")
    return value


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}, got {value}")
    return value


def default_worker_id() -> str:
    """Host plus a per-process suffix.

    The hostname alone would collide the moment two workers run on one box, and
    a bare uuid would make `claimed_by` useless for the question it is actually
    asked — *which machine* is holding this.
    """
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


@dataclasses.dataclass(frozen=True)
class WorkerSettings:
    """Everything the loop's shape depends on, resolved once at startup."""

    worker_id: str = dataclasses.field(default_factory=default_worker_id)
    concurrency: int = DEFAULT_CONCURRENCY
    idle_sleep_s: float = DEFAULT_IDLE_SLEEP_S
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_s: int = DEFAULT_BACKOFF_BASE_S
    housekeeping_interval_s: float = DEFAULT_HOUSEKEEPING_S
    attempt_retention_days: int = DEFAULT_RETENTION_DAYS
    topics: tuple[str, ...] | None = None
    #: Stop after this many claims. None runs until signalled; a number gives a
    #: bounded run for tests and for `--once`-style smoke checks.
    max_tasks: int | None = None

    @classmethod
    def from_env(cls) -> WorkerSettings:
        topics = [t.strip() for t in os.environ.get("MERIDIAN_WORKER_TOPICS", "").split(",")]
        topics = [t for t in topics if t]
        max_tasks = _env_int("MERIDIAN_WORKER_MAX_TASKS", 0, minimum=0)
        return cls(
            worker_id=os.environ.get("MERIDIAN_WORKER_ID") or default_worker_id(),
            concurrency=_env_int("MERIDIAN_WORKER_CONCURRENCY", DEFAULT_CONCURRENCY, minimum=1),
            idle_sleep_s=_env_float("MERIDIAN_WORKER_IDLE_SLEEP_S", DEFAULT_IDLE_SLEEP_S),
            lease_seconds=_env_int(
                "MERIDIAN_WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS, minimum=1
            ),
            housekeeping_interval_s=_env_float(
                "MERIDIAN_WORKER_HOUSEKEEPING_S", DEFAULT_HOUSEKEEPING_S
            ),
            attempt_retention_days=_env_int(
                "MERIDIAN_ATTEMPT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS, minimum=0
            ),
            topics=tuple(topics) or None,
            max_tasks=max_tasks or None,
        )


@dataclasses.dataclass(frozen=True)
class Claim:
    """The claimed task's data, detached from the session that claimed it.

    Carried as plain values rather than as the ORM instance because the claim
    commits and its session closes before any fetching starts — holding a
    connection open across a network request would pin one for the whole fetch,
    and there are more lanes than there are pool slots to spare.
    """

    task_id: int
    url: str
    attempts: int
    topic: str | None


@dataclasses.dataclass
class WorkerStats:
    """What one run of the loop did. Logged on the way out."""

    claimed: int = 0
    fetched: int = 0
    unchanged: int = 0
    retried: int = 0
    abandoned: int = 0
    errored: int = 0
    outcomes: Counter[str] = dataclasses.field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {
            "claimed": self.claimed,
            "fetched": self.fetched,
            "unchanged": self.unchanged,
            "retried": self.retried,
            "abandoned": self.abandoned,
            "errored": self.errored,
            "outcomes": dict(self.outcomes),
        }


class Worker:
    """Drains the queue through a `Crawler` until told to stop."""

    def __init__(
        self,
        crawler: Crawler,
        *,
        settings: WorkerSettings | None = None,
        session_factory: SessionFactory = session,
    ) -> None:
        self._crawler = crawler
        self._settings = settings or WorkerSettings()
        self._session_factory = session_factory
        self._stopping = asyncio.Event()
        self._stats = WorkerStats()
        self._reserved = 0

    @property
    def settings(self) -> WorkerSettings:
        return self._settings

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    def stop(self) -> None:
        """Ask the loop to finish what it is holding and come back."""
        self._stopping.set()

    # -- the run ------------------------------------------------------------

    async def run(self) -> WorkerStats:
        """Claim and fetch until stopped, then hand back what was left."""
        settings = self._settings
        log.info(
            "worker starting",
            extra={
                "worker_id": settings.worker_id,
                "concurrency": settings.concurrency,
                "topics": list(settings.topics or []),
                "max_tasks": settings.max_tasks,
            },
        )

        # Not required — claim_next already ignores an expired lease — but it
        # makes work abandoned by a crash visible in the queue on startup
        # rather than only implicit in a timestamp comparison.
        try:
            async with self._session_factory() as sess:
                reclaimed = await reclaim_expired(sess, lease_seconds=settings.lease_seconds)
            if reclaimed:
                log.info("reclaimed expired leases", extra={"count": reclaimed})
        except Exception:
            # A worker that cannot reach the database at startup should back off
            # in its lanes like any other outage, not refuse to start.
            log.exception("could not reclaim expired leases at startup")

        lanes = [
            asyncio.create_task(self._lane(i), name=f"lane-{i}")
            for i in range(settings.concurrency)
        ]
        keeper = asyncio.create_task(self._housekeeping(), name="housekeeping")
        try:
            await asyncio.gather(*lanes)
        finally:
            # `gather` returns the moment one lane raises, leaving the rest of
            # them running — a caller that got an exception from `run()` would
            # then have three lanes still claiming and fetching behind its back,
            # and would release their leases out from under them. Cancelling is
            # a no-op on the lanes that finished normally.
            for lane in lanes:
                lane.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
            await self._release_claims()
            log.info(
                "worker stopped",
                extra={"worker_id": settings.worker_id, **self._stats.as_dict()},
            )
        return self._stats

    async def _lane(self, index: int) -> None:
        """One claim-fetch-settle loop. Ends only when stopped or out of budget."""
        consecutive_errors = 0
        while not self._stopping.is_set():
            if not self._reserve():
                return
            try:
                claim = await self._claim()
            except Exception:
                self._release_reservation()
                consecutive_errors += 1
                self._stats.errored += 1
                log.exception(
                    "could not claim a task",
                    extra={"lane": index, "consecutive_errors": consecutive_errors},
                )
                await self._sleep(_backoff_for(consecutive_errors))
                continue

            consecutive_errors = 0
            if claim is None:
                self._release_reservation()
                await self._sleep(self._settings.idle_sleep_s)
                continue

            self._stats.claimed += 1
            try:
                await self._process(claim)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._stats.errored += 1
                log.exception(
                    "task failed unexpectedly",
                    extra={"lane": index, "task_id": claim.task_id, "url": claim.url},
                )
                await self._settle_error(claim)

    async def _claim(self) -> Claim | None:
        async with self._session_factory() as sess:
            task = await claim_next(
                sess,
                worker_id=self._settings.worker_id,
                lease_seconds=self._settings.lease_seconds,
                topics=list(self._settings.topics) if self._settings.topics else None,
                task_types=HANDLED_TASK_TYPES,
            )
            if task is None:
                return None
            return Claim(
                task_id=task.task_id,
                url=task.url_or_query,
                attempts=task.attempts,
                topic=task.topic,
            )

    async def _process(self, claim: Claim) -> None:
        """Fetch one claimed task and settle it.

        ``attempt_number`` is ``attempts + 1`` and not ``attempts``: the counter
        on the row is how many attempts have *finished*, so passing it straight
        through would file every retry in the attempt log as a first try, and
        the log's whole purpose is telling a URL that failed once from one that
        has been failing all week.
        """
        result = await self._crawler.fetch(
            claim.url, task_id=claim.task_id, attempt_number=claim.attempts + 1
        )
        self._stats.outcomes[result.outcome] += 1
        disposition = queue_disposition(result.outcome, result.status_code)
        detail = f"{result.outcome}: {result.detail}" if result.detail else result.outcome

        async with self._session_factory() as sess:
            task = await sess.get(QueueTask, claim.task_id)
            if task is None:
                # Deleted underneath us — a bulk queue purge from Admin, say.
                # The fetch_attempts row survives it (task_id is ON DELETE SET
                # NULL), so the request is still on the health line.
                log.warning(
                    "claimed task vanished before it settled",
                    extra={"task_id": claim.task_id},
                )
                return

            if disposition == "fetched":
                await advance(sess, task, "fetched")
                self._stats.fetched += 1
            elif disposition == "done":
                await advance(sess, task, "done")
                self._stats.unchanged += 1
            elif disposition == "retry":
                retrying = await fail(
                    sess,
                    task,
                    detail,
                    max_retries=self._settings.max_retries,
                    backoff_base_s=self._settings.backoff_base_s,
                )
                if retrying:
                    self._stats.retried += 1
                else:
                    self._stats.abandoned += 1
            else:
                await abandon(sess, task, detail)
                self._stats.abandoned += 1

        log.info(
            "task settled",
            extra={
                "task_id": claim.task_id,
                "url": claim.url,
                "topic": claim.topic,
                "outcome": result.outcome,
                "status": result.status_code,
                "disposition": disposition,
                "attempt_number": claim.attempts + 1,
            },
        )

    async def _settle_error(self, claim: Claim) -> None:
        """Give a task back after an exception the loop did not expect.

        Left claimed, it would sit out its whole lease before anyone could try
        it again. Failed with a retry, it comes back after a backoff — which is
        the right answer when nobody yet knows whether the bug was in the task
        or in us.
        """
        try:
            async with self._session_factory() as sess:
                task = await sess.get(QueueTask, claim.task_id)
                if task is not None:
                    await fail(
                        sess,
                        task,
                        "worker error",
                        max_retries=self._settings.max_retries,
                        backoff_base_s=self._settings.backoff_base_s,
                    )
        except Exception:
            # The database is where the failure probably was. The lease expiry
            # is the backstop for exactly this.
            log.exception("could not record a failed task", extra={"task_id": claim.task_id})

    # -- housekeeping -------------------------------------------------------

    async def _housekeeping(self) -> None:
        """Prune the attempt log and log the health line, on a slow tick.

        Runs here because there is nowhere else: `fetch_attempts` gains a row
        per request and nothing else in the system is awake often enough to
        bound it. Cancelled rather than stopped on shutdown — a prune half done
        is a prune, and the next tick finishes it.
        """
        interval = self._settings.housekeeping_interval_s
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await self.housekeep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("housekeeping tick failed")

    async def housekeep(self) -> None:
        """One housekeeping pass. Public so a test — or an operator — can run it."""
        async with self._session_factory() as sess:
            pruned = await prune_attempts(
                sess, older_than_days=self._settings.attempt_retention_days
            )
            health = await fetch_health(sess)
            depth = await queue_depth(sess)

        log.info(
            "health",
            extra={
                # §12.5's daily health line, or the fetch and queue half of it.
                # Novelty pass rate and edges added belong to the orchestrator
                # and are not this process's to report.
                "queue_depth": depth,
                "pending": depth.get("pending", 0),
                "fetch_attempts": health.attempts,
                "fetch_success_rate": health.success_rate,
                "by_outcome": health.by_outcome,
                "attempts_pruned": pruned,
                "tracked_domains": self._crawler.limiter.tracked_domains,
            },
        )

    # -- odds and ends ------------------------------------------------------

    def _reserve(self) -> bool:
        """Take one slot of the ``max_tasks`` budget, if there is one.

        Reserved before the claim rather than counted after it, so N lanes
        racing on the last slot cannot between them claim N+1 tasks.
        """
        if self._settings.max_tasks is None:
            return True
        if self._reserved >= self._settings.max_tasks:
            return False
        self._reserved += 1
        return True

    def _release_reservation(self) -> None:
        if self._settings.max_tasks is not None:
            self._reserved -= 1

    async def _sleep(self, seconds: float) -> None:
        """Wait, but wake immediately if the worker has been asked to stop.

        A plain sleep would make shutdown take up to a full idle interval per
        lane, which is the difference between a deploy that feels instant and
        one that looks hung.
        """
        if seconds <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    async def _release_claims(self) -> None:
        try:
            async with self._session_factory() as sess:
                released = await release_worker_claims(sess, self._settings.worker_id)
            if released:
                log.info("released held leases", extra={"count": released})
        except Exception:
            log.exception("could not release held leases; they will expire instead")


def install_signal_handlers(worker: Worker) -> None:
    """Wire SIGINT/SIGTERM: first asks, second insists.

    The second signal cancels every task in the loop, unwinding `run()` through
    the cancellation path — deliberately harsher than the first, because by the
    time an operator sends it they have already waited once.

    `add_signal_handler` is POSIX-only and raises on Windows and inside a thread
    that is not the main one; suppressed rather than required, since a worker
    that cannot install handlers should still crawl.
    """
    loop = asyncio.get_running_loop()
    state = {"signalled": False}

    def handle(signame: str) -> None:
        if state["signalled"]:
            log.warning("second signal; stopping now", extra={"signal": signame})
            for task in asyncio.all_tasks(loop):
                task.cancel()
            return
        state["signalled"] = True
        log.info("shutting down; finishing fetches in flight", extra={"signal": signame})
        worker.stop()

    for signame in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signame), handle, signame)


async def run_worker(settings: WorkerSettings | None = None) -> WorkerStats:
    """Build the whole fetch stack from the environment and run it."""
    settings = settings or WorkerSettings.from_env()
    browser = Crawl4aiClient.from_env()
    if browser is None:
        # Correct — the static path is most of the corpus — but worth saying out
        # loud, because a worker that has quietly lost its browser for a week
        # extracts worse and reports nothing (P1-26).
        log.warning("no CRAWL4AI_URL; JS-dependent pages will be fetched statically only")

    async with Fetcher(browser=browser) as fetcher:
        crawler = Crawler(session, fetcher=fetcher, limiter=DomainLimiter())
        worker = Worker(crawler, settings=settings)
        install_signal_handlers(worker)
        try:
            return await worker.run()
        finally:
            await dispose_engines()


def main() -> None:
    """Entry point: `python -m worker.main`."""
    configure_logging("worker")
    settings = WorkerSettings.from_env()
    # One run_id for the process, on every record it emits. A crawl that ran for
    # six hours is one thing to grep for, not a timestamp range to guess at.
    # Both endings of the shutdown path are ordinary here, not errors: Ctrl-C
    # raises KeyboardInterrupt, and a second signal cancels the run. Neither
    # should print a traceback on a worker that did what it was asked.
    quiet_exits = (KeyboardInterrupt, asyncio.CancelledError)
    with bind_run_id(f"worker-{settings.worker_id}"), contextlib.suppress(*quiet_exits):
        asyncio.run(run_worker(settings))


def _backoff_for(consecutive_errors: int) -> float:
    return ERROR_BACKOFF_S[min(consecutive_errors, len(ERROR_BACKOFF_S)) - 1]


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
