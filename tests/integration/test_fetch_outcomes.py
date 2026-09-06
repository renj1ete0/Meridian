"""Every outcome the fetcher can produce is one the database will store.

A completeness probe, against a real Postgres, because the failure it catches is
invisible everywhere else. ``worker/fetch.py`` returns an outcome string; the
worker loop writes it to ``fetch_attempts.outcome``, which is a CHECK-constrained
column. Nothing connects the two but a shared value set, and `P0-21` established
that Alembic's autogenerate does not notice CHECK constraints on existing tables
— so a new outcome can reach production with the column widened and the
constraint unchanged, failing for the first time on the first refusal.

The set is enumerated from the model rather than typed out here. A test with a
hardcoded list would need editing every time the schema legitimately grows, and
would then be edited into passing.
"""

from __future__ import annotations

import datetime as dt
import inspect as inspect_module
import re

import pytest
from sqlalchemy import delete, select, text

from meridian_core.models import FetchAttempt
from meridian_core.models.queue import FETCH_OUTCOME
from worker import fetch as fetch_module

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.mark.parametrize("outcome", sorted(FETCH_OUTCOME.enums))
async def test_the_database_accepts_every_declared_outcome(session_for, outcome: str) -> None:
    """The CHECK constraint and the model's value set must not have drifted."""
    sess = await session_for("rw")
    sess.add(
        FetchAttempt(
            domain="outcome-probe.test",
            url=f"https://outcome-probe.test/{outcome}",
            attempted_at=dt.datetime.now(dt.UTC),
            outcome=outcome,
            attempt_number=1,
        )
    )
    await sess.flush()

    stored = await sess.scalar(
        select(FetchAttempt.outcome).where(
            FetchAttempt.url == f"https://outcome-probe.test/{outcome}"
        )
    )
    assert stored == outcome
    await sess.execute(delete(FetchAttempt).where(FetchAttempt.domain == "outcome-probe.test"))


async def test_the_database_itself_refuses_an_unknown_outcome(session_for) -> None:
    """The weaker half — that valid values work — proves nothing on its own.

    Deliberately raw SQL. Going through the ORM would prove only that
    SQLAlchemy's ``validate_strings=True`` refused the value in Python, which it
    does, and which says nothing about the database: ``constrained()`` produced
    unchecked VARCHAR columns for the whole of Phase 0 because
    ``create_constraint`` defaults to False, and the ORM-level guard was working
    perfectly the entire time. The constraint has to be tested where it lives.
    """
    sess = await session_for("rw")
    with pytest.raises(Exception, match="(?i)check constraint|ck_fetch_attempts"):
        await sess.execute(
            text(
                "INSERT INTO fetch_attempts (domain, url, attempted_at, outcome, attempt_number) "
                "VALUES ('outcome-probe.test', 'https://outcome-probe.test/nonsense', now(), "
                "'probably_fine', 1)"
            )
        )
    await sess.rollback()


async def test_the_orm_refuses_an_unknown_outcome_before_the_database_has_to(
    session_for,
) -> None:
    """Belt and braces: the value set is enforced in Python as well as in SQL."""
    sess = await session_for("rw")
    sess.add(
        FetchAttempt(
            domain="outcome-probe.test",
            url="https://outcome-probe.test/nonsense",
            attempted_at=dt.datetime.now(dt.UTC),
            outcome="probably_fine",
            attempt_number=1,
        )
    )
    with pytest.raises(Exception, match="not among the defined enum values"):
        await sess.flush()
    await sess.rollback()


def test_every_outcome_the_fetcher_returns_is_a_declared_one() -> None:
    """The other direction: a string the code emits that the schema never heard of.

    Read out of the source rather than by exercising every path, because the
    paths that produce the rarest outcomes are the hardest to reach and the most
    likely to be wrong. A literal passed to ``refuse(...)`` or set as
    ``outcome=`` is exactly what would land in the column.
    """
    source = inspect_module.getsource(fetch_module)
    emitted = set(re.findall(r'(?:refuse|return b"", )\(?\s*"([a-z_]+)"', source))
    emitted |= set(re.findall(r'outcome="([a-z_]+)"', source))

    assert emitted, "the scan found no outcomes at all — it has stopped matching the code"
    undeclared = emitted - set(FETCH_OUTCOME.enums)
    assert not undeclared, (
        f"worker/fetch.py can return {sorted(undeclared)}, which the fetch_outcome "
        "CHECK constraint would reject at insert time"
    )
