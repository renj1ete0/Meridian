"""One synthesis cycle, and a way to run it without letting it write
(task `P4-09`, §11.10, §6.3).

`P4-08` moves a run through its stages. This is the thing that calls it, and
the two flags §11.10 asks for: `--dry-run`, which applies nothing, and `--once`,
which stops after a single cycle.

**Here rather than in `services/orchestrator/`.** The scaffold reserves a
service for this and the compose file gates it behind `phase4` because its
Dockerfile does not exist. Creating that service now would mean a Dockerfile, a
compose entry, a release-script line and a healthcheck for a process whose
stages are not built — and `docs/handover.md` already records what an empty
profiled service costs. The worker image runs `python -m worker.<x>` entry
points already and has every dependency; when the stages land, moving this
module is a rename.

**`--dry-run` is a rolled-back transaction, not a promise.** A flag that each
stage had to remember to check is a flag one stage will forget, and the way you
find out is that a dry run wrote something. Here the whole cycle runs inside a
transaction that is rolled back unconditionally, so a stage that writes without
asking still writes nothing. The journal is what makes the run legible: every
intended call is recorded and printed whether or not it was applied.

**A cycle that changes nothing stops the loop.** Without that, an orchestrator
whose stages are all unbuilt — which is every orchestrator today — would find
work past the high-water mark, fail to advance it, find the same work, and spin
until something killed it. The loop's exit condition is progress, not emptiness.

**The stages are named, not absent.** Each one says which task builds it, for
the same reason `worker/commands.py` refuses by name: "there is no tagging
stage" and "the tagging stage did nothing" are indistinguishable from a log,
and only one of them is true.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import time
from collections.abc import AsyncIterator
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, get_sessionmaker
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Run
from meridian_core.runs import (
    FINAL_STAGE,
    STAGES,
    RunLocked,
    advance,
    advancing,
    begin_or_resume,
    finish,
)

log = get_logger(__name__)

__all__ = [
    "BUILT_BY",
    "Journal",
    "cycle",
    "pending_work",
    "run_orchestrator",
    "step",
]

#: Which task builds each stage. Named rather than omitted: a stage missing
#: from a log reads as a stage that ran and found nothing, and the two want
#: entirely different responses.
#:
#: `done` is absent because it is not work — it is the state of having finished.
BUILT_BY: Final[dict[str, str]] = {
    "pull": "`P4-04`'s read side — nothing selects the chunks a run reasons over yet",
    "extract": "`P4-04`'s `add_edge` — relation extraction has nowhere to write",
    "tag": "`P4-04`'s `tag_entity`, and `P7-01`'s proposal gate",
    "score": "`P5-03`'s coverage scoring, which is schema-aware",
    "analogies": "`P7-04`'s analogical expansion",
    "gap": "`P5-04`'s gap analysis",
    "seed": "`P5-04`'s seed emission, capped and validated",
}


@dataclasses.dataclass
class Journal:
    """What the cycle intended to do, in the order it intended to do it.

    Kept even on a real run. §11.9 wants cost per run compared week on week,
    and a run whose stages are only visible as row counts cannot be compared
    with one whose stages refused for different reasons.
    """

    dry_run: bool = False
    entries: list[str] = dataclasses.field(default_factory=list)

    def note(self, stage: str, message: str) -> None:
        self.entries.append(f"{stage:<9} {message}")

    def call(self, tool: str, **params: object) -> None:
        """Record an intended tool call.

        The write itself is the caller's; this only records that it was asked
        for. On a dry run the transaction is rolled back around it, so the two
        stay consistent without the caller knowing which mode it is in.
        """
        shown = ", ".join(f"{k}={v!r}" for k, v in params.items())
        self.entries.append(f"{'call':<9} {tool}({shown})")

    def render(self) -> str:
        head = "would do (nothing was written):" if self.dry_run else "did:"
        if not self.entries:
            return f"{head}\n  (nothing)"
        return head + "\n" + "\n".join(f"  {line}" for line in self.entries)


@contextlib.asynccontextmanager
async def _scope(*, dry_run: bool) -> AsyncIterator[AsyncSession]:
    """A session that commits, or one that cannot.

    Deliberately not a flag threaded through every stage: the guarantee has to
    hold for a stage whose author did not think about dry runs, and the only
    version of that guarantee which does is a transaction nobody can commit.
    """
    async with get_sessionmaker("rw")() as sess:
        try:
            yield sess
            if dry_run:
                await sess.rollback()
            else:
                await sess.commit()
        except Exception:
            await sess.rollback()
            raise


async def pending_work(sess: AsyncSession, run: Run) -> int:
    """How many chunks sit past the high-water mark (§6.3).

    The mark is per-run state but the question is about the corpus, so a fresh
    run with no mark asks about everything — which is the correct answer for
    the first run a deployment ever does.
    """
    stmt = select(func.count()).select_from(Chunk)
    if run.last_chunk_id is not None:
        stmt = stmt.where(Chunk.chunk_id > run.last_chunk_id)
    return int(await sess.scalar(stmt) or 0)


async def step(sess: AsyncSession, run: Run, *, journal: Journal, now: dt.datetime) -> str:
    """Do the current stage, then move to the next. Returns the new stage.

    Every stage is presently a refusal that names its task. The structure is
    the deliverable: a stage that lands drops in here and inherits the
    resumability, the heartbeat and the dry-run guarantee without restating
    any of them.
    """
    stage = run.stage or STAGES[0]
    # The window is where a stage goes. Inside it, writes happen and
    # `progress.reached(chunk_id)` records how far they got; on the way out the
    # mark moves once, after them (§6.3, `P4-11`). A stage that lands here
    # inherits that ordering without restating it — and one that raises leaves
    # the mark exactly where it was.
    async with advancing(sess, run, now=now) as progress:
        journal.note(stage, f"not built — needs {BUILT_BY.get(stage, 'nothing (it is the end)')}")
        del progress  # nothing has been written, so nothing is marked
    return await advance(sess, run, now=now)


async def cycle(
    sess: AsyncSession,
    *,
    journal: Journal,
    now: dt.datetime,
    agent_id: str | None = None,
    stop_after: str | None = None,
) -> Run:
    """Begin or resume a run and walk it to the end, or to `stop_after`.

    `stop_after` leaves the run **unfinished on purpose**, which is the point:
    it stays resumable, so the next call continues from there rather than
    starting again. That is the only way to step through a run by hand without
    the state machine treating each step as a fresh crash.
    """
    if stop_after is not None and stop_after not in STAGES:
        raise ValueError(f"{stop_after!r} is not a stage. Known: {', '.join(STAGES)}")

    run, resumed = await begin_or_resume(sess, agent_id=agent_id, now=now)
    journal.note("run", f"{'resumed' if resumed else 'started'} run {run.run_id} at {run.stage}")

    waiting = await pending_work(sess, run)
    journal.note("run", f"{waiting:,} chunks past the mark")

    while run.stage != FINAL_STAGE:
        reached = run.stage
        await step(sess, run, journal=journal, now=now)
        if stop_after is not None and reached == stop_after:
            journal.note("run", f"stopping after {reached}; the run stays resumable")
            return run

    await finish(sess, run, now=now)
    journal.note("run", f"finished run {run.run_id}")
    return run


async def run_orchestrator(
    *,
    dry_run: bool = False,
    once: bool = False,
    stop_after: str | None = None,
    max_cycles: int = 10,
    agent_id: str | None = None,
    now: dt.datetime | None = None,
) -> Journal:
    """Run cycles while there is work and each one makes progress.

    **Progress, not emptiness, is the exit condition.** A cycle that advanced
    neither the high-water mark nor any counter will do exactly the same thing
    next time, so continuing would spin — and an orchestrator spinning on a
    daily timer looks, from the outside, like one that is busy.
    """
    journal = Journal(dry_run=dry_run)
    moment = now or dt.datetime.now(dt.UTC)

    await _cycles(
        journal,
        dry_run=dry_run,
        once=once,
        stop_after=stop_after,
        max_cycles=max_cycles,
        agent_id=agent_id,
        moment=moment,
    )
    return journal


async def _cycles(
    journal: Journal,
    *,
    dry_run: bool,
    once: bool,
    stop_after: str | None,
    max_cycles: int,
    agent_id: str | None,
    moment: dt.datetime,
) -> None:
    for iteration in range(max_cycles):
        async with _scope(dry_run=dry_run) as sess:
            try:
                run = await cycle(
                    sess, journal=journal, now=moment, agent_id=agent_id, stop_after=stop_after
                )
            except RunLocked as exc:
                # §13.4's answer to a busy system is to skip the cycle. Two
                # orchestrators is worse than none.
                journal.note("run", f"another orchestrator holds the run: {exc}")
                break

            progressed = run.last_chunk_id is not None or run.edges_added or run.tags_added
            waiting = await pending_work(sess, run)

        if once or stop_after is not None:
            break
        if not waiting:
            journal.note("run", "nothing past the mark; stopping")
            break
        if not progressed:
            journal.note(
                "run",
                f"cycle {iteration + 1} changed nothing while {waiting:,} chunks wait; stopping",
            )
            break


async def _main(args: argparse.Namespace) -> Journal:
    """Run, then close the pool — both inside one event loop.

    The disposal belongs here rather than in `run_orchestrator`: a library
    function that tears down the process's shared engines is a surprise to
    anybody who calls it twice. It also has to happen in the loop that created
    them — a second `asyncio.run` is a second loop, and disposing from it fails
    at exit, after the work, in a traceback that says nothing about the cause.
    """
    try:
        return await run_orchestrator(
            dry_run=args.dry_run,
            once=args.once,
            stop_after=args.stop_after,
            max_cycles=args.max_cycles,
            agent_id=args.agent,
        )
    finally:
        await dispose_engines()


def main() -> None:
    """Entry point: ``python -m worker.orchestrate``."""
    parser = argparse.ArgumentParser(description="§11.10's synthesis cycle.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would happen and write nothing. The transaction is rolled back.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single cycle and exit, rather than continuing while work remains",
    )
    parser.add_argument(
        "--stop-after",
        metavar="STAGE",
        default=None,
        help=(
            f"stop once this stage is done, leaving the run resumable. One of: {', '.join(STAGES)}"
        ),
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=10,
        help="a ceiling on cycles per invocation, so a loop cannot run away unattended",
    )
    parser.add_argument("--agent", default=None, help="record this agent on the run")
    args = parser.parse_args()

    configure_logging("orchestrate")
    with bind_run_id(f"orch-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        journal = asyncio.run(_main(args))
        print(journal.render())


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
