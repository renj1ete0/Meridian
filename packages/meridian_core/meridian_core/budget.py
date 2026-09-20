"""Caps, and the refusal to run without them (tasks `P4-10`, `P4-13`, §16, §11.9).

§11.9 describes the one feedback loop in this design that nothing else bounds:
gap analysis emits seeds, seeds become crawl targets, a larger corpus produces
more gaps, and tomorrow's batch is bigger than today's. §16 calls the risk
*"manageable if caps are set before first autonomous run"* — which is a
mitigation with an ordering requirement inside it, and until now nothing
enforced the ordering.

**Absent is refused, never unlimited.** Every function here treats a missing
budget, a missing cap, or a missing ceiling as a reason not to start. That is
the opposite of the usual ergonomics and it is deliberate: a default of infinity
is the shape in which forgetting to configure something becomes a bill, and the
loop is unattended, so the first signal would be the invoice rather than a log
line. `reserve_seeds` in `validation.py` already took this position for its own
cap; this module is where the caps come from.

**Cost is checked before a run, not during it.** A run cannot know what it will
spend, so the ceiling is enforced as "the month so far leaves room to start" —
and a run that overshoots is recorded honestly rather than killed halfway, which
would leave a half-written graph to reconcile. The month's *next* run is the one
that gets refused. That is a real limitation and the alternative is worse: §11.9
asks for trend alerting precisely because the ceiling alone is a blunt
instrument.

**The calendar month, in UTC.** Not a rolling 30 days: a ceiling somebody sets
by looking at a monthly invoice should reset when the invoice does, and a
rolling window makes "how much is left" a question nobody can answer from the
statement in front of them.
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

    Deliberately not `ValidationError`: that type reports a bad *write* back to
    a model as a tool-call failure it might retry differently. A budget refusal
    is not about the content of the call, and a model retrying it with better
    arguments is exactly what should not happen.
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

    Measured on `started_at` rather than `completed_at`, so a run that is still
    going already counts toward the month it began in — otherwise a long run
    spanning midnight on the 1st would be invisible to the ceiling for as long
    as it kept spending.

    Runs with no recorded cost contribute zero rather than making the total
    null, which is what `coalesce` is doing here: an old run that predates cost
    logging should not blank out the month.
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

    Three refusals, in the order somebody would fix them: no budget row at all,
    a budget with caps missing, and a month that has already reached its
    ceiling. Returns the budget on success so the caller enforces the same
    numbers it was checked against — reading them twice invites a run governed
    by caps that changed in between.
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

    The same shape as `reserve_seeds`, for the same reasons, and they are worth
    restating because the shape is the point:

    - **`cap=None` refuses.** Unconfigured is not unlimited (§16).
    - **All or nothing**, so a partially admitted call does not leave the caller
      unable to say what it spent.
    - **The row is locked, not just read.** Two concurrent tool calls reading
      `tokens_used` at 900 against a cap of 1000 would both pass and both write.

    Returns the remaining allowance, so a caller can stop asking for more before
    it is refused — a model that hits the cap mid-answer wastes the tokens it
    already spent on that answer.
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

    A call has to be paid for before it is made — the cap exists to stop a call
    that cannot be afforded, and finding out afterwards is not a cap. But the
    only figure available beforehand is the worst case: the prompt plus
    `max_tokens`, which almost every answer comes in under. Left alone, a run
    would exhaust its allowance on answers it never gave.

    So the reservation is the worst case and this is the correction. It only
    ever *releases* — an answer that somehow cost more than was reserved keeps
    the larger figure, because the tokens were genuinely spent and a cap that
    forgave an overrun would be a cap with a hole in it.
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

    Additive rather than assigned: a run makes many calls to many models, and a
    setter would record whichever one happened to write last. §11.9 wants the
    per-run figure to be comparable week on week, which it is not if it means
    "the last call" in some runs and "all of them" in others.
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
