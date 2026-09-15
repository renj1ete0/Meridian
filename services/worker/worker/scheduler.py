"""The scheduler (task P5-06, spec §13.1, §13.4).

`python -m worker.scheduler` — reads its timetable from the database, and there
is no crontab anywhere.

§13.1: *"no cron files. The scheduler reads its timetable from the DB so
schedule changes are a UI action."* A crontab on the box is configuration nobody
can see from the interface, cannot change without SSH, and does not travel with
a snapshot — a corpus restored elsewhere arrives with no idea what was supposed
to be running.

**Jobs run as subprocesses, not in this process.** Each is already a `python -m`
entry point that opens its own database connections and exits; importing and
calling them here would mean one crash takes the scheduler with it, and one
job's memory is the scheduler's memory for as long as it runs. A subprocess is
also what makes a timeout enforceable — there is something to kill.

**`python -m <module>`, never a shell.** The module comes from a database row a
UI can edit (§13.2). Passing it to a shell would make the timetable a remote
execution surface for anyone who could write to that table, which is a
considerably larger promise than "you may change when the digest runs".

**Nothing here is its own supervisor.** `restart: unless-stopped` in compose
owns restarts (`P1-30`), the same as every other service — two supervisors
racing to restart one process is how a crash loop goes invisible.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
import time

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.schedule import (
    DEFAULT_LEASE_SECONDS,
    Claim,
    claim_due_job,
    release_claims,
    settle_job,
)

log = get_logger(__name__)

#: How long to wait when nothing is due. Short enough that a job scheduled from
#: the UI starts promptly; long enough that an idle scheduler is not a poll loop
#: against the database.
DEFAULT_POLL_SECONDS = 30

#: A job that has not finished by now is killed. Its lease is longer, so the
#: kill happens before another scheduler could take the job — two copies of a
#: backup running at once is worse than one that was cut short.
DEFAULT_TIMEOUT_SECONDS = 1800


def scheduler_id() -> str:
    return os.environ.get("MERIDIAN_SCHEDULER_ID") or f"{os.uname().nodename}-{os.getpid()}"


class Scheduler:
    def __init__(
        self,
        *,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_jobs: int | None = None,
    ) -> None:
        self.id = scheduler_id()
        self._poll = poll_seconds
        self._timeout = timeout_seconds
        self._lease = lease_seconds
        self._max_jobs = max_jobs
        self._stopping = False
        self.completed = 0

    def stop(self) -> None:
        # The job in flight finishes. A scheduler that killed its own child on
        # SIGTERM would leave a half-written backup, which is the one outcome
        # worse than no backup.
        log.info("scheduler stopping after the current job")
        self._stopping = True

    async def run(self) -> int:
        try:
            while not self._stopping:
                claim = await self._claim()
                if claim is None:
                    if self._max_jobs is not None:
                        break
                    await self._sleep(self._poll)
                    continue

                await self._run_job(claim)
                self.completed += 1
                if self._max_jobs is not None and self.completed >= self._max_jobs:
                    break
        finally:
            async with session("rw") as sess:
                released = await release_claims(sess, self.id)
            if released:
                log.info("released claims on shutdown", extra={"count": released})
            await dispose_engines()
        return self.completed

    async def _claim(self) -> Claim | None:
        async with session("rw") as sess:
            return await claim_due_job(sess, scheduler_id=self.id, lease_seconds=self._lease)

    async def _run_job(self, claim: Claim) -> None:
        started = time.perf_counter()
        status, error = "ok", None

        log.info("scheduled job starting", extra={"job": claim.name, "job_module": claim.module})
        try:
            # `sys.executable -m`, with the arguments as a list. No shell, so
            # nothing in a database row is ever interpreted as one.
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                claim.module,
                *claim.args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
                status, error = "timeout", f"killed after {self._timeout}s"
                output = b""

            if status == "ok" and process.returncode != 0:
                status = "failed"
                # The tail, not the head. A traceback's useful end is the last
                # few lines, and the first few are the harness.
                error = output.decode("utf-8", "replace")[-1500:]
        except Exception as exc:  # noqa: BLE001 - a scheduler must outlive its jobs
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        duration_ms = int((time.perf_counter() - started) * 1000)
        async with session("rw") as sess:
            await settle_job(
                sess, claim.job_id, status=status, duration_ms=duration_ms, error=error
            )

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), timeout=seconds)


def install_signal_handlers(scheduler: Scheduler) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, name), scheduler.stop)


async def run_scheduler(**kwargs) -> int:
    scheduler = Scheduler(**kwargs)
    install_signal_handlers(scheduler)
    log.info("scheduler started", extra={"scheduler_id": scheduler.id})
    return await scheduler.run()


def main() -> None:
    """Entry point: ``python -m worker.scheduler``."""
    parser = argparse.ArgumentParser(description="Run the timetable held in the database.")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="run at most N due jobs and exit; for a bounded check of the timetable",
    )
    args = parser.parse_args()

    configure_logging("scheduler")
    with bind_run_id(f"sched-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_scheduler(
                poll_seconds=args.poll_seconds,
                timeout_seconds=args.timeout_seconds,
                max_jobs=args.max_jobs,
            )
        )


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
