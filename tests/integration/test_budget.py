"""Caps for a run, and where they come from (task `P4-10`, §16).

Against a real Postgres, because two of the three things worth proving are
database behaviour rather than Python: the single-row CHECK that stops a second
budget existing, and the `SELECT ... FOR UPDATE` that stops two concurrent tool
calls both fitting under the same cap. Neither survives a mock.

The third is the one this task exists for, and it is a *rejection* test in the
sense §3 of AGENTS.md means: that an absent cap refuses. Everything about this
module is arranged so that forgetting to configure something stops the run, and
the way that regresses is somebody adding a sensible-looking default.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from meridian_core.budget import (
    BUDGET_ID,
    BudgetError,
    load_budget,
    month_to_date_cost,
    month_window,
    record_run_spend,
    reserve_tokens,
)
from meridian_core.models import BudgetConfig, Run

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
async def clean(session_for):
    """A session with no budget row and no runs in the window under test."""
    sess = await session_for("rw")
    await sess.execute(delete(BudgetConfig))
    await sess.execute(delete(Run).where(Run.agent_id.like("test-budget%")))
    await sess.flush()
    return sess


async def a_budget(sess, *, tokens=100_000, seeds=50, ceiling=25.0) -> BudgetConfig:
    row = BudgetConfig(
        budget_id=BUDGET_ID,
        max_tokens_per_run=tokens,
        max_seeds_per_run=seeds,
        monthly_cost_ceiling_usd=ceiling,
    )
    sess.add(row)
    await sess.flush()
    return row


async def a_run(sess, *, cost=None, started_at=NOW, status="running", tokens=0) -> Run:
    run = Run(
        started_at=started_at,
        status=status,
        agent_id="test-budget",
        cost_usd=cost,
        tokens_used=tokens,
    )
    sess.add(run)
    await sess.flush()
    return run


# --------------------------------------------------------------------------
# One row, enforced by the database


async def test_a_second_budget_row_is_refused_by_the_database(clean) -> None:
    """Not by convention. A settings table that can hold two rows eventually
    does, and then "the budget" is whichever one the query ordered first."""
    await a_budget(clean)

    clean.add(BudgetConfig(budget_id=2, max_tokens_per_run=1))

    with pytest.raises(IntegrityError):
        await clean.flush()
    await clean.rollback()


async def test_a_nonpositive_cap_is_refused_by_the_database(clean) -> None:
    """A cap of zero is a typo that would otherwise read as a very strict
    limit, and the failure would look like a broken orchestrator."""
    clean.add(BudgetConfig(budget_id=BUDGET_ID, max_seeds_per_run=0))

    with pytest.raises(IntegrityError):
        await clean.flush()
    await clean.rollback()


# --------------------------------------------------------------------------
# Token reservation


async def test_tokens_are_refused_without_a_cap(clean) -> None:
    """The same position `reserve_seeds` takes: unconfigured is not unlimited."""
    run = await a_run(clean)

    with pytest.raises(BudgetError) as raised:
        await reserve_tokens(clean, run.run_id, 10, cap=None)

    assert raised.value.reason == "token_cap"


async def test_a_reservation_that_would_exceed_the_cap_takes_nothing(clean) -> None:
    """All or nothing. A partially admitted batch would make the cap depend on
    the order the caller happened to ask in."""
    run = await a_run(clean, tokens=90)

    with pytest.raises(BudgetError):
        await reserve_tokens(clean, run.run_id, 20, cap=100)

    await clean.refresh(run)
    assert run.tokens_used == 90, "a refused reservation still spent tokens"


async def test_a_reservation_reports_what_is_left(clean) -> None:
    """So a caller can stop before it is refused — a model that hits the cap
    mid-answer has wasted what it already spent on that answer."""
    run = await a_run(clean, tokens=0)

    assert await reserve_tokens(clean, run.run_id, 40, cap=100) == 60
    assert await reserve_tokens(clean, run.run_id, 60, cap=100) == 0


async def test_a_finished_run_cannot_spend_more(clean) -> None:
    """A write arriving after the run completed is a crashed worker resuming
    without reading state, or a replayed token. Neither should extend spend."""
    run = await a_run(clean, status="done")

    with pytest.raises(BudgetError) as raised:
        await reserve_tokens(clean, run.run_id, 1, cap=100)

    assert raised.value.reason == "run_open"


# --------------------------------------------------------------------------
# Cost


async def test_spend_accumulates_rather_than_overwriting(clean) -> None:
    """A run makes many calls to many models. A setter would record whichever
    one wrote last, and the per-run figure §11.9 compares week on week would
    mean different things in different runs."""
    run = await a_run(clean, cost=None)

    await record_run_spend(clean, run.run_id, cost_usd=1.5)
    total = await record_run_spend(clean, run.run_id, cost_usd=2.25)

    assert total == pytest.approx(3.75)


async def test_a_run_with_no_recorded_cost_does_not_blank_the_month(clean) -> None:
    """`coalesce`, not `sum`: a run predating cost logging should contribute
    zero rather than making the month's total null."""
    await a_run(clean, cost=None, status="done")
    await a_run(clean, cost=4.0, status="done")

    assert await month_to_date_cost(clean, now=NOW) == pytest.approx(4.0)


async def test_negative_spend_is_refused(clean) -> None:
    run = await a_run(clean)

    with pytest.raises(BudgetError):
        await record_run_spend(clean, run.run_id, cost_usd=-1.0)


# --------------------------------------------------------------------------
# The window itself


@pytest.mark.parametrize(
    "moment,expected_start,expected_end",
    [
        (dt.datetime(2026, 12, 31, 23, 59, tzinfo=dt.UTC), "2026-12-01", "2027-01-01"),
        (dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC), "2026-01-01", "2026-02-01"),
        (dt.datetime(2024, 2, 29, 12, 0, tzinfo=dt.UTC), "2024-02-01", "2024-03-01"),
    ],
)
def test_the_month_window_handles_the_awkward_boundaries(
    moment: dt.datetime, expected_start: str, expected_end: str
) -> None:
    """December rolling into next year, the first instant of a month, and a
    leap day — the three that a naive `month + 1` gets wrong."""
    start, end = month_window(moment)

    assert start.date().isoformat() == expected_start
    assert end.date().isoformat() == expected_end


async def test_a_budget_row_of_nulls_is_not_the_same_as_no_row(clean) -> None:
    """Both refuse, and they send somebody to different screens."""
    clean.add(BudgetConfig(budget_id=BUDGET_ID))
    await clean.flush()

    budget = await load_budget(clean)

    assert budget is not None
    assert budget.missing == (
        "max_tokens_per_run",
        "max_seeds_per_run",
        "monthly_cost_ceiling_usd",
    )


async def test_the_budget_table_is_reachable_at_all(clean) -> None:
    """Guard on the fixture. If the delete above silently matched nothing
    because the table were absent, every refusal test would pass for the wrong
    reason."""
    assert (await clean.execute(select(BudgetConfig))).scalars().all() == []


# --------------------------------------------------------------------------
# The admin surface, because a refusal that points at a screen needs the screen
# --------------------------------------------------------------------------


async def test_a_fresh_install_reports_every_cap_missing(clean) -> None:
    """There is no budget row and nothing seeds one — `config/*.yaml` seeds
    sensible defaults for topics and fetch policy, and a sensible default *cap*
    would satisfy §16's ordering requirement by accident."""
    from api.routes.admin import CAP_FIELDS, _budget_read

    view = await _budget_read(clean)

    assert view.ready is False
    assert sorted(view.missing) == sorted(CAP_FIELDS)
    assert view.month_to_date_usd == 0.0


async def test_a_complete_budget_with_room_is_ready(clean) -> None:
    from api.routes.admin import _budget_read

    await a_budget(clean, ceiling=10.0)

    view = await _budget_read(clean)

    assert view.ready is True
    assert view.missing == []
