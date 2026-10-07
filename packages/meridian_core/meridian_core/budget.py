"""Caps, and the refusal to run without them (tasks `P4-10`, `P4-13`, §16, §11.9).

A missing budget, cap or ceiling refuses; it is never unlimited. Cost is checked before
a run starts, so the month's next run is the one refused. Months are calendar months in
UTC. See docs/features/synthesis.md#budgets.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import BudgetConfig, Run

log = get_logger(__name__)

#: The only row. The table's CHECK enforces it; this names it once so callers
#: never write the literal.
BUDGET_ID = 1

__all__ = [
    "BUDGET_ID",
    "Budget",
    "BudgetError",
    "check_can_start_run",
    "load_budget",
    "month_to_date_cost",
    "month_window",
    "record_run_spend",
    "reserve_tokens",
    "settle_tokens",
]


class BudgetError(RuntimeError):
    """A run must not start, or must not continue.

    Not `ValidationError`, which goes back to a model as a failure to retry.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class Budget:
    """The configured caps, as read.

    Frozen because a run reads these once and enforces them repeatedly: a cap
    that could be mutated mid-run by one tool call is a cap the next call
    disagrees with.
    """

    max_tokens_per_run: int | None
    max_seeds_per_run: int | None
    monthly_cost_ceiling_usd: float | None

    @property
    def is_complete(self) -> bool:
        """Whether every cap §11.9 asks for has actually been set.

        All three, not any: a run capped on seeds and uncapped on tokens is
        uncapped, because the expensive half is the one nobody limited.
        """
        return (
            self.max_tokens_per_run is not None
            and self.max_seeds_per_run is not None
            and self.monthly_cost_ceiling_usd is not None
        )

    @property
    def missing(self) -> tuple[str, ...]:
        """Which caps are unset, named for an error message somebody can act on."""
        absent = []
        if self.max_tokens_per_run is None:
            absent.append("max_tokens_per_run")
        if self.max_seeds_per_run is None:
            absent.append("max_seeds_per_run")
        if self.monthly_cost_ceiling_usd is None:
            absent.append("monthly_cost_ceiling_usd")
        return tuple(absent)


async def load_budget(sess: AsyncSession) -> Budget | None:
    """The configured budget, or None when nobody has configured one.

    None and a row of nulls are different states and both refuse, but they
    refuse with different messages — "no budget has been configured" and "the
    budget is missing a token cap" send somebody to different screens.
    """
    row = await sess.get(BudgetConfig, BUDGET_ID)
    if row is None:
        return None
    return Budget(
        max_tokens_per_run=row.max_tokens_per_run,
        max_seeds_per_run=row.max_seeds_per_run,
        monthly_cost_ceiling_usd=row.monthly_cost_ceiling_usd,
    )


def month_window(now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    """The calendar month containing ``now``, half-open, in UTC."""
    moment = now or dt.datetime.now(dt.UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    start = moment.astimezone(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Month arithmetic without a calendar library: the 28th of next month is in
    # next month whatever this month's length, and truncating it to day 1 lands
    # on the boundary. December rolls over because `month % 12 + 1` wraps.
    following = start.replace(
        year=start.year + (start.month == 12),
        month=start.month % 12 + 1,
        day=1,
    )
    return start, following


async def month_to_date_cost(sess: AsyncSession, *, now: dt.datetime | None = None) -> float:
    """What this calendar month's runs have cost so far.

    Counted by `started_at`, so a running run counts already; runs with no recorded
    cost count as zero.
    """
    start, following = month_window(now)
    total = await sess.scalar(
        select(func.coalesce(func.sum(Run.cost_usd), 0.0)).where(
            Run.started_at >= start, Run.started_at < following
        )
    )
    return float(total or 0.0)


async def check_can_start_run(sess: AsyncSession, *, now: dt.datetime | None = None) -> Budget:
    """Refuse to start a synthesis run that has no budget (`P4-13`).

    Refuses with no budget row, with caps missing, or with the month's ceiling reached.
    Returns the budget, so the run enforces the numbers it was checked against.
    """
    budget = await load_budget(sess)
    if budget is None:
        raise BudgetError(
            "budget_unconfigured",
            "No budget is configured. §16 requires caps to exist before the "
            "first autonomous run; set them in Admin before starting one.",
        )
    if not budget.is_complete:
        raise BudgetError(
            "budget_incomplete",
            f"The budget is missing {', '.join(budget.missing)}. An unset cap "
            f"is not an unlimited one — set every cap before starting a run.",
        )

    spent = await month_to_date_cost(sess, now=now)
    ceiling = budget.monthly_cost_ceiling_usd
    assert ceiling is not None  # is_complete
    if spent >= ceiling:
        raise BudgetError(
            "monthly_ceiling",
            f"This month has spent {spent:.2f} of a {ceiling:.2f} USD ceiling. "
            f"Raise the ceiling or wait for the month to turn over.",
        )

    log.info(
        "run budget checked",
        extra={
            "spent_usd": round(spent, 4),
            "ceiling_usd": ceiling,
            "max_tokens_per_run": budget.max_tokens_per_run,
            "max_seeds_per_run": budget.max_seeds_per_run,
        },
    )
    return budget


async def reserve_tokens(sess: AsyncSession, run_id: int, count: int, *, cap: int | None) -> int:
    """Take ``count`` tokens out of this run's allowance, or refuse the lot.

    As `reserve_seeds`: `cap=None` refuses, all or nothing, on a locked row. Returns the
    remaining allowance.
    """
    if cap is None or cap <= 0:
        raise BudgetError(
            "token_cap",
            "No token cap is configured for this run; refusing rather than "
            "treating an absent cap as unlimited (§16).",
        )
    if count <= 0:
        raise BudgetError("token_cap", "A token reservation must be for at least one token.")

    run = (
        await sess.execute(select(Run).where(Run.run_id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise BudgetError("run_open", f"No run {run_id}.")
    if run.status != "running":
        raise BudgetError("run_open", f"Run {run_id} is {run.status}, not running.")

    if run.tokens_used + count > cap:
        raise BudgetError(
            "token_cap",
            f"Run {run_id} has used {run.tokens_used} of {cap} tokens; "
            f"{count} more would exceed the cap.",
        )

    run.tokens_used += count
    await sess.flush()
    remaining = cap - run.tokens_used
    log.info(
        "tokens reserved",
        extra={"run_id": run_id, "tokens": count, "used": run.tokens_used, "remaining": remaining},
    )
    return remaining


async def settle_tokens(sess: AsyncSession, run_id: int, *, reserved: int, actual: int) -> int:
    """Correct a reservation once the real cost is known (`P4-15`).

    The reservation is the worst case (prompt plus `max_tokens`); this releases the
    difference. It only ever releases: an overrun keeps the larger figure.
    """
    if reserved < 0 or actual < 0:
        raise BudgetError("token_cap", "Token counts cannot be negative.")

    release = reserved - actual
    if release <= 0:
        return 0

    run = (
        await sess.execute(select(Run).where(Run.run_id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise BudgetError("run_open", f"No run {run_id}.")

    # Never below zero: a settlement that ran twice would otherwise credit the
    # run with tokens nobody reserved.
    release = min(release, run.tokens_used)
    run.tokens_used -= release
    await sess.flush()
    log.info(
        "tokens released",
        extra={"run_id": run_id, "released": release, "used": run.tokens_used},
    )
    return release


async def record_run_spend(sess: AsyncSession, run_id: int, *, cost_usd: float) -> float:
    """Add to what this run has cost, and return the run's total.

    Additive, so the figure covers every call in the run.
    """
    if cost_usd < 0:
        raise BudgetError("cost", "A run's cost cannot be negative.")

    run = (
        await sess.execute(select(Run).where(Run.run_id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise BudgetError("run_open", f"No run {run_id}.")

    run.cost_usd = (run.cost_usd or 0.0) + cost_usd
    await sess.flush()
    log.info("run spend recorded", extra={"run_id": run_id, "cost_usd": run.cost_usd})
    return float(run.cost_usd)
