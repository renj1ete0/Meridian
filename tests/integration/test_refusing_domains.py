"""A domain that refuses every request is blocked (task B-114).

Against a real Postgres, because the rule is an aggregate over `fetch_attempts`
and the exceptions are about rows other code wrote. Mostly rejection tests: the
domains that must *not* be blocked are the ones that would cost the corpus.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.models import FetchAttempt
from meridian_core.models import FetchPolicy as FetchPolicyRow
from meridian_core.policy import (
    REFUSAL_ACTOR,
    REFUSAL_MIN_ATTEMPTS,
    REFUSAL_WINDOW_DAYS,
    REFUSED_STATUS,
    block_refusing_domains,
    refusing_domains,
)

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime.now(dt.UTC)


@pytest.fixture
def domain() -> str:
    return f"refuse-{uuid.uuid4().hex[:10]}.example"


@pytest.fixture
async def cleanup(session_for, domain):
    yield
    sess = await session_for("rw")
    await sess.rollback()
    await sess.execute(delete(FetchAttempt).where(FetchAttempt.domain == domain))
    await sess.execute(delete(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
    await sess.commit()


async def attempts(sess, domain, n, *, outcome="http_error", status=REFUSED_STATUS, ago=1.0):
    at = NOW - dt.timedelta(hours=ago)
    sess.add_all(
        FetchAttempt(
            domain=domain,
            url=f"https://{domain}/{i}",
            attempted_at=at - dt.timedelta(seconds=i),
            outcome=outcome,
            status_code=status,
        )
        for i in range(n)
    )
    await sess.flush()


async def mine(sess, domain) -> list[tuple[str, int]]:
    return [f for f in await refusing_domains(sess, now=NOW) if f[0] == domain]


async def test_a_domain_that_refused_every_request_is_blocked_with_a_note(
    session_for, domain, cleanup
) -> None:
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS)

    found = [f for f in await block_refusing_domains(sess, now=NOW) if f[0] == domain]
    await sess.commit()

    assert found == [(domain, REFUSAL_MIN_ATTEMPTS)]
    row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
    assert row.status == "blocked"
    assert row.updated_by == REFUSAL_ACTOR and "B-114" in row.note


async def test_one_answer_of_any_other_kind_spares_it(session_for, domain, cleanup) -> None:
    """Sites the corpus reads also return long runs of 403s; one success is proof."""
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS * 3)
    await attempts(sess, domain, 1, outcome="success", status=200)
    assert await mine(sess, domain) == []


@pytest.mark.parametrize(("outcome", "status"), [("http_error", 404), ("http_error", 503), ("timeout", None)])
async def test_only_refusals_count_as_refusals(session_for, domain, cleanup, outcome, status) -> None:
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS - 1)
    await attempts(sess, domain, 1, outcome=outcome, status=status)
    assert await mine(sess, domain) == []


async def test_below_the_minimum_nothing_is_concluded(session_for, domain, cleanup) -> None:
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS - 1)
    assert await mine(sess, domain) == []


async def test_requests_that_never_went_out_are_not_counted(session_for, domain, cleanup) -> None:
    """A robots denial is no request, so it neither refuses nor spares."""
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS - 1)
    await attempts(sess, domain, 50, outcome="robots_denied", status=None)
    assert await mine(sess, domain) == []
    await attempts(sess, domain, 1)
    assert await mine(sess, domain) == [(domain, REFUSAL_MIN_ATTEMPTS)]


async def test_refusals_outside_the_window_are_forgotten(session_for, domain, cleanup) -> None:
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS, ago=24 * (REFUSAL_WINDOW_DAYS + 1))
    assert await mine(sess, domain) == []


async def test_a_domain_a_person_unblocked_gets_a_fresh_window(session_for, domain, cleanup) -> None:
    """Otherwise Admin's unblock would be undone at the next hourly pass."""
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS * 2, ago=5)
    sess.add(
        FetchPolicyRow(
            domain=domain,
            settings={},
            status="active",
            updated_by="admin",
            updated_at=NOW - dt.timedelta(hours=2),
        )
    )
    await sess.flush()
    assert await mine(sess, domain) == []

    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS, ago=1)
    assert await mine(sess, domain) == [(domain, REFUSAL_MIN_ATTEMPTS)]


@pytest.mark.parametrize("status", ["paused", "blocked"])
async def test_a_domain_already_stopped_is_left_as_it_is(session_for, domain, cleanup, status) -> None:
    """A person's pause is not overwritten with this rule's block."""
    sess = await session_for("rw")
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS)
    sess.add(FetchPolicyRow(domain=domain, settings={}, status=status, note="set by hand"))
    await sess.flush()
    assert await mine(sess, domain) == []


async def test_the_rules_own_block_lifts_after_the_window(session_for, domain, cleanup) -> None:
    """A blocked domain is never asked again, so without expiry it could never recover."""
    sess = await session_for("rw")
    sess.add(
        FetchPolicyRow(
            domain=domain,
            settings={},
            status="blocked",
            updated_by=REFUSAL_ACTOR,
            updated_at=NOW - dt.timedelta(days=REFUSAL_WINDOW_DAYS + 1),
        )
    )
    await sess.flush()

    await block_refusing_domains(sess, now=NOW)

    row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
    assert row.status == "active"
    # Refusals from before the lift were already answered by the block.
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS, ago=24 * 3)
    assert await mine(sess, domain) == []
    # Fresh ones, after it, are judged again.
    await attempts(sess, domain, REFUSAL_MIN_ATTEMPTS, ago=-1)
    assert await mine(sess, domain) == [(domain, REFUSAL_MIN_ATTEMPTS)]


async def test_a_block_someone_else_made_never_lifts(session_for, domain, cleanup) -> None:
    sess = await session_for("rw")
    sess.add(
        FetchPolicyRow(
            domain=domain,
            settings={},
            status="blocked",
            updated_by="admin",
            updated_at=NOW - dt.timedelta(days=REFUSAL_WINDOW_DAYS * 3),
        )
    )
    await sess.flush()

    await block_refusing_domains(sess, now=NOW)

    row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
    assert row.status == "blocked"
