"""Run state, without a database (task `P4-08`, §11.10).

Two things can be decided from the constants alone, and both are the kind that
rot quietly: whether the stage list still matches what the column will accept,
and whether "nobody is holding this run" means what it should.

The staleness rule is the one worth reading carefully. A window that is too
short hands a live run to a second orchestrator; one that never fires leaves a
crashed run holding the only slot the unique index permits, and every later run
is refused.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from meridian_core.models import Run
from meridian_core.models.runs import RUN_STAGE, RUN_STATUS
from meridian_core.runs import (
    FINAL_STAGE,
    STAGES,
    STALE_AFTER,
    UNFINISHED,
    Progress,
    is_stale,
)

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)


def run(**kwargs) -> Run:
    defaults = {"run_id": 1, "stage": "pull", "status": "running", "heartbeat_at": NOW}
    return Run(**{**defaults, **kwargs})


# --------------------------------------------------------------------------
# Drift against the column
# --------------------------------------------------------------------------


def test_the_stage_order_covers_exactly_what_the_column_accepts() -> None:
    """A stage in the tuple that the CHECK refuses fails at write time; a stage
    the column accepts that the tuple omits is one `advance` can never reach.

    Both are silent until a run actually gets there, which on a daily cycle is
    a day later, in an unattended process.
    """
    assert set(STAGES) == set(RUN_STAGE.enums)
    assert len(STAGES) == len(set(STAGES)), "a repeated stage makes `advance` a loop"


def test_the_last_stage_is_the_one_that_means_finished() -> None:
    assert FINAL_STAGE == STAGES[-1] == "done"


def test_the_unfinished_statuses_are_real_statuses() -> None:
    # `UNFINISHED` drives the query that finds a run to resume. A value the
    # column cannot hold would make that query quietly match nothing.
    assert set(UNFINISHED) <= set(RUN_STATUS.enums)
    assert "done" not in UNFINISHED and "failed" not in UNFINISHED


# --------------------------------------------------------------------------
# Whether anybody is holding the run
# --------------------------------------------------------------------------


def test_a_run_that_never_beat_is_stale() -> None:
    """NULL is the state of a run that died before completing a step — and of
    every row written before the heartbeat column existed. Reading it as "alive"
    would leave the single slot occupied forever."""
    assert is_stale(run(heartbeat_at=None), now=NOW)


def test_a_run_that_beat_just_now_is_not_stale() -> None:
    assert not is_stale(run(heartbeat_at=NOW), now=NOW)


def test_a_run_inside_the_window_is_not_stale() -> None:
    # A stage can legitimately spend minutes waiting on a frontier model, and a
    # window that fired during normal work would hand the row to a second
    # process while the first was still using it.
    assert not is_stale(run(heartbeat_at=NOW - STALE_AFTER + dt.timedelta(seconds=1)), now=NOW)


def test_a_run_past_the_window_is_stale() -> None:
    assert is_stale(run(heartbeat_at=NOW - STALE_AFTER - dt.timedelta(seconds=1)), now=NOW)


def test_a_deferred_run_is_always_available_however_recently_it_beat() -> None:
    """§13.4 defers a run so the next cycle picks it up. It was put down
    deliberately, so there is nothing to take it away from — and a deferred run
    that still looked held would never be resumed at all."""
    assert is_stale(run(status="deferred", heartbeat_at=NOW), now=NOW)


def test_the_window_is_long_enough_to_survive_a_slow_stage() -> None:
    # Not an arbitrary number: it has to exceed the slowest legitimate step, and
    # a frontier model over a large batch is minutes rather than seconds.
    assert STALE_AFTER >= dt.timedelta(minutes=15)


@pytest.mark.parametrize("status", ["done", "failed"])
def test_staleness_says_nothing_about_a_finished_run(status) -> None:
    # A finished run is not resumable at all; `unfinished()` never returns one,
    # so `is_stale` is never asked about it. Asserted so that a future caller
    # reading `is_stale(run)` as "may be taken over" is reading it correctly.
    assert status not in UNFINISHED


# --------------------------------------------------------------------------
# How far a stage got (task `P4-11`)
# --------------------------------------------------------------------------


def test_progress_starts_with_nothing_to_mark() -> None:
    # A stage that wrote nothing must not move the mark. `None` is what makes
    # that distinguishable from "reached chunk 0".
    assert Progress().chunk_id is None


def test_progress_keeps_the_highest_not_the_latest() -> None:
    """A batch processed out of order would otherwise leave the mark behind
    the work, and the next run would redo the tail of it."""
    progress = Progress()

    progress.reached(500)
    progress.reached(200)

    assert progress.chunk_id == 500


def test_progress_moves_forward_as_a_stage_works() -> None:
    progress = Progress()

    for chunk_id in (10, 20, 30):
        progress.reached(chunk_id)

    assert progress.chunk_id == 30


def test_progress_touches_nothing_outside_itself() -> None:
    # Deliberately inert: a stage that dies holding one has changed nothing,
    # which is what lets the mark move exactly once, at the end.
    progress = Progress()
    progress.reached(1)

    assert dataclasses.fields(progress)[0].name == "chunk_id"
    assert len(dataclasses.fields(progress)) == 1
