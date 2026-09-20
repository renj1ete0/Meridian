"""A synthesis cycle against a real Postgres (task `P4-09`, §11.10).

The claim worth testing here is the one a flag cannot make on its own: that
`--dry-run` writes nothing **even when a stage tries to**. A mode each stage
had to remember to honour is a mode one stage will forget, and the way that is
discovered is by a dry run leaving something behind.

So the test that matters most injects a stage that writes without asking, runs
the cycle dry, and looks for what it wrote.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, func, select

from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, Run
from meridian_core.runs import FINAL_STAGE, STAGES, begin_or_resume, unfinished
from worker import orchestrate
from worker.orchestrate import Journal, cycle, pending_work, run_orchestrator

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
async def clean(session_for) -> AsyncIterator:
    """No unfinished run on the way in, and none of ours left behind.

    **The shared engines are disposed at both ends.** `run_orchestrator` opens
    its own sessions from `meridian_core.db`'s process-wide sessionmaker, which
    caches an engine bound to the event loop that created it — and every test
    gets a new loop. Without this, the second test in the file inherits the
    first one's engine and fails inside asyncpg with a message about a future
    attached to a different loop, which says nothing about the cause.
    """
    await dispose_engines()
    sess = await session_for("rw")
    await sess.rollback()
    before = {row.run_id for row in await sess.scalars(select(Run))}

    stranded = await unfinished(sess)
    held = None
    if stranded is not None:
        held = (stranded.run_id, stranded.status, stranded.stage, stranded.heartbeat_at)
        stranded.status = "failed"
        stranded.error = "set aside by the test suite"
    await sess.commit()

    yield sess

    await sess.rollback()
    await sess.execute(delete(Run).where(Run.run_id.notin_(before or {-1})))
    if held is not None:
        run_id, status, stage, heartbeat = held
        row = await sess.get(Run, run_id)
        row.status, row.stage, row.heartbeat_at, row.error = status, stage, heartbeat, None
    await sess.commit()
    await dispose_engines()


async def run_count(sess) -> int:
    await sess.rollback()
    return int(await sess.scalar(select(func.count()).select_from(Run)) or 0)


# --------------------------------------------------------------------------
# A cycle, end to end
# --------------------------------------------------------------------------


async def test_a_cycle_walks_every_stage_and_finishes(clean) -> None:
    journal = Journal()

    run = await cycle(clean, journal=journal, now=NOW)

    assert run.stage == FINAL_STAGE
    assert run.status == "done"
    for stage in STAGES[:-1]:
        assert any(line.startswith(stage) for line in journal.entries), stage


async def test_the_journal_names_the_run_it_worked_on(clean) -> None:
    # A journal that did not say which run it described would be unusable the
    # moment two of them appeared in one log.
    journal = Journal()

    run = await cycle(clean, journal=journal, now=NOW)

    assert any(str(run.run_id) in line for line in journal.entries)


async def test_pending_work_counts_everything_before_the_first_mark(clean) -> None:
    """A run with no mark asks about the whole corpus, which is the right
    answer for the first run a deployment ever does."""
    run, _ = await begin_or_resume(clean, now=NOW)
    total = int(await clean.scalar(select(func.count()).select_from(Chunk)) or 0)

    assert await pending_work(clean, run) == total


async def test_pending_work_counts_only_what_is_past_the_mark(clean) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)
    highest = await clean.scalar(select(func.max(Chunk.chunk_id)))
    if highest is None:
        pytest.skip("the dev corpus holds no chunks to mark against")

    run.last_chunk_id = highest
    await clean.flush()

    assert await pending_work(clean, run) == 0


# --------------------------------------------------------------------------
# --dry-run
# --------------------------------------------------------------------------


async def test_a_dry_run_leaves_no_run_behind(clean) -> None:
    before = await run_count(clean)

    journal = await run_orchestrator(dry_run=True, now=NOW)

    assert journal.dry_run
    assert await run_count(clean) == before


async def test_a_dry_run_writes_nothing_even_when_a_stage_tries(clean, monkeypatch) -> None:
    """The guarantee is a rolled-back transaction, not each stage's good manners.

    A stage added next year by somebody who never read this file still writes
    nothing on a dry run — which is the only version of the promise that
    survives the code growing.
    """
    written: list[int] = []

    async def writing_step(sess, run, *, journal, now):
        row = Run(started_at=now, stage="pull", status="failed", error="written by a rogue stage")
        sess.add(row)
        await sess.flush()
        written.append(row.run_id)
        journal.note(run.stage or "?", "wrote a row without asking")
        return await orchestrate.advance(sess, run, now=now)

    monkeypatch.setattr(orchestrate, "step", writing_step)
    before = await run_count(clean)

    await run_orchestrator(dry_run=True, now=NOW)

    assert written, "the injected stage did not run; the test proves nothing"
    assert await run_count(clean) == before
    assert await clean.get(Run, written[0]) is None


async def test_a_real_run_does_leave_a_row(clean) -> None:
    # The other half: if the dry-run test passed because nothing ran at all,
    # this one fails.
    before = await run_count(clean)

    await run_orchestrator(dry_run=False, once=True, now=NOW)

    assert await run_count(clean) == before + 1


# --------------------------------------------------------------------------
# --stop-after, and resuming
# --------------------------------------------------------------------------


async def test_stopping_after_a_stage_leaves_the_run_resumable(clean) -> None:
    """Unfinished on purpose. That is what makes stepping through a run
    possible without the state machine reading each step as a fresh crash."""
    await run_orchestrator(stop_after="extract", now=NOW)

    await clean.rollback()
    waiting = await unfinished(clean)
    assert waiting is not None
    assert waiting.stage == "tag", "the stage after the one it stopped on"
    assert waiting.status == "running"


async def test_a_second_invocation_continues_rather_than_starting_over(clean) -> None:
    await run_orchestrator(stop_after="extract", now=NOW)
    await clean.rollback()
    waiting = await unfinished(clean)
    # Read now, as a plain int: the row is expired by the rollback below, and
    # refreshing it afterwards would be IO from outside the session's context.
    first_id = waiting.run_id

    # Far enough ahead that the heartbeat has gone stale and the run is free.
    later = NOW + dt.timedelta(hours=2)
    journal = await run_orchestrator(now=later)

    await clean.rollback()
    assert await unfinished(clean) is None, "the second invocation finished it"
    assert any(f"resumed run {first_id}" in line for line in journal.entries)


async def test_a_live_run_is_reported_rather_than_fought_over(clean) -> None:
    """§13.4's answer to a busy system is to skip the cycle.

    Raising here would make a scheduled job fail and alert, which trains
    somebody to ignore the channel — and two orchestrators is worse than none.
    """
    await begin_or_resume(clean, now=NOW)
    await clean.commit()

    journal = await run_orchestrator(now=NOW + dt.timedelta(minutes=1))

    assert any("another orchestrator holds the run" in line for line in journal.entries)


# --------------------------------------------------------------------------
# The loop stops
# --------------------------------------------------------------------------


async def test_a_cycle_that_changes_nothing_stops_the_loop(clean) -> None:
    """Otherwise an orchestrator whose stages are unbuilt — which is every
    orchestrator today — finds work, fails to advance the mark, finds the same
    work, and spins until something kills it. From outside, that looks busy.
    """
    total = int(await clean.scalar(select(func.count()).select_from(Chunk)) or 0)
    if total == 0:
        pytest.skip("with no chunks the loop stops for a different reason")

    journal = await run_orchestrator(dry_run=True, max_cycles=5, now=NOW)

    assert any("changed nothing" in line for line in journal.entries)
    assert sum(1 for line in journal.entries if "started run" in line) == 1


async def test_once_stops_after_a_single_cycle(clean) -> None:
    journal = await run_orchestrator(dry_run=True, once=True, max_cycles=5, now=NOW)

    assert sum(1 for line in journal.entries if "started run" in line) == 1
    assert not any("changed nothing" in line for line in journal.entries), (
        "--once stops before the progress check has anything to say"
    )


async def test_max_cycles_is_a_ceiling_nothing_can_exceed(clean) -> None:
    # The last line of defence for an unattended process: whatever the exit
    # conditions do, the invocation ends.
    journal = await run_orchestrator(dry_run=True, max_cycles=1, now=NOW)

    assert sum(1 for line in journal.entries if "started run" in line) == 1
