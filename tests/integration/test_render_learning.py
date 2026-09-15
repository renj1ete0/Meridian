"""Domains learn they need a browser (task P1-27, spec §6.4).

`render_js: auto` fetches statically first and re-fetches through the browser
when the HTML turns out to be a shell. That order is right for a corpus of
mostly-static pages, and it has no memory — so a JS-only domain pays both
requests on every page forever, and those requests queue in the same per-domain
rate-limit slot the pages do. The cost is not bandwidth, it is crawl throughput
on exactly the domains that are already slowest.

Three properties carry this file, and the third is the one that makes the
feature safe to leave running unattended.

**Consecutive, not cumulative.** One static fetch that turned out to be enough
puts the domain back to zero. A running total would keep treating a redesigned
site as though it had not changed.

**Configuration wins.** Learning fills the gap where nobody decided; it does not
overrule somebody who did.

**The conclusion expires.** A domain skipping the static fetch produces no
evidence about itself, so without an expiry the first correct conclusion becomes
permanent and a redesign can never be noticed. That is the trap the obvious
version of this falls into, and it is silent: the crawl keeps working, on the
expensive path, forever.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete

from meridian_core.models import FetchPolicy
from meridian_core.policy import (
    RENDER_JS_THRESHOLD,
    RENDER_JS_TTL,
    learned_render_js,
    record_render_outcome,
    resolve_policy,
)

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def domain() -> str:
    return f"learn-{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def policy_row(session_for, domain: str):
    sess = await session_for("rw")
    row = FetchPolicy(domain=domain, settings={}, status="active")
    sess.add(row)
    await sess.flush()

    yield sess, row

    await sess.rollback()
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain == domain))
    await sess.commit()


async def escalate(sess, domain: str, times: int, *, now: dt.datetime = NOW) -> None:
    for _ in range(times):
        await record_render_outcome(sess, domain, escalated=True, now=now)


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


async def test_one_escalation_teaches_nothing(policy_row, domain) -> None:
    # Two is a coincidence. A single JS-heavy page on an otherwise static site
    # must not send every other page on it through a browser.
    sess, row = policy_row

    await escalate(sess, domain, 1)

    assert row.render_js_escalations == 1
    assert learned_render_js(row, now=NOW) is False


async def test_enough_consecutive_escalations_teach_it(policy_row, domain) -> None:
    sess, row = policy_row

    await escalate(sess, domain, RENDER_JS_THRESHOLD)

    assert learned_render_js(row, now=NOW) is True
    assert row.render_js_learned_at == NOW


async def test_a_static_success_resets_the_count(policy_row, domain) -> None:
    # Consecutive, not cumulative. A running total would keep treating a
    # redesigned site as though it had not changed.
    sess, row = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD - 1)

    await record_render_outcome(sess, domain, escalated=False, now=NOW)

    assert row.render_js_escalations == 0


async def test_a_static_success_undoes_a_conclusion(policy_row, domain) -> None:
    sess, row = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD)

    await record_render_outcome(sess, domain, escalated=False, now=NOW)

    assert row.render_js_learned_at is None
    assert learned_render_js(row, now=NOW) is False


async def test_a_domain_with_no_policy_row_is_not_given_one(session_for, domain) -> None:
    # Otherwise `fetch_policy` fills with a row per domain the frontier ever
    # touched — a table of configuration nobody wrote, in the screen where an
    # operator looks for the configuration they did.
    sess = await session_for("rw")

    await record_render_outcome(sess, f"absent-{domain}", escalated=True, now=NOW)

    assert await sess.get(FetchPolicy, f"absent-{domain}") is None


# --------------------------------------------------------------------------
# The conclusion expires
# --------------------------------------------------------------------------


async def test_the_conclusion_holds_inside_its_window(policy_row, domain) -> None:
    sess, row = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD)

    assert learned_render_js(row, now=NOW + RENDER_JS_TTL - dt.timedelta(hours=1)) is True


async def test_the_conclusion_lapses_after_its_window(policy_row, domain) -> None:
    # The trap the obvious version of this falls into, and it is silent: a
    # domain skipping the static fetch produces no evidence about itself, so the
    # first correct conclusion becomes permanent and the crawl keeps working —
    # on the expensive path, forever.
    sess, row = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD)

    assert learned_render_js(row, now=NOW + RENDER_JS_TTL + dt.timedelta(seconds=1)) is False


async def test_a_re_probe_that_confirms_renews_the_conclusion(policy_row, domain) -> None:
    sess, row = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD)
    later = NOW + RENDER_JS_TTL + dt.timedelta(days=1)

    await record_render_outcome(sess, domain, escalated=True, now=later)

    assert row.render_js_learned_at == later
    assert learned_render_js(row, now=later) is True


# --------------------------------------------------------------------------
# What the fetcher is actually told
# --------------------------------------------------------------------------


async def test_a_learned_domain_resolves_to_always(policy_row, domain) -> None:
    sess, _ = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD, now=dt.datetime.now(dt.UTC))

    resolved = await resolve_policy(sess, domain)

    assert resolved.render_js == "always"


async def test_an_unlearned_domain_still_resolves_to_auto(policy_row, domain) -> None:
    # The converse, without which the test above would pass against a resolver
    # that sent everything to the browser.
    sess, _ = policy_row
    await escalate(sess, domain, 1, now=dt.datetime.now(dt.UTC))

    resolved = await resolve_policy(sess, domain)

    assert resolved.render_js == "auto"


async def test_an_explicit_setting_is_not_overruled(policy_row, domain) -> None:
    # Learning fills the gap where nobody decided. An operator who set `never`
    # on a domain — a paywall, a site whose browser path loops — must not find
    # the crawl doing it anyway.
    sess, row = policy_row
    row.settings = {"render_js": "never"}
    await escalate(sess, domain, RENDER_JS_THRESHOLD, now=dt.datetime.now(dt.UTC))

    resolved = await resolve_policy(sess, domain)

    assert resolved.render_js == "never"


async def test_a_lapsed_conclusion_resolves_back_to_auto(policy_row, domain) -> None:
    sess, row = policy_row
    await escalate(sess, domain, RENDER_JS_THRESHOLD, now=NOW)
    row.render_js_learned_at = dt.datetime.now(dt.UTC) - RENDER_JS_TTL - dt.timedelta(days=1)
    await sess.flush()

    resolved = await resolve_policy(sess, domain)

    assert resolved.render_js == "auto"
