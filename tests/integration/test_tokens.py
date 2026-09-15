"""Scoped credentials (task P3-03, spec §11.4, §11.11).

Against a real Postgres because the uniqueness constraint on `token_hash` and
the nullable `allowed_tools` are both load-bearing, and neither is visible from
Python.

Rejection tests dominate for the usual reason: the failure that matters is not
"a valid token was refused" — someone reports that within a minute. It is a
token that should not have worked and did, which nobody reports.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.models import AgentToken
from meridian_core.tokens import (
    TokenScope,
    hash_token,
    issue_token,
    resolve_token,
    revoke_token,
)

pytestmark = pytest.mark.usefixtures("require_db")

TOOLS = ["search_chunks", "get_source_metadata"]


@pytest.fixture
def agent() -> str:
    return f"agent-{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def cleanup(session_for, agent):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(AgentToken).where(AgentToken.agent_id == agent))
    await sess.commit()


# --------------------------------------------------------------------------
# The secret does not survive issuing
# --------------------------------------------------------------------------


async def test_the_secret_is_never_stored(session_for, agent, cleanup) -> None:
    """§11.11 keeps credentials out of the database's reach, and this is the one
    credential the database has to know *something* about. What it knows is a
    hash: a snapshot, a backup or a leaked dump contains nothing usable."""
    sess = await session_for("rw")
    secret, row = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)

    stored = (
        await sess.execute(select(AgentToken).where(AgentToken.token_id == row.token_id))
    ).scalar_one()

    assert secret not in stored.token_hash
    assert stored.token_hash.startswith("sha256:")
    assert stored.token_hash == hash_token(secret)


async def test_two_tokens_do_not_collide(session_for, agent, cleanup) -> None:
    """`token_hash` is UNIQUE, so a generator that repeated itself would fail at
    the insert rather than silently issuing one credential twice."""
    sess = await session_for("rw")
    first, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)
    second, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)

    assert first != second


# --------------------------------------------------------------------------
# What a valid token carries
# --------------------------------------------------------------------------


async def test_a_valid_token_resolves_to_its_scope(session_for, agent, cleanup) -> None:
    sess = await session_for("rw")
    secret, row = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS, rate_limit=60)

    scope = await resolve_token(sess, secret)

    assert isinstance(scope, TokenScope)
    assert scope.token_id == row.token_id
    assert scope.agent_id == agent
    assert scope.rate_limit == 60
    assert scope.permits("search_chunks")
    assert not scope.permits("add_edge")


async def test_the_scope_does_not_carry_the_row(session_for, agent, cleanup) -> None:
    """A dataclass rather than the ORM object, so nothing downstream can hold or
    log the record containing the hash."""
    sess = await session_for("rw")
    secret, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)

    scope = await resolve_token(sess, secret)

    assert not hasattr(scope, "token_hash")
    assert "hash" not in repr(scope).lower()


# --------------------------------------------------------------------------
# Every way a token fails
# --------------------------------------------------------------------------


async def test_an_unknown_token_is_refused(session_for) -> None:
    sess = await session_for("rw")
    assert await resolve_token(sess, "not-a-token-anyone-issued") is None


async def test_an_empty_token_is_refused(session_for) -> None:
    """An unset header arrives as the empty string more often than as absence,
    and hashing it would produce a perfectly valid lookup for a hash nobody
    holds — which is a refusal, but by luck rather than by intent."""
    sess = await session_for("rw")
    assert await resolve_token(sess, "") is None


async def test_a_revoked_token_is_refused(session_for, agent, cleanup) -> None:
    sess = await session_for("rw")
    secret, row = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)
    assert await resolve_token(sess, secret) is not None

    assert await revoke_token(sess, row.token_id) is True

    assert await resolve_token(sess, secret) is None


async def test_an_expired_token_is_refused(session_for, agent, cleanup) -> None:
    sess = await session_for("rw")
    past = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    secret, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS, expires_at=past)

    assert await resolve_token(sess, secret) is None


async def test_expiry_is_exclusive_at_the_boundary(session_for, agent, cleanup) -> None:
    """A token expiring *at* this instant is expired. Off by one in the other
    direction leaves a credential valid for the moment it was supposed to stop
    being valid, which is the kind of thing nobody notices until an audit."""
    sess = await session_for("rw")
    moment = dt.datetime.now(dt.UTC)
    secret, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS, expires_at=moment)

    assert await resolve_token(sess, secret, now=moment) is None
    assert await resolve_token(sess, secret, now=moment - dt.timedelta(seconds=1)) is not None


async def test_every_rejection_looks_the_same_to_the_caller(session_for, agent, cleanup) -> None:
    """Unknown, revoked and expired all return None.

    Distinguishing them tells an unauthenticated caller that a token exists, or
    once existed, which is information it has not earned — and "revoked" in
    particular confirms a real credential was guessed. The operator gets the
    reason in the log instead.
    """
    sess = await session_for("rw")
    revoked_secret, revoked_row = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)
    await revoke_token(sess, revoked_row.token_id)
    expired_secret, _ = await issue_token(
        sess,
        agent_id=agent,
        allowed_tools=TOOLS,
        expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
    )

    assert await resolve_token(sess, "unknown") is None
    assert await resolve_token(sess, revoked_secret) is None
    assert await resolve_token(sess, expired_secret) is None


# --------------------------------------------------------------------------
# The unscoped case fails closed
# --------------------------------------------------------------------------


async def test_a_token_with_no_tools_permits_nothing(session_for, agent, cleanup) -> None:
    """NULL `allowed_tools` means *no* tools, not *all* tools.

    The same argument as `P3-07`'s refusal to give the guest role default
    privileges: the nullable column's empty state is what a row created without
    thinking will hold, so it has to be the safe one. Forgetting to grant
    produces a caller who cannot do something and says so; forgetting to
    restrict produces one who can do everything and does not mention it.
    """
    sess = await session_for("rw")
    secret, row = await issue_token(sess, agent_id=agent, allowed_tools=[])
    row.allowed_tools = None
    await sess.flush()

    scope = await resolve_token(sess, secret)

    assert scope is not None, "an unscoped token should authenticate"
    assert scope.allowed_tools == frozenset()
    assert not scope.permits("search_chunks"), "an unscoped token authorised a tool"


async def test_revoking_a_token_that_is_not_there_is_not_an_error(session_for) -> None:
    """An operator revoking twice, or revoking an id from a stale list, should
    be told nothing happened rather than handed an exception."""
    sess = await session_for("rw")
    assert await revoke_token(sess, 10**12) is False
