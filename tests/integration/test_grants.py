"""Access for somebody who is not the operator (task `P3-06`, §3).

Two properties carry this design, and both are negatives:

- **Revoking a person revokes every credential they hold.** That is the reason
  grants exist rather than tokens alone, and the way it fails is one token left
  working that nobody remembers issuing.
- **A profile grants tools, never a list somebody edited.** §3: a free-form set
  per person is how somebody ends up holding a write tool nobody remembers
  granting.

Against a real Postgres for the first, because it is a transaction over two
tables and the point is that they move together.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from meridian_core.grants import (
    PROFILE_TOOLS,
    GrantError,
    ResolvedGrant,
    resolve_grant,
    revoke_grant,
)
from meridian_core.models import AgentToken, Grant

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)
SUBJECT = "guest@grants.test"


@pytest.fixture
async def clean(session_for):
    sess = await session_for("rw")
    await sess.execute(delete(AgentToken).where(AgentToken.agent_id.like("%grants.test%")))
    await sess.execute(delete(Grant).where(Grant.subject.like("%grants.test%")))
    await sess.flush()
    return sess


async def a_grant(sess, **over) -> Grant:
    row = Grant(
        subject=over.pop("subject", SUBJECT),
        subject_kind=over.pop("subject_kind", "person"),
        profile=over.pop("profile", "reader"),
        expires_at=over.pop("expires_at", NOW + dt.timedelta(days=30)),
        **over,
    )
    sess.add(row)
    await sess.flush()
    return row


def resolved(**over) -> ResolvedGrant:
    return ResolvedGrant(
        grant_id=over.pop("grant_id", 1),
        subject=over.pop("subject", SUBJECT),
        subject_kind=over.pop("subject_kind", "person"),
        profile=over.pop("profile", "reader"),
        topics=over.pop("topics", ()),
        max_source_tier=over.pop("max_source_tier", None),
        raw_files=over.pop("raw_files", False),
    )


# --------------------------------------------------------------------------
# Revocation — the reason grants exist


async def test_revoking_a_grant_revokes_every_token_under_it(clean) -> None:
    """The property a per-token model cannot express. Somebody holds a browser
    session, a laptop client and a server client; missing one leaves it
    working."""
    grant = await a_grant(clean)
    for n in range(3):
        clean.add(
            AgentToken(agent_id=f"grants.test-{n}", token_hash=f"h{n}", grant_id=grant.grant_id)
        )
    await clean.flush()

    revoked = await revoke_grant(clean, grant.grant_id)

    assert revoked == 3
    still_live = (
        (
            await clean.execute(
                select(AgentToken).where(
                    AgentToken.grant_id == grant.grant_id, AgentToken.revoked.is_(False)
                )
            )
        )
        .scalars()
        .all()
    )
    assert still_live == []


async def test_revocation_marks_tokens_rather_than_deleting_them(clean) -> None:
    """An audit entry points at a token row. Deleting it would leave the
    history unable to say whose credential made a call."""
    grant = await a_grant(clean)
    clean.add(AgentToken(agent_id="grants.test-a", token_hash="ha", grant_id=grant.grant_id))
    await clean.flush()

    await revoke_grant(clean, grant.grant_id)

    assert (
        await clean.scalar(select(AgentToken.token_id).where(AgentToken.grant_id == grant.grant_id))
    ) is not None


async def test_a_person_grant_must_have_an_expiry(clean) -> None:
    """§3: an access grant with no end is a grant nobody revisits. Enforced by
    the database, so it cannot be skipped by a code path that forgot."""
    from sqlalchemy.exc import IntegrityError

    clean.add(Grant(subject=SUBJECT, subject_kind="person", expires_at=None))

    with pytest.raises(IntegrityError):
        await clean.flush()
    await clean.rollback()


async def test_a_service_grant_may_be_open_ended(clean) -> None:
    """It belongs to a machine somebody is already running, not a person whose
    circumstances change."""
    await a_grant(clean, subject_kind="service", subject="ci@grants.test", expires_at=None)

    grant = await resolve_grant(clean, "ci@grants.test", now=NOW)

    assert grant.subject_kind == "service"


# --------------------------------------------------------------------------
# Three refusals, three reasons


async def test_an_unknown_subject_has_no_access(clean) -> None:
    with pytest.raises(GrantError) as raised:
        await resolve_grant(clean, "nobody@grants.test", now=NOW)

    assert raised.value.reason == "no_grant"


async def test_a_revoked_grant_says_revoked(clean) -> None:
    await a_grant(clean, revoked=True)

    with pytest.raises(GrantError) as raised:
        await resolve_grant(clean, SUBJECT, now=NOW)

    assert raised.value.reason == "revoked"


async def test_an_expired_grant_says_expired_and_when(clean) -> None:
    """Three different messages, because "you have no access", "it was revoked"
    and "it expired last Tuesday" are different things to be told."""
    await a_grant(clean, expires_at=NOW - dt.timedelta(days=1))

    with pytest.raises(GrantError) as raised:
        await resolve_grant(clean, SUBJECT, now=NOW)

    assert raised.value.reason == "expired"
    assert "2026-09-14" in str(raised.value)


# --------------------------------------------------------------------------
# Profiles


def test_no_profile_carries_a_write_tool() -> None:
    """Including `operator`. A grant is for reading somebody else's corpus
    (§2.1); writes belong to the orchestrator's own credential."""
    every_tool = {tool for tools in PROFILE_TOOLS.values() for tool in tools}

    assert not [t for t in every_tool if any(w in t for w in ("add_", "write", "enqueue", "tag_"))]


def test_a_reader_cannot_run_arbitrary_queries() -> None:
    assert "run_readonly_query" not in PROFILE_TOOLS["reader"]
    assert "run_readonly_query" in PROFILE_TOOLS["analyst"]


def test_an_unknown_profile_grants_nothing() -> None:
    """Fails closed. A profile added by a later migration and not yet known to
    this code must not widen anything, and a grant that can call nothing is a
    legible failure rather than a silent escalation."""
    assert resolved(profile="superuser").tools == frozenset()
    assert not resolved(profile="superuser").may_call("search_chunks")


# --------------------------------------------------------------------------
# §5: two things a guest does not get by default


def test_annotations_are_not_shared_by_default() -> None:
    """§12.5 predicts annotations become the highest-quality layer because they
    are the operator's own thinking, which makes them the most personal thing
    in the system. Sharing them is a deliberate act, not a default."""
    assert resolved(profile="reader").annotations is False
    assert resolved(profile="analyst").annotations is False
    assert resolved(profile="operator").annotations is True


async def test_a_grant_defaults_to_no_raw_files_in_the_database(clean) -> None:
    """The default lives in the column, not only in the code that writes it."""
    grant = await a_grant(clean)

    assert grant.raw_files is False
