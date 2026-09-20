"""The orchestrator's state, and how a crash resumes (task `P4-08`, §11.10).

§11.10 is a rejection of workflow frameworks: "a crash at `stage='tagging'`
resumes there on the next wake… in roughly 200 lines, with no abstraction layer
between the orchestrator and its validated tool calls." The `runs` table has
existed since `P0-07`. This is the part that moves a row through it.

**Nothing here does the work.** These functions change one row. Which chunks a
stage reads, which model it asks and what it writes are the stages' own
business — and keeping them apart is what lets the resumability rules be tested
without a model, a corpus, or a stage that exists yet.

Four decisions, each with a failure it exists to prevent:

**At most one unfinished run, enforced by the database.** Two orchestrators on
one corpus means double spend against §11.9's monthly ceiling and two sets of
writes racing the same high-water mark. A unique partial index makes a second
one an error rather than a quiet second run — the application could check
first, but a check is a race and an index is not.

**A heartbeat, because a crashed run and a live one look the same.** Both are
`status='running'` with a stage. Without something that decays, a resume would
either never happen (the row looks claimed forever) or happen alongside the run
it was meant to replace. NULL reads as stale: a run that died before completing
a step is the one that most needs taking over.

**Deferred is not failed.** §13.4 wants a deferred run when the model API is
unreachable — "skip and retry next cycle. Ingestion continues regardless." The
stage is kept, so the next wake continues rather than restarting, and a
provider outage costs synthesis rather than the day's crawl.

**Stages only move forward.** The stage is a claim about what has already been
committed, so moving back would redo work that is already in the corpus — and
the duplicates would be indistinguishable from the originals. `advance` refuses
anything but the next stage, and the high-water mark refuses to go backwards
for the same reason.

**The mark advances after the writes, never before** (§6.3, `P4-11`). Marking
first and writing second loses those chunks permanently if anything fails in
between: nothing would be missing from the corpus, only from the reasoning over
it, so there is nothing to notice. `advancing()` does the ordering, and `mark()`
refuses outright while unwritten changes are still sitting in the session.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
from collections.abc import AsyncIterator
from typing import Final

from sqlalchemy import select
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

#: §11.10's stages, in the order a run passes through them. The tuple *is* the
#: order — a set would leave "what comes after tagging" to be re-decided at
#: every call site, and two call sites deciding differently is a run that skips
#: a stage without anything noticing.
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
#: may take it over. Generous on purpose: a single stage can legitimately spend
#: minutes waiting on a frontier model, and a window that fired during normal
#: work would hand the row to a second process while the first was still using
#: it — which is the exact failure the heartbeat exists to prevent.
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

    **Resuming is the default, not the exception.** A wake that started a fresh
    run whenever it found one in progress would redo a whole day of extraction
    on every crash, and pay for it twice.

    Raises `RunLocked` when a run is `running` and its heartbeat is recent.
    `FOR UPDATE` serialises two callers that arrive together, and the unique
    index catches the one case the lock cannot — two callers finding no row at
    all and both inserting.
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

    run = Run(
        started_at=now,
        stage=STAGES[0],
        status="running",
        heartbeat_at=now,
        agent_id=agent_id,
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

    **Monotonic, and refuses rather than clamps.** A mark that went backwards
    would re-feed chunks the run has already paid to process; one that silently
    clamped would hide the caller bug that sent it.

    **It refuses while writes are still pending.** §6.3's rule is that the mark
    advances only after the writes it covers, and the way that rule gets broken
    is by marking first and writing second — at which point anything failing in
    between loses those chunks permanently, because the mark says they were
    handled. Unflushed objects in the session are exactly that state and are
    visible from here, so this is a check rather than a convention.
    `advancing()` is the supported way and does the ordering for you.
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

    **The ordering is the whole point.** A mark that advanced before its writes
    landed would tell the next run those chunks were handled, and anything
    failing in between loses them permanently — silently, because nothing is
    missing from the corpus, only from the reasoning over it.

    Used as::

        async with advancing(sess, run, now=now) as progress:
            for chunk in batch:
                ...                          # writes
                await sess.flush()
                progress.reached(chunk.chunk_id)

    An exception leaves the mark exactly where it was, and the caller's
    transaction discards the writes with it. A clean exit marks once — and
    because it is the same transaction, the writes and the mark commit together
    or not at all, which is stronger than the rule asks for.
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

    **Accumulates rather than assigns**, for the reason `budget.py` gives: a
    per-run figure §11.9 compares week on week would otherwise mean "the last
    stage" in some runs and "all of them" in others, and the trend would be
    noise. Negative deltas are refused — a counter that can go down cannot be
    compared to anything.
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

    The stage is kept — that is the whole point — and `completed_at` stays
    empty, because the run has not completed. The heartbeat is cleared: nobody
    is holding it, and leaving a recent one would make the next wake believe
    somebody was.

    No `now`, unlike the rest of this module. Deferring records no timestamp:
    the run did not complete, and nothing here should imply a moment that is
    not stored. Taking the argument for symmetry would be an invitation to
    believe it was.
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

    Sets the stage to `done` whatever it was. A run with nothing to process is
    finished at `pull`, and refusing to close it would leave the table holding
    an unfinished run forever — which the unique index would then read as
    "somebody is working", blocking every later run.
    """
    run.stage = FINAL_STAGE
    run.status = "done"
    run.completed_at = now
    run.heartbeat_at = None
    await sess.flush()
