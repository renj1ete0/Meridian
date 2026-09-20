"""The run state machine against a real Postgres (task `P4-08`, §11.10).

What needs a database here is the part the application cannot enforce: at most
one unfinished run, which is a unique partial index rather than a check in
Python, because a check is a race and an index is not.

The rest is resumability — a crash resumes at the stage it reached, a deferred
run is picked up next cycle, and a mark never moves backwards. Every one of
those is only interesting when a row survives between two callers.

**The fixture removes the runs it makes.** A left-behind `running` row would
occupy the only slot the index permits and refuse every later run, in this
suite and in the dev stack.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select

from meridian_core import runs as run_state
from meridian_core.models import Run
from meridian_core.runs import (
    FINAL_STAGE,
    STAGES,
    STALE_AFTER,
    RunLocked,
    advance,
    beat,
    begin_or_resume,
    defer,
    fail,
    finish,
    mark,
    record,
    unfinished,
)

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
async def clean(session_for) -> AsyncIterator:
    """A session with no unfinished run, and none left behind.

    The dev database is a real one and may hold run history; only the rows this
    file creates are removed, and any unfinished row found on the way in is
    settled so the tests start from the state a fresh install has.
    """
    sess = await session_for("rw")
    await sess.rollback()
    before = {row.run_id for row in await sess.scalars(select(Run))}

    # Put any run already in flight aside rather than ending it: the suite must
    # not settle somebody's real run, and the unique index means it cannot
    # simply be left where it is. Its state is written back afterwards.
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


# --------------------------------------------------------------------------
# Starting, and not starting twice
# --------------------------------------------------------------------------


async def test_a_new_run_starts_at_the_first_stage(clean) -> None:
    run, resumed = await begin_or_resume(clean, now=NOW, agent_id="hosted-frontier")

    assert resumed is False
    assert run.stage == STAGES[0]
    assert run.status == "running"
    assert run.heartbeat_at == NOW
    assert run.agent_id == "hosted-frontier"


async def test_a_live_run_refuses_a_second_orchestrator(clean) -> None:
    """Two orchestrators on one corpus means double spend against §11.9's
    ceiling and two sets of writes racing the same high-water mark."""
    await begin_or_resume(clean, now=NOW)

    with pytest.raises(RunLocked, match="pull"):
        await begin_or_resume(clean, now=NOW + dt.timedelta(minutes=1))


async def test_the_database_refuses_a_second_running_row_written_directly(clean) -> None:
    """The index, not the application.

    `begin_or_resume` checks first, but a check is a race: two callers can both
    find nothing and both insert. Only the index closes that, which is why the
    invariant lives there.
    """
    from sqlalchemy.exc import IntegrityError

    await begin_or_resume(clean, now=NOW)
    await clean.commit()

    clean.add(Run(started_at=NOW, stage="pull", status="running"))
    with pytest.raises(IntegrityError):
        await clean.flush()
    await clean.rollback()


# --------------------------------------------------------------------------
# Resuming
# --------------------------------------------------------------------------


async def test_a_crashed_run_is_taken_over_at_the_stage_it_reached(clean) -> None:
    """§11.10's whole claim: "a crash at `stage='tagging'` resumes there".

    Starting fresh instead would redo a day of extraction on every crash, and
    pay for it twice.
    """
    run, _ = await begin_or_resume(clean, now=NOW)
    await advance(clean, run, now=NOW)
    await advance(clean, run, now=NOW)
    assert run.stage == "tag"
    await clean.commit()

    later = NOW + STALE_AFTER + dt.timedelta(minutes=1)
    resumed_run, resumed = await begin_or_resume(clean, now=later)

    assert resumed is True
    assert resumed_run.run_id == run.run_id
    assert resumed_run.stage == "tag"
    assert resumed_run.heartbeat_at == later


async def test_a_run_that_died_before_its_first_beat_is_resumable(clean) -> None:
    # NULL heartbeat: claimed, never worked. The row that most needs taking
    # over is the one that would otherwise look claimed forever.
    run, _ = await begin_or_resume(clean, now=NOW)
    run.heartbeat_at = None
    await clean.flush()

    resumed_run, resumed = await begin_or_resume(clean, now=NOW)

    assert resumed is True
    assert resumed_run.run_id == run.run_id


async def test_a_deferred_run_is_resumed_on_the_next_cycle(clean) -> None:
    """§13.4: "if the model API is unreachable, skip and retry next cycle."

    The stage is kept, so a provider outage costs the rest of the cycle rather
    than the work already done.
    """
    run, _ = await begin_or_resume(clean, now=NOW)
    await advance(clean, run, now=NOW)
    await defer(clean, run, "the provider returned 503")
    await clean.commit()

    resumed_run, resumed = await begin_or_resume(clean, now=NOW + dt.timedelta(minutes=1))

    assert resumed is True
    assert resumed_run.run_id == run.run_id
    assert resumed_run.stage == "extract"
    assert resumed_run.status == "running"


async def test_a_finished_run_does_not_block_the_next_one(clean) -> None:
    first, _ = await begin_or_resume(clean, now=NOW)
    await finish(clean, first, now=NOW)
    await clean.commit()

    second, resumed = await begin_or_resume(clean, now=NOW + dt.timedelta(days=1))

    assert resumed is False
    assert second.run_id != first.run_id


async def test_a_failed_run_does_not_block_the_next_one(clean) -> None:
    first, _ = await begin_or_resume(clean, now=NOW)
    await fail(clean, first, "unrecoverable", now=NOW)
    await clean.commit()

    _, resumed = await begin_or_resume(clean, now=NOW + dt.timedelta(days=1))

    assert resumed is False


# --------------------------------------------------------------------------
# Stages move forward
# --------------------------------------------------------------------------


async def test_a_run_walks_the_stages_in_order(clean) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)

    seen = [run.stage]
    while run.stage != FINAL_STAGE:
        seen.append(await advance(clean, run, now=NOW))

    assert seen == list(STAGES)


async def test_nothing_comes_after_the_last_stage(clean) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)
    while run.stage != FINAL_STAGE:
        await advance(clean, run, now=NOW)

    with pytest.raises(ValueError, match="nothing after"):
        await advance(clean, run, now=NOW)


@pytest.mark.parametrize("ender", ["defer", "fail", "finish"])
async def test_a_run_that_is_not_running_does_not_advance(clean, ender) -> None:
    """The stage is a claim about what has already been committed.

    Advancing a deferred or finished run would move that claim without any work
    behind it, and the next resume would skip the stage entirely.
    """
    run, _ = await begin_or_resume(clean, now=NOW)
    if ender == "defer":
        await defer(clean, run, "later")
    elif ender == "fail":
        await fail(clean, run, "no", now=NOW)
    else:
        await finish(clean, run, now=NOW)

    with pytest.raises(ValueError, match="only a running run"):
        await advance(clean, run, now=NOW)


async def test_advancing_beats(clean) -> None:
    # The heartbeat is touched as steps complete rather than on a timer: a
    # timer keeps beating for a stage that is wedged, which is the thing the
    # heartbeat exists to make visible.
    run, _ = await begin_or_resume(clean, now=NOW)
    later = NOW + dt.timedelta(minutes=5)

    await advance(clean, run, now=later)

    assert run.heartbeat_at == later


# --------------------------------------------------------------------------
# The high-water mark
# --------------------------------------------------------------------------


async def test_the_mark_moves_forward(clean) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)

    await mark(clean, run, 100, now=NOW)
    await mark(clean, run, 250, now=NOW)

    assert run.last_chunk_id == 250


async def test_the_mark_refuses_to_move_backwards(clean) -> None:
    """A mark that went back would re-feed chunks the run has already paid to
    process. Refused rather than clamped: clamping hides the caller bug."""
    run, _ = await begin_or_resume(clean, now=NOW)
    await mark(clean, run, 250, now=NOW)

    with pytest.raises(ValueError, match="move back"):
        await mark(clean, run, 100, now=NOW)

    assert run.last_chunk_id == 250


async def test_the_mark_survives_a_resume(clean) -> None:
    # This is what makes a resume cheaper than a restart.
    run, _ = await begin_or_resume(clean, now=NOW)
    await mark(clean, run, 4242, now=NOW)
    await clean.commit()

    resumed_run, _ = await begin_or_resume(clean, now=NOW + STALE_AFTER * 2)

    assert resumed_run.last_chunk_id == 4242


# --------------------------------------------------------------------------
# Counters
# --------------------------------------------------------------------------


async def test_counters_accumulate_across_stages(clean) -> None:
    """§11.9 compares cost per run week on week. A figure that meant "the last
    stage" in some runs and "all of them" in others would be noise."""
    run, _ = await begin_or_resume(clean, now=NOW)

    await record(clean, run, tokens=1000, edges=5, cost_usd=0.25)
    await record(clean, run, tokens=500, tags=3, seeds=2, cost_usd=0.10)

    assert run.tokens_used == 1500
    assert run.edges_added == 5
    assert run.tags_added == 3
    assert run.seeds_emitted == 2
    assert run.cost_usd == pytest.approx(0.35)


@pytest.mark.parametrize(
    "kwargs",
    [{"tokens": -1}, {"edges": -1}, {"tags": -1}, {"seeds": -1}, {"cost_usd": -0.01}],
)
async def test_a_counter_cannot_go_down(clean, kwargs) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)

    with pytest.raises(ValueError, match="negative"):
        await record(clean, run, **kwargs)


async def test_counters_survive_a_resume(clean) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)
    await record(clean, run, tokens=900, cost_usd=1.5)
    await defer(clean, run, "provider down")
    await clean.commit()

    resumed_run, _ = await begin_or_resume(clean, now=NOW + dt.timedelta(hours=1))
    await record(clean, resumed_run, tokens=100, cost_usd=0.5)

    assert resumed_run.tokens_used == 1000
    assert resumed_run.cost_usd == pytest.approx(2.0)


# --------------------------------------------------------------------------
# Ending
# --------------------------------------------------------------------------


async def test_deferring_keeps_the_stage_and_releases_the_row(clean) -> None:
    run, _ = await begin_or_resume(clean, now=NOW)
    await advance(clean, run, now=NOW)

    await defer(clean, run, "the provider returned 503")

    assert run.status == "deferred"
    assert run.stage == "extract", "the stage is the point of deferring"
    assert run.completed_at is None, "a deferred run has not completed"
    assert run.heartbeat_at is None, "nobody is holding it"
    assert "503" in (run.error or "")


async def test_failing_leaves_the_stage_where_it_stopped(clean) -> None:
    # "It failed during tagging" is the first thing anybody asks.
    run, _ = await begin_or_resume(clean, now=NOW)
    await advance(clean, run, now=NOW)
    await advance(clean, run, now=NOW)

    await fail(clean, run, "schema validation rejected every edge", now=NOW)

    assert run.stage == "tag"
    assert run.status == "failed"
    assert run.completed_at == NOW


async def test_a_run_with_nothing_to_do_can_finish_from_the_first_stage(clean) -> None:
    """Otherwise an empty day leaves an unfinished run in the only slot the
    index permits, and every later run is refused."""
    run, _ = await begin_or_resume(clean, now=NOW)

    await finish(clean, run, now=NOW)

    assert run.stage == FINAL_STAGE
    assert run.status == "done"
    assert await unfinished(clean) is None


async def test_the_module_exports_what_an_orchestrator_needs(clean) -> None:
    # Drift: a helper added here and forgotten in `__all__` is one a caller
    # importing the module by name cannot find.
    for name in run_state.__all__:
        assert hasattr(run_state, name), name
