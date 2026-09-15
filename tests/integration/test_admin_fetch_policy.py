"""`/api/admin/fetch-policy` (task P6-22, spec §6.4, §13.2, §2.6).

The one admin surface whose changes reach somebody else's server, which is why
the tests here are mostly about what it **refuses**.

**The safety guards are not form fields.** `ResolvedPolicy` carries the SSRF
protections — private addresses, cloud metadata, scheme and redirect rules — and
a browser form that could switch one off would be the worst change available in
this system, one click away from controls about politeness. `respect_robots` and
`user_agent` are excluded for a different reason: a crawler that can stop
honouring robots.txt, or change who it says it is, from a web form is one whose
operator did not decide that.

**The global row needs a confirmation the server checks.** It is the only edit
here whose blast radius is the entire crawl, and a client-side dialog is a
promise rather than a check.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import FetchPolicy
from meridian_core.policy import GLOBAL_DOMAIN, RENDER_JS_THRESHOLD

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def domain() -> str:
    return f"policy-{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def row(session_for, domain: str):
    """One committed row this file owns, plus the global row put back afterwards.

    The global row is the dev database's real crawl configuration, so a test
    that edited it and walked away would re-tune somebody's crawler.
    """
    sess = await session_for("rw")
    await sess.rollback()
    glob = await sess.get(FetchPolicy, GLOBAL_DOMAIN)
    before = dict(glob.settings or {}) if glob else None

    sess.add(FetchPolicy(domain=domain, settings={}, status="active"))
    await sess.commit()

    yield sess

    await sess.rollback()
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain == domain))
    if before is not None:
        current = await sess.get(FetchPolicy, GLOBAL_DOMAIN)
        current.settings = before
    await sess.commit()


async def fetch_row(client, domain: str) -> dict:
    body = (await client.get(f"/api/admin/fetch-policy?q={domain}")).json()
    return next(r for r in body["rows"] if r["policy"]["domain"] == domain)


async def stored(session_for, domain: str) -> FetchPolicy:
    sess = await session_for("rw")
    await sess.rollback()
    return (
        await sess.scalars(select(FetchPolicy).where(FetchPolicy.domain == domain))
    ).one()


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "block_private_addresses",
        "block_cloud_metadata",
        "allowed_schemes",
        "require_https_final",
        "block_mixed_dns",
        "revalidate_each_redirect",
        "respect_robots",
        "user_agent",
    ],
)
async def test_a_guard_or_an_identity_claim_cannot_be_changed_here(
    client, open_admin, row, domain, key
) -> None:
    response = await client.patch(f"/api/admin/fetch-policy/{domain}", json={"settings": {key: 1}})

    assert response.status_code == 422
    assert key in response.json()["detail"]


async def test_the_refusal_says_where_those_belong(client, open_admin, row, domain) -> None:
    # "Not allowed" alone sends somebody looking for a permission they do not
    # have. The message has to name the reason, because the fix is to edit the
    # deployment rather than to find another button.
    response = await client.patch(
        f"/api/admin/fetch-policy/{domain}", json={"settings": {"respect_robots": False}}
    )

    assert "deployment settings" in response.json()["detail"]


async def test_a_value_outside_its_bounds_is_refused(client, open_admin, row, domain) -> None:
    # §2.6: all writes validate server-side. The bounds already exist on
    # `ResolvedPolicy`; a negative delay that reached the crawler unchecked would
    # fail at whatever hour the domain came up next.
    response = await client.patch(
        f"/api/admin/fetch-policy/{domain}", json={"settings": {"delay_per_domain_ms": -5}}
    )

    assert response.status_code == 422
    assert "delay_per_domain_ms" in response.json()["detail"]


async def test_a_zero_timeout_is_refused(client, open_admin, row, domain) -> None:
    response = await client.patch(
        f"/api/admin/fetch-policy/{domain}", json={"settings": {"timeout_s": 0}}
    )

    assert response.status_code == 422


async def test_editing_the_global_row_needs_confirmation(client, open_admin, row) -> None:
    response = await client.patch(
        f"/api/admin/fetch-policy/{GLOBAL_DOMAIN}", json={"settings": {"timeout_s": 31}}
    )

    assert response.status_code == 422
    assert "every domain" in response.json()["detail"]


async def test_a_confirmed_global_edit_goes_through(client, open_admin, row, session_for) -> None:
    # The converse. Without it the test above would pass against a surface that
    # refused the global row outright, which is a different and less useful
    # design — the global row is where the defaults live.
    response = await client.patch(
        f"/api/admin/fetch-policy/{GLOBAL_DOMAIN}",
        json={"settings": {"timeout_s": 31}, "confirm": True},
    )

    assert response.status_code == 200
    assert (await stored(session_for, GLOBAL_DOMAIN)).settings["timeout_s"] == 31


async def test_a_per_domain_edit_needs_no_confirmation(client, open_admin, row, domain) -> None:
    response = await client.patch(
        f"/api/admin/fetch-policy/{domain}", json={"settings": {"timeout_s": 45}}
    )

    assert response.status_code == 200


async def test_an_unknown_domain_is_a_404(client, open_admin, row) -> None:
    assert (await client.patch("/api/admin/fetch-policy/nope.test", json={})).status_code == 404


# --------------------------------------------------------------------------
# What it does
# --------------------------------------------------------------------------


async def test_settings_merge_rather_than_replace(
    client, open_admin, row, domain, session_for
) -> None:
    # A full-replacement PATCH from a form that rendered only some keys is how a
    # delay somebody tuned disappears without anybody touching it.
    await client.patch(f"/api/admin/fetch-policy/{domain}", json={"settings": {"timeout_s": 45}})

    await client.patch(
        f"/api/admin/fetch-policy/{domain}", json={"settings": {"max_retries": 4}}
    )
    settings = (await stored(session_for, domain)).settings

    assert settings == {"timeout_s": 45, "max_retries": 4}


async def test_the_row_reports_what_it_overrides(client, open_admin, row, domain) -> None:
    # The difference between "this domain is slow" and "everything is slow",
    # which is not visible from the resolved values alone.
    await client.patch(f"/api/admin/fetch-policy/{domain}", json={"settings": {"timeout_s": 45}})

    assert (await fetch_row(client, domain))["overridden"] == ["timeout_s"]


async def test_the_resolved_policy_shows_inherited_values_too(
    client, open_admin, row, domain
) -> None:
    # Every key, including the ones this screen refuses to edit. Showing only
    # the editable ones would misrepresent what the crawler is actually doing.
    resolved = (await fetch_row(client, domain))["resolved"]

    assert "block_private_addresses" in resolved
    assert resolved["delay_per_domain_ms"] >= 0


async def test_an_edit_records_who_made_it(client, open_admin, row, domain, session_for) -> None:
    await client.patch(f"/api/admin/fetch-policy/{domain}", json={"settings": {"timeout_s": 45}})
    saved = await stored(session_for, domain)

    assert saved.updated_by == "user"
    assert saved.updated_at is not None


async def test_unblocking_clears_the_failure_count_too(
    client, open_admin, row, domain, session_for
) -> None:
    # Either alone is a trap. Clearing the status without the counter leaves the
    # domain one failure from being blocked again, which reads as the unblock
    # not having worked; clearing the counter without the status leaves it
    # blocked with nothing explaining why.
    sess = await session_for("rw")
    await sess.rollback()
    blocked = await sess.get(FetchPolicy, domain)
    blocked.status = "blocked"
    blocked.consecutive_failures = 9
    await sess.commit()

    await client.post(f"/api/admin/fetch-policy/{domain}/unblock")
    saved = await stored(session_for, domain)

    assert saved.status == "active"
    assert saved.consecutive_failures == 0


async def test_blocked_domains_come_first(client, open_admin, row, domain, session_for) -> None:
    # The one an operator came to find: it is consuming no crawl budget and
    # producing no sources, and §6.4's auto-blocking means it can appear without
    # anybody choosing it.
    sess = await session_for("rw")
    await sess.rollback()
    (await sess.get(FetchPolicy, domain)).status = "blocked"
    await sess.commit()

    rows = (await client.get("/api/admin/fetch-policy?limit=200")).json()["rows"]

    assert rows[0]["policy"]["status"] == "blocked"


# --------------------------------------------------------------------------
# What the crawl learned (`P1-27`)
# --------------------------------------------------------------------------


async def test_a_learned_domain_reads_as_rendering_without_being_configured(
    client, open_admin, row, domain, session_for
) -> None:
    # The distinction the screen exists to make legible: the crawler will use a
    # browser, and nobody set that. An operator seeing `render_js: always` with
    # no override needs the learned columns beside it to know why.
    sess = await session_for("rw")
    await sess.rollback()
    learned = await sess.get(FetchPolicy, domain)
    learned.render_js_escalations = RENDER_JS_THRESHOLD
    learned.render_js_learned_at = dt.datetime.now(dt.UTC)
    await sess.commit()

    found = await fetch_row(client, domain)

    assert found["resolved"]["render_js"] == "always"
    assert found["overridden"] == []
    assert found["policy"]["render_js_escalations"] == RENDER_JS_THRESHOLD


async def test_forgetting_the_learning_returns_the_domain_to_auto(
    client, open_admin, row, domain, session_for
) -> None:
    # For a site that dropped its JavaScript shell today, where waiting a week
    # for the expiry is a week of browser launches that were not needed.
    sess = await session_for("rw")
    await sess.rollback()
    learned = await sess.get(FetchPolicy, domain)
    learned.render_js_escalations = RENDER_JS_THRESHOLD
    learned.render_js_learned_at = dt.datetime.now(dt.UTC)
    await sess.commit()

    body = (await client.post(f"/api/admin/fetch-policy/{domain}/forget-render")).json()

    assert body["resolved"]["render_js"] == "auto"
    assert (await stored(session_for, domain)).render_js_escalations == 0
