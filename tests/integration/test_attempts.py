"""The fetch attempt log and the health line it feeds (task P1-19, spec §12.5).

Against a real Postgres: the point of this table is that it survives the process
that wrote it, and an in-memory double would test the aggregation while assuming
away the part that matters — the CHECK constraint on ``outcome``, the indexes
the health query runs on, and the row still being there tomorrow.

Every test scopes its rows to a domain unique to itself, because these run
against a developer's dev database alongside whatever else is in it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from meridian_core.attempts import (
    MAX_DETAIL_CHARS,
    fetch_health,
    prune_attempts,
    record_attempt,
)
from meridian_core.models import FetchAttempt

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def attempt_domain() -> str:
    return f"a{uuid.uuid4().hex[:12]}.test"


async def _log(sess, domain: str, outcome: str, *, age_hours: float = 0.0, **kwargs):
    return await record_attempt(
        sess,
        domain=domain,
        url=kwargs.pop("url", f"https://{domain}/page"),
        outcome=outcome,
        attempted_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=age_hours),
        **kwargs,
    )


# ------------------------------------------------------------------ the writer


async def test_an_attempt_is_recorded_with_everything_the_health_line_needs(
    session_for, attempt_domain
) -> None:
    sess = await session_for("rw")
    row = await _log(
        sess,
        attempt_domain,
        "success",
        status_code=200,
        duration_ms=412,
        bytes_fetched=15_000,
        task_id=None,
        attempt_number=2,
    )

    stored = await sess.scalar(
        select(FetchAttempt).where(FetchAttempt.attempt_id == row.attempt_id)
    )
    assert stored.outcome == "success"
    assert stored.status_code == 200
    assert stored.duration_ms == 412
    assert stored.bytes_fetched == 15_000
    assert stored.attempt_number == 2
    assert stored.attempted_at is not None


async def test_a_refusal_that_never_touched_the_network_is_still_recorded(
    session_for, attempt_domain
) -> None:
    """A crawler refusing everything and a crawler doing nothing look identical
    in a log that only holds requests that went out."""
    sess = await session_for("rw")
    row = await _log(sess, attempt_domain, "robots_denied", error_detail="disallowed by robots.txt")

    assert row.status_code is None
    assert row.error_detail == "disallowed by robots.txt"


async def test_the_domain_is_normalised_so_per_domain_rates_group(
    session_for, attempt_domain
) -> None:
    """`www.x` and `x` are one domain to the policy; they must be one here too,
    or a domain's rate is split across two spellings and reads as healthier."""
    sess = await session_for("rw")
    row = await _log(sess, f"www.{attempt_domain}", "success", status_code=200)
    assert row.domain == attempt_domain


async def test_a_runaway_error_message_is_truncated(session_for, attempt_domain) -> None:
    """`error_detail` is Text, so an upstream error can be arbitrarily large."""
    sess = await session_for("rw")
    row = await _log(sess, attempt_domain, "connection_error", error_detail="x" * 50_000)
    assert len(row.error_detail) == MAX_DETAIL_CHARS


async def test_a_typod_outcome_never_reaches_the_table(session_for, attempt_domain) -> None:
    """A misspelt outcome must not land as a row nobody can aggregate.

    This is the *Python* guard: ``validate_strings`` on the mapped Enum refuses
    the value before the INSERT is built, which is why passing here says nothing
    about the database. ``test_fetch_outcomes.py`` proves the CHECK constraint
    itself, through raw SQL, for exactly that reason (P0-21).
    """
    sess = await session_for("rw")
    with pytest.raises(StatementError, match="not among the defined enum values"):
        await _log(sess, attempt_domain, "mostly_fine")


# ------------------------------------------------------------- the health line


async def test_the_success_rate_counts_a_304_as_a_success(session_for, attempt_domain) -> None:
    """§6.4's conditional requests exist to produce 304s. A rate that fell as
    caching improved would report the feature working as the crawler failing."""
    sess = await session_for("rw")
    for outcome in ("success", "not_modified", "not_modified", "timeout"):
        await _log(sess, attempt_domain, outcome)

    health = await fetch_health(sess, hours=1, domain=attempt_domain)
    assert health.attempts == 4
    assert health.successes == 3
    assert health.success_rate == pytest.approx(0.75)


async def test_the_health_line_breaks_down_by_outcome(session_for, attempt_domain) -> None:
    """A rate says something is wrong; only the breakdown says what (§12.5)."""
    sess = await session_for("rw")
    for outcome in ("success", "timeout", "timeout", "robots_denied"):
        await _log(sess, attempt_domain, outcome)

    health = await fetch_health(sess, hours=1, domain=attempt_domain)
    assert health.by_outcome == {"success": 1, "timeout": 2, "robots_denied": 1}


async def test_attempts_outside_the_window_are_not_counted(session_for, attempt_domain) -> None:
    """Otherwise the daily line reports the lifetime average and stops moving."""
    sess = await session_for("rw")
    await _log(sess, attempt_domain, "success")
    await _log(sess, attempt_domain, "timeout", age_hours=30)

    day = await fetch_health(sess, hours=24, domain=attempt_domain)
    week = await fetch_health(sess, hours=24 * 7, domain=attempt_domain)

    assert day.attempts == 1 and day.success_rate == 1.0
    assert week.attempts == 2 and week.success_rate == pytest.approx(0.5)


async def test_a_window_with_no_attempts_has_no_rate(session_for, attempt_domain) -> None:
    """0% would report a crawler that fetched nothing as one where everything
    failed — different problems, and one of them is not a problem."""
    health = await fetch_health(await session_for("rw"), hours=24, domain=attempt_domain)
    assert health.attempts == 0
    assert health.success_rate is None
    assert health.by_outcome == {}


async def test_one_domains_failures_do_not_change_anothers_rate(
    session_for, attempt_domain
) -> None:
    other = f"b{uuid.uuid4().hex[:12]}.test"
    sess = await session_for("rw")
    await _log(sess, attempt_domain, "success")
    for _ in range(5):
        await _log(sess, other, "timeout")

    assert (await fetch_health(sess, hours=1, domain=attempt_domain)).success_rate == 1.0
    assert (await fetch_health(sess, hours=1, domain=other)).success_rate == 0.0


@pytest.mark.parametrize("hours", [0, -1])
async def test_a_nonsense_window_is_refused(session_for, hours: int) -> None:
    with pytest.raises(ValueError):
        await fetch_health(await session_for("rw"), hours=hours)


# ------------------------------------------------------------------- retention


async def test_the_prune_removes_only_rows_past_the_window(session_for, attempt_domain) -> None:
    """One row per request is high volume by design; the aggregate rates are
    what matter after a few weeks, not the rows."""
    sess = await session_for("rw")
    fresh = await _log(sess, attempt_domain, "success", age_hours=24)
    stale = await _log(sess, attempt_domain, "success", age_hours=24 * 40)

    deleted = await prune_attempts(sess, older_than_days=30)

    assert deleted >= 1
    remaining = set(
        (
            await sess.scalars(
                select(FetchAttempt.attempt_id).where(FetchAttempt.domain == attempt_domain)
            )
        ).all()
    )
    assert fresh.attempt_id in remaining
    assert stale.attempt_id not in remaining


async def test_the_prune_keeps_deleting_past_one_batch(session_for, attempt_domain) -> None:
    """A prune that has not run for a month must not stop after one statement."""
    sess = await session_for("rw")
    for _ in range(5):
        await _log(sess, attempt_domain, "success", age_hours=24 * 40)

    # Five stale rows and a batch of two: a prune that ran one statement and
    # stopped would leave at least three of them. Rows belonging to other
    # domains can only crowd ours out of the early batches, so the assertion
    # holds without this test touching anything it did not write.
    deleted = await prune_attempts(sess, older_than_days=30, batch_size=2)

    survivors = (
        await sess.scalars(
            select(FetchAttempt.attempt_id).where(FetchAttempt.domain == attempt_domain)
        )
    ).all()
    assert survivors == []
    assert deleted >= 5


@pytest.mark.parametrize("kwargs", [{"older_than_days": -1}, {"batch_size": 0}])
async def test_a_nonsense_prune_is_refused(session_for, kwargs) -> None:
    """`older_than_days=-1` would delete rows from the future onwards — that is,
    everything — which is the one mistake this function must not make quietly."""
    with pytest.raises(ValueError):
        await prune_attempts(await session_for("rw"), **kwargs)
