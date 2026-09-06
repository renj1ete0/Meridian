"""Fetch policy resolution (spec §6.4).

Layering is the whole feature: one setting for a large API and a small municipal
server is wrong in one direction or the other, so a per-domain override has to
beat the global row while still inheriting everything it does not mention.

These run against the seeded global row rather than a fixture, because the thing
worth protecting is that the *shipped* defaults resolve correctly.
"""

from __future__ import annotations

import uuid

import pytest
from meridian_core.models import FetchPolicy as FetchPolicyRow
from meridian_core.policy import (
    ResolvedPolicy,
    file_defaults,
    merge_layers,
    record_failure,
    record_success,
    resolve_policy,
)
from pydantic import ValidationError
from sqlalchemy import delete, select

pytestmark = pytest.mark.usefixtures("require_db")


def _domain() -> str:
    """Unique per test: these rows must be committed, so a killed run would
    otherwise leave a per-domain override behind and poison later runs."""
    return f"policy-test-{uuid.uuid4().hex[:8]}.example"


# ---------------------------------------------------------------- pure merging


def test_earlier_layers_win() -> None:
    assert merge_layers({"timeout_s": 5}, {"timeout_s": 30, "max_retries": 2}) == {
        "timeout_s": 5,
        "max_retries": 2,
    }


def test_merge_is_shallow_so_an_override_replaces_a_list() -> None:
    """Deep-merging would make an override *extend* the default list, producing
    a policy nobody wrote — an `allowed_schemes` override that still permits the
    scheme it was written to forbid."""
    merged = merge_layers({"allowed_schemes": ["https"]}, {"allowed_schemes": ["http", "https"]})
    assert merged["allowed_schemes"] == ["https"]


def test_none_does_not_overwrite_a_real_value() -> None:
    assert merge_layers({"timeout_s": None}, {"timeout_s": 30})["timeout_s"] == 30


def test_file_defaults_are_present() -> None:
    """The floor exists so a worker started against an unseeded database fails
    predictably rather than with a KeyError mid-fetch."""
    assert file_defaults()["timeout_s"] > 0


# ------------------------------------------------------------------- layering


async def test_unknown_domain_inherits_the_global_row(session_for) -> None:
    sess = await session_for("rw")
    policy = await resolve_policy(sess, _domain())
    assert policy.concurrency_per_domain >= 1
    assert policy.respect_robots is True
    assert policy.is_fetchable, "a domain with no row of its own must be active"


async def test_per_domain_row_beats_the_global_row(session_for) -> None:
    domain = _domain()
    sess = await session_for("rw")
    baseline = await resolve_policy(sess, domain)

    sess.add(
        FetchPolicyRow(
            domain=domain,
            settings={"concurrency_per_domain": 1, "delay_per_domain_ms": 9000},
            status="active",
        )
    )
    await sess.commit()
    try:
        sess2 = await session_for("rw")
        policy = await resolve_policy(sess2, domain)
        assert policy.concurrency_per_domain == 1
        assert policy.delay_per_domain_ms == 9000
        # Unmentioned keys still come from the layers underneath.
        assert policy.timeout_s == baseline.timeout_s
        assert policy.respect_robots == baseline.respect_robots
    finally:
        cleanup = await session_for("rw")
        await cleanup.execute(delete(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        await cleanup.commit()


async def test_source_tiers_does_not_leak_into_the_policy(session_for) -> None:
    """The tier map rides in the global row but is not a fetch setting."""
    sess = await session_for("rw")
    policy = await resolve_policy(sess, _domain())
    assert not hasattr(policy, "source_tiers") or policy.model_dump().get("source_tiers") is None


async def test_global_status_is_not_inherited(session_for) -> None:
    """Blocking '*' must not silently stop the entire crawl.

    A per-domain row can be blocked; a domain with no row of its own stays
    active even though it inherits the global row's settings.
    """
    sess = await session_for("rw")
    glob = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == "*"))
    assert glob is not None, "global row missing — was the database seeded?"
    assert (await resolve_policy(sess, _domain())).is_fetchable


async def test_blocked_domain_is_not_fetchable(session_for) -> None:
    domain = _domain()
    sess = await session_for("rw")
    sess.add(FetchPolicyRow(domain=domain, settings={}, status="blocked"))
    await sess.commit()
    try:
        sess2 = await session_for("rw")
        assert (await resolve_policy(sess2, domain)).is_fetchable is False
    finally:
        cleanup = await session_for("rw")
        await cleanup.execute(delete(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        await cleanup.commit()


# ------------------------------------------------------- failure accumulation


async def test_domain_blocks_only_at_the_threshold(session_for) -> None:
    """One dead site must not consume crawl budget for weeks unnoticed (§6.4),
    but a single blip must not block a good domain either."""
    domain = _domain()
    try:
        for expected_block in (False, False, True):
            sess = await session_for("rw")
            blocked = await record_failure(sess, domain, blocked_after=3)
            await sess.commit()
            assert blocked is expected_block

        sess = await session_for("rw")
        row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        assert row.status == "blocked"
        assert row.consecutive_failures == 3
    finally:
        cleanup = await session_for("rw")
        await cleanup.execute(delete(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        await cleanup.commit()


async def test_success_resets_the_counter(session_for) -> None:
    """Only *consecutive* failures block, so an intermittent domain survives."""
    domain = _domain()
    try:
        sess = await session_for("rw")
        await record_failure(sess, domain, blocked_after=3)
        await record_failure(sess, domain, blocked_after=3)
        await sess.commit()

        sess2 = await session_for("rw")
        await record_success(sess2, domain)
        await sess2.commit()

        sess3 = await session_for("rw")
        row = await sess3.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        assert row.consecutive_failures == 0
        assert row.status == "active"
    finally:
        cleanup = await session_for("rw")
        await cleanup.execute(delete(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        await cleanup.commit()


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "bad",
    [
        {"concurrency_per_domain": 0},
        {"timeout_s": 0},
        {"delay_per_domain_ms": -1},
        {"max_retries": -1},
        {"max_page_bytes": 0},
    ],
)
def test_nonsense_policy_values_are_rejected(bad: dict) -> None:
    """A policy that says zero concurrency would stall the crawler silently."""
    with pytest.raises(ValidationError):
        ResolvedPolicy(domain="x.example", **bad)


def test_jitter_is_applied_to_the_configured_delay() -> None:
    import random

    policy = ResolvedPolicy(domain="x.example", delay_per_domain_ms=1000, delay_jitter_ms=1000)
    draws = {policy.next_delay_ms(random.Random(i)) for i in range(50)}
    assert min(draws) >= 1000
    assert max(draws) <= 2000
    assert len(draws) > 5, "a fixed delay is a fingerprint; jitter must vary it"
