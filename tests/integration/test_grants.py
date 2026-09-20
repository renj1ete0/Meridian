"""Access for somebody who is not the operator (tasks `P3-06`, `P3-10`, §3, §5).

Two properties carry this design, and both are negatives:

- **Revoking a person revokes every credential they hold.** That is the reason
  grants exist rather than tokens alone, and the way it fails is one token left
  working that nobody remembers issuing.
- **A guest cannot widen their own grant.** Filters intersect, so a narrower
  request narrows further and a broader one changes nothing.

Against a real Postgres for the first, because it is a transaction over two
tables and the point is that they move together.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from meridian_core.grants import (
    PROFILE_TOOLS,
    TIER_ORDER,
    GrantError,
    ResolvedGrant,
    filters_for,
    resolve_grant,
    revoke_grant,
    tiers_allowed,
)
from meridian_core.models import AgentToken, Grant
from meridian_core.search import SearchFilters

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
# Scoping — a guest cannot widen their grant


def test_a_grant_without_topics_sees_everything() -> None:
    assert filters_for(resolved()).topics is None


def test_a_scoped_grant_narrows_an_unscoped_request() -> None:
    filters = filters_for(resolved(topics=("walkability",)))

    assert filters.topics == ("walkability",)


def test_a_guest_asking_wider_does_not_get_wider() -> None:
    """The intersection is the point. Asking for a topic they do not hold must
    not add it."""
    filters = filters_for(
        resolved(topics=("walkability",)), SearchFilters(topics=["walkability", "robotics"])
    )

    assert filters.topics == ("walkability",)


def test_a_guest_asking_only_for_what_they_lack_gets_nothing(clean=None) -> None:
    """Not "everything they do hold" — that answers a question they did not
    ask. An empty intersection is the honest result."""
    filters = filters_for(resolved(topics=("walkability",)), SearchFilters(topics=["robotics"]))

    assert filters.topics not in (None, ())
    assert "robotics" not in (filters.topics or ())
    assert "walkability" not in (filters.topics or ())


def test_a_tier_ceiling_admits_that_tier_and_above() -> None:
    assert tiers_allowed("academic") == ("government", "academic")


def test_no_ceiling_admits_every_tier() -> None:
    assert tiers_allowed(None) == TIER_ORDER


def test_an_unknown_ceiling_admits_nothing() -> None:
    """A typo must not widen access. Treating an unrecognised ceiling as "no
    ceiling" is a mistake failing in the wrong direction."""
    assert tiers_allowed("gov") == ()


def test_a_guests_search_is_always_cleared_only() -> None:
    """This is content going to somebody else's model, which is what `P4-14`'s
    screening exists to guard."""
    assert filters_for(resolved(), SearchFilters(cleared_only=False)).cleared_only is True


# --------------------------------------------------------------------------
# §5: two things a guest does not get by default


def test_raw_files_are_off_unless_the_grant_says_otherwise() -> None:
    """Serving the raw store to somebody else is redistribution of third-party
    material — a different act from sharing what was extracted from it. A guest
    gets chunks, metadata and the source URL, which is a citation."""
    from meridian_core.grants import may_read_raw

    assert may_read_raw(resolved()) is False
    assert may_read_raw(resolved(raw_files=True)) is True


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


# --------------------------------------------------------------------------
# Audit and rate limiting (task `P3-11`, §6)
# --------------------------------------------------------------------------


async def test_calls_are_indexed_by_grant_not_by_token(clean) -> None:
    """The reason the table has this shape. "What has this person's model been
    reading" is unanswerable from a per-token log once they hold three
    clients, and joining them by hand means hoping you found them all."""
    from meridian_core.grants import calls_by_grant, record_call

    grant = await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)

    for token_id in (1, 2, 3):
        await record_call(clean, live, "search_chunks", token_id=token_id, rows=4)

    calls = await calls_by_grant(clean, grant.grant_id)

    assert len(calls) == 3
    assert {c.token_id for c in calls} == {1, 2, 3}


async def test_the_arguments_are_kept_and_the_results_are_not(clean) -> None:
    """What somebody searched for is the audit. What came back is the corpus,
    and copying it here would be a second store of the same content with none
    of the retention rules the first one has (§5.4)."""
    from meridian_core.grants import calls_by_grant, record_call

    grant = await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)

    await record_call(clean, live, "search_chunks", arguments={"query": "kerb ramps"}, rows=12)

    call = (await calls_by_grant(clean, grant.grant_id))[0]
    assert call.arguments == {"query": "kerb ramps"}
    assert call.rows == 12
    assert not hasattr(call, "results")


async def test_refusals_are_recorded_too(clean) -> None:
    """A log of successful calls answers half the question. A grant repeatedly
    refused a tool is the more interesting signal, and it is the one that
    disappears if only successes land here."""
    from meridian_core.grants import calls_by_grant, record_call

    grant = await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)

    await record_call(clean, live, "run_readonly_query", refused="profile")

    call = (await calls_by_grant(clean, grant.grant_id))[0]
    assert call.refused == "profile"


async def test_a_failing_audit_write_does_not_take_the_read_surface_down(clean) -> None:
    """Monitoring causing the outage it exists to detect. A missing audit row
    is a smaller problem than a guest's client failing on a query that
    worked."""
    from meridian_core.grants import record_call

    live = ResolvedGrant(
        grant_id=-1,  # no such grant: the foreign key will refuse this row
        subject=SUBJECT,
        subject_kind="person",
        profile="reader",
        topics=(),
        max_source_tier=None,
        raw_files=False,
    )

    await record_call(clean, live, "search_chunks")  # must not raise
    await clean.rollback()


async def test_the_rate_limit_counts_a_token_not_a_person(clean) -> None:
    """One misbehaving laptop must not silence the same person's phone. What
    is being limited is a client in a retry loop — §6's point is that it is
    otherwise indistinguishable from a crawl — and that is a property of the
    client."""
    from meridian_core.grants import record_call, within_rate_limit

    await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)

    for _ in range(3):
        await record_call(clean, live, "search_chunks", token_id=1)

    assert await within_rate_limit(clean, live, token_id=1, limit=3, now=NOW) is False
    assert await within_rate_limit(clean, live, token_id=2, limit=3, now=NOW) is True


async def test_refused_calls_do_not_count_against_the_limit(clean) -> None:
    """Rate-limiting somebody for calls they were not allowed to make turns one
    misconfiguration into two, and the refusals are already visible."""
    from meridian_core.grants import record_call, within_rate_limit

    await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)

    for _ in range(5):
        await record_call(clean, live, "run_readonly_query", token_id=1, refused="profile")

    assert await within_rate_limit(clean, live, token_id=1, limit=3, now=NOW) is True


async def test_calls_outside_the_window_do_not_count(clean) -> None:
    from meridian_core.grants import within_rate_limit
    from meridian_core.models import GrantAudit

    grant = await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)
    for _ in range(5):
        clean.add(
            GrantAudit(
                grant_id=grant.grant_id,
                token_id=1,
                tool="search_chunks",
                at=NOW - dt.timedelta(hours=2),
            )
        )
    await clean.flush()

    assert await within_rate_limit(clean, live, token_id=1, limit=3, now=NOW) is True


async def test_no_limit_means_no_limit(clean) -> None:
    """The opposite of `budget.py`'s convention, and right here: a rate limit
    throttles something already authorised, so a token issued without one is a
    decision somebody made. A budget with no cap is a decision nobody made."""
    from meridian_core.grants import within_rate_limit

    await a_grant(clean)
    live = await resolve_grant(clean, SUBJECT, now=NOW)

    assert await within_rate_limit(clean, live, token_id=1, limit=None, now=NOW) is True
    assert await within_rate_limit(clean, live, token_id=1, limit=0, now=NOW) is False
