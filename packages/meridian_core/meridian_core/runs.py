"""The orchestrator's state, and how a crash resumes (task `P4-08`, §11.10).

These functions change one `runs` row; the stages do the work. At most one run is
unfinished (a unique partial index), a heartbeat tells a crashed run from a live one,
deferred keeps the stage, stages only move forward, and the mark moves only after the
writes it covers. See docs/features/synthesis.md#run-state.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
from collections.abc import AsyncIterator
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Run

log = get_logger(__name__)

__all__ = [
    "FINAL_STAGE",
    "STAGES",
    "STALE_AFTER",
    "Progress",
    "RunLocked",
    "advance",
    "advancing",
    "beat",
    "begin_or_resume",
    "defer",
    "fail",
    "finish",
    "is_stale",
    "mark",
    "record",
    "unfinished",
]

#: §11.10's stages, in the order a run passes through them. The tuple is the order.
STAGES: Final[tuple[str, ...]] = (
    "pull",
    "extract",
    "tag",
    "score",
    "analogies",
    "gap",
    "seed",
    "done",
)

FINAL_STAGE: Final[str] = STAGES[-1]

#: How long a run may go without completing a step before another orchestrator
#: may take it over. Generous, because one stage can wait minutes on a model.
STALE_AFTER: Final[dt.timedelta] = dt.timedelta(minutes=30)

#: Statuses that mean "this run is not over". Both are resumable; only one of
#: them can have somebody holding it.
UNFINISHED: Final[tuple[str, ...]] = ("running", "deferred")


class RunLocked(RuntimeError):
    """Another orchestrator is working, and recently enough to be believed.

    Not an error to retry through. §13.4's answer to a busy system is to skip
    the cycle, and two orchestrators is worse than none.
    """


def is_stale(run: Run, *, now: dt.datetime, stale_after: dt.timedelta = STALE_AFTER) -> bool:
    """Whether nobody appears to be holding this run.

    A `deferred` run is always stale in this sense: it was put down
    deliberately, so there is nothing to take it away from.
    """
    if run.status == "deferred":
        return True
    if run.heartbeat_at is None:
        return True
    return (now - run.heartbeat_at) > stale_after


async def unfinished(sess: AsyncSession, *, for_update: bool = False) -> Run | None:
    """The run still in flight, if there is one.

    At most one can exist — the index says so — but the query does not assume
    it: `ORDER BY run_id DESC` means a database that somehow holds two returns
    the newer one rather than an arbitrary one.
    """
    stmt = select(Run).where(Run.status.in_(UNFINISHED)).order_by(Run.run_id.desc()).limit(1)
    if for_update:
        stmt = stmt.with_for_update()
    return await sess.scalar(stmt)


async def begin_or_resume(
    sess: AsyncSession,
    *,
    agent_id: str | None = None,
    now: dt.datetime,
    stale_after: dt.timedelta = STALE_AFTER,
) -> tuple[Run, bool]:
    """Take up the unfinished run, or start one. Returns `(run, resumed)`.

    Raises `RunLocked` when a run is `running` and its heartbeat is recent. `FOR UPDATE`
    serialises callers; the unique index catches two that both find no row and insert.
    """
    existing = await unfinished(sess, for_update=True)

    if existing is not None:
        if not is_stale(existing, now=now, stale_after=stale_after):
            raise RunLocked(
                f"run {existing.run_id} is at stage {existing.stage!r} "
                f"and was alive at {existing.heartbeat_at:%Y-%m-%d %H:%M:%SZ}."
            )
        log.info(
            "resuming a run nobody is holding",
            extra={"run": existing.run_id, "stage": existing.stage, "was": existing.status},
        )
        existing.status = "running"
        existing.heartbeat_at = now
        # The error that deferred it is kept until the run finishes: "this run
        # was deferred once and recovered" is a different and more useful fact
        # than a run that never stalled.
        if agent_id is not None:
            existing.agent_id = agent_id
        await sess.flush()
        return existing, True

    # One mark for the system, not one per run (§6.3, `B-36`): a new run starts where
    # the furthest earlier run reached. See docs/features/synthesis.md#run-state.
    reached = await sess.scalar(select(func.max(Run.last_chunk_id)))
    run = Run(
        started_at=now,
        stage=STAGES[0],
        status="running",
        heartbeat_at=now,
        agent_id=agent_id,
        last_chunk_id=reached,
    )
    sess.add(run)
    try:
        await sess.flush()
    except IntegrityError as exc:
        # Another orchestrator inserted between the lock and this statement.
        # The index is the only thing that can catch it, and turning it into
        # the same refusal keeps the caller's handling to one branch.
        raise RunLocked("another orchestrator started a run first.") from exc
    return run, False


async def beat(sess: AsyncSession, run: Run, *, now: dt.datetime) -> None:
    """Say that this run is still working.

    Called as each step completes rather than on a timer: a timer keeps beating
    for a stage that is wedged, which is the thing the heartbeat is supposed to
    make visible.
    """
    run.heartbeat_at = now
    await sess.flush()


async def advance(sess: AsyncSession, run: Run, *, now: dt.datetime) -> str:
    """Move to the next stage, and return it.

    Refuses anything but forward. The stage is a claim about what has already
    been committed, so going back means redoing work that is in the corpus and
    producing duplicates nothing can tell from the originals.
    """
    if run.status != "running":
        raise ValueError(f"run {run.run_id} is {run.status!r}; only a running run advances.")
    if run.stage == FINAL_STAGE:
        raise ValueError(f"run {run.run_id} is at {FINAL_STAGE!r}; there is nothing after it.")

    index = STAGES.index(run.stage) if run.stage in STAGES else -1
    if index < 0:
        raise ValueError(f"run {run.run_id} is at unknown stage {run.stage!r}.")

    run.stage = STAGES[index + 1]
    run.heartbeat_at = now
    await sess.flush()
    return run.stage


async def mark(sess: AsyncSession, run: Run, chunk_id: int, *, now: dt.datetime) -> None:
    """Advance the high-water mark (§6.3, `P4-11`).

    Refuses a mark that would go backwards, and refuses while unflushed writes sit in the
    session: the mark moves only after the writes it covers. Use `advancing()`.
    """
    pending = [obj for obj in (*sess.new, *sess.dirty, *sess.deleted) if obj is not run]
    if pending:
        kinds = sorted({type(obj).__name__ for obj in pending})
        raise ValueError(
            f"the mark for run {run.run_id} cannot advance with unwritten changes "
            f"outstanding ({', '.join(kinds)}). Flush them first: §6.3 advances the "
            "mark only after the writes it covers."
        )
    if run.last_chunk_id is not None and chunk_id < run.last_chunk_id:
        raise ValueError(
            f"the mark for run {run.run_id} would move back from {run.last_chunk_id} to {chunk_id}."
        )
    run.last_chunk_id = chunk_id
    run.heartbeat_at = now
    await sess.flush()


@dataclasses.dataclass
class Progress:
    """How far a stage got, recorded as it goes.

    Separate from the run row on purpose: nothing here touches the database, so
    a stage that dies holding one has changed nothing. The mark moves once, at
    the end, and only if the stage returned.
    """

    #: The furthest chunk whose writes are in this transaction.
    chunk_id: int | None = None

    def reached(self, chunk_id: int) -> None:
        """Note that everything up to `chunk_id` has been written.

        Keeps the highest rather than the latest. A stage that processed a
        batch out of order would otherwise leave the mark behind the work it
        did, and the next run would redo the tail of it.
        """
        self.chunk_id = chunk_id if self.chunk_id is None else max(self.chunk_id, chunk_id)


@contextlib.asynccontextmanager
async def advancing(sess: AsyncSession, run: Run, *, now: dt.datetime) -> AsyncIterator[Progress]:
    """Do the writes, then move the mark — or do neither (§6.3, `P4-11`).

    Used as::

        async with advancing(sess, run, now=now) as progress:
            for chunk in batch:
                ...                          # writes
                await sess.flush()
                progress.reached(chunk.chunk_id)

    An exception leaves the mark where it was; a clean exit marks once, in the same
    transaction as the writes.
    """
    progress = Progress()
    yield progress
    if progress.chunk_id is not None:
        await mark(sess, run, progress.chunk_id, now=now)


async def record(
    sess: AsyncSession,
    run: Run,
    *,
    tokens: int = 0,
    edges: int = 0,
    tags: int = 0,
    seeds: int = 0,
    cost_usd: float | None = None,
) -> None:
    """Add to the run's counters.

    Accumulates rather than assigns; negative deltas are refused.
    """
    for name, value in (("tokens", tokens), ("edges", edges), ("tags", tags), ("seeds", seeds)):
        if value < 0:
            raise ValueError(f"{name} cannot be negative ({value}).")
    if cost_usd is not None and cost_usd < 0:
        raise ValueError(f"cost cannot be negative ({cost_usd}).")

    run.tokens_used += tokens
    run.edges_added += edges
    run.tags_added += tags
    run.seeds_emitted += seeds
    if cost_usd is not None:
        run.cost_usd = (run.cost_usd or 0.0) + cost_usd
    await sess.flush()


async def defer(sess: AsyncSession, run: Run, reason: str) -> None:
    """Put the run down, to be picked up next cycle (§13.4).

    Keeps the stage, leaves `completed_at` empty and clears the heartbeat. Takes no
    `now`: deferring records no time.
    """
    run.status = "deferred"
    run.error = reason
    run.heartbeat_at = None
    await sess.flush()
    log.info("run deferred", extra={"run": run.run_id, "stage": run.stage})


async def fail(sess: AsyncSession, run: Run, error: str, *, now: dt.datetime) -> None:
    """End the run without finishing it.

    Distinct from `defer`: this one is not resumed. The stage is left where it
    stopped, because "it failed during tagging" is the first thing anybody asks.
    """
    run.status = "failed"
    run.error = error
    run.completed_at = now
    run.heartbeat_at = None
    await sess.flush()
    log.warning("run failed", extra={"run": run.run_id, "stage": run.stage})


async def finish(sess: AsyncSession, run: Run, *, now: dt.datetime) -> None:
    """End the run successfully.

    Sets the stage to `done` whatever it was, and clears `error`, which a deferral may
    have left. See docs/features/synthesis.md#run-state.
    """
    run.stage = FINAL_STAGE
    run.status = "done"
    run.completed_at = now
    run.heartbeat_at = None
    run.error = None
    await sess.flush()
