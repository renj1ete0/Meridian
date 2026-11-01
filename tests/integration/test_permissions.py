"""People's permissions, end to end (task B-252, ADR 0024).

Through the real Access middleware with a local signing key standing in for Cloudflare's, so
each request carries a verified identity as in production; grants in the real database. The
cases are mostly refusals: a person without a grant, a grant that ended, a permission switched
off, an operator managing people, a change that would lock its author out.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

import api.main
from api.access import AccessVerifier
from api.main import create_app
from meridian_core import people
from meridian_core.db import dispose_engines
from meridian_core.models import Grant, GrantChange

pytestmark = pytest.mark.usefixtures("require_db")

TEAM = "team.example.test"
AUD = "aud-permissions-test"
DOMAIN = "perm.test"
OWNER = f"owner@{DOMAIN}"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Keys:
    def get_signing_key_from_jwt(self, token: str):
        class _Signing:
            key = KEY.public_key()

        return _Signing()


def as_(email: str) -> dict[str, str]:
    now = dt.datetime.now(dt.UTC)
    claims = {
        "aud": AUD,
        "iss": f"https://{TEAM}",
        "iat": now,
        "exp": now + dt.timedelta(minutes=10),
        "email": email,
    }
    return {"Cf-Access-Jwt-Assertion": jwt.encode(claims, KEY, algorithm="RS256")}


def person(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:6]}@{DOMAIN}"


@pytest.fixture
async def client(monkeypatch, session_for):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "")
    monkeypatch.setenv("MERIDIAN_OWNER_EMAILS", OWNER)
    monkeypatch.setattr(api.main, "AccessVerifier", lambda s: AccessVerifier(s, jwk_client=_Keys()))
    people.forget_setup_code()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    sess = await session_for("rw")
    await sess.execute(delete(Grant).where(Grant.subject.like(f"%@{DOMAIN}")))
    await sess.commit()
    people.forget_setup_code()
    await dispose_engines()


async def grant(client, subject: str, **body) -> dict:
    response = await client.post(
        "/api/admin/people", json={"subject": subject, **body}, headers=as_(OWNER)
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# Who may do what
# --------------------------------------------------------------------------


async def test_an_owner_holds_everything_and_is_listed_from_the_environment(client) -> None:
    me = (await client.get("/api/me", headers=as_(OWNER))).json()
    assert me["owner"] is True and "access.manage" in me["permissions"]
    listed = (await client.get("/api/admin/people", headers=as_(OWNER))).json()
    (row,) = [p for p in listed["people"] if p["subject"] == OWNER]
    assert row["profile"] == "owner" and row["grant_id"] is None


async def test_an_owner_matches_whatever_case_access_reports(client) -> None:
    me = (await client.get("/api/me", headers=as_(OWNER.upper()))).json()
    assert me["owner"] is True


async def test_someone_access_let_in_without_a_grant_sees_nothing(client) -> None:
    """Cloudflare decides who reaches the site; reaching it is not reading it."""
    stranger = person("stranger")
    me = (await client.get("/api/me", headers=as_(stranger))).json()
    assert me["permissions"] == [] and me["profile"] is None
    assert (await client.get("/api/explore/stats", headers=as_(stranger))).status_code == 403
    assert (await client.get("/api/admin/status", headers=as_(stranger))).status_code == 403


async def test_a_reader_reads_and_is_refused_admin_by_name(client) -> None:
    reader = person("reader")
    await grant(client, reader, profile="reader")
    assert (await client.get("/api/explore/stats", headers=as_(reader))).status_code == 200
    refused = await client.get("/api/admin/status", headers=as_(reader))
    assert refused.status_code == 403
    assert "admin.view" in refused.json()["detail"]


async def test_a_switch_adds_one_permission_and_another_takes_one_away(client) -> None:
    analyst = person("analyst")
    await grant(client, analyst, profile="analyst", allow=["seeds.add"], deny=["chat.ask"])
    me = (await client.get("/api/me", headers=as_(analyst))).json()
    assert "seeds.add" in me["permissions"] and "chat.ask" not in me["permissions"]
    # Seeds are allowed through the gate (the body is then judged on its merits)...
    seeded = await client.post("/api/admin/seeds", json={}, headers=as_(analyst))
    assert seeded.status_code != 403
    # ...and Ask is refused before any model is asked.
    asked = await client.post("/api/admin/chat/ask", json={"question": "x"}, headers=as_(analyst))
    assert asked.status_code == 403
    # Topics stay the operator's.
    steer = await client.patch("/api/admin/topics/x", json={}, headers=as_(analyst))
    assert steer.status_code == 403


async def test_an_operator_does_not_manage_people_unless_switched_on(client) -> None:
    operator = person("operator")
    made = await grant(client, operator, profile="operator")
    assert (await client.get("/api/admin/people", headers=as_(operator))).status_code == 403
    assert (await client.get("/api/admin/tokens", headers=as_(operator))).status_code == 403
    await client.patch(
        f"/api/admin/people/{made['grant_id']}",
        json={"allow": ["access.manage"]},
        headers=as_(OWNER),
    )
    assert (await client.get("/api/admin/people", headers=as_(operator))).status_code == 200


@pytest.mark.parametrize("ended", ["revoked", "expired"])
async def test_a_grant_that_ended_grants_nothing(client, session_for, ended) -> None:
    reader = person("ended")
    made = await grant(client, reader, profile="reader")
    sess = await session_for("rw")
    row = await sess.get(Grant, made["grant_id"])
    if ended == "revoked":
        row.revoked = True
    else:
        row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await sess.commit()
    assert (await client.get("/api/explore/stats", headers=as_(reader))).status_code == 403


# --------------------------------------------------------------------------
# Changing grants
# --------------------------------------------------------------------------


async def test_every_change_is_recorded_with_who_made_it(client, session_for) -> None:
    analyst = person("recorded")
    made = await grant(client, analyst, profile="analyst")
    await client.patch(
        f"/api/admin/people/{made['grant_id']}", json={"deny": ["chat.ask"]}, headers=as_(OWNER)
    )
    sess = await session_for("ro")
    changes = (
        await sess.scalars(
            select(GrantChange)
            .where(GrantChange.grant_id == made["grant_id"])
            .order_by(GrantChange.change_id)
        )
    ).all()
    assert [c.actor for c in changes] == [OWNER, OWNER]
    assert changes[0].before is None
    assert changes[1].before["deny"] == [] and changes[1].after["deny"] == ["chat.ask"]


async def test_nobody_removes_their_own_way_back(client) -> None:
    manager = person("manager")
    made = await grant(client, manager, profile="operator", allow=["access.manage"])
    for body in ({"allow": []}, {"deny": ["access.manage"]}, {"revoked": True}):
        refused = await client.patch(
            f"/api/admin/people/{made['grant_id']}", json=body, headers=as_(manager)
        )
        assert refused.status_code == 422, body
        assert "your own access" in refused.json()["detail"]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"subject": OWNER}, "owner"),
        ({"subject": "not-an-email"}, "email"),
        ({"subject": "x@perm.test", "allow": ["root.everything"]}, "unknown permission"),
        ({"subject": "x@perm.test", "profile": "admin"}, ""),
    ],
)
async def test_a_bad_grant_is_refused(client, body, fragment) -> None:
    response = await client.post("/api/admin/people", json=body, headers=as_(OWNER))
    assert response.status_code == 422
    assert fragment in response.text


async def test_a_second_live_grant_for_one_person_is_refused(client) -> None:
    reader = person("twice")
    await grant(client, reader)
    response = await client.post("/api/admin/people", json={"subject": reader}, headers=as_(OWNER))
    assert response.status_code == 422 and "already" in response.text


async def test_the_table_refuses_an_unknown_switch(session_for) -> None:
    """A switch written around the API (SQL, a future tool) is held to the catalogue."""
    sess = await session_for("rw")
    with pytest.raises(IntegrityError):
        await sess.execute(
            text(
                "INSERT INTO grants (subject, subject_kind, profile, expires_at, allow) "
                "VALUES ('sql@perm.test', 'person', 'reader', now() + interval '1 day', "
                "ARRAY['root.everything'])"
            )
        )


# --------------------------------------------------------------------------
# Claiming a fresh instance
# --------------------------------------------------------------------------


async def test_a_fresh_instance_is_claimed_once_with_the_code_from_the_log(
    client, monkeypatch, session_for
) -> None:
    monkeypatch.setenv("MERIDIAN_OWNER_EMAILS", "")
    sess = await session_for("ro")
    if not await people.setup_needed(sess):
        pytest.skip("this database already has someone who manages access")
    first, second = person("first"), person("second")

    me = (await client.get("/api/me", headers=as_(first))).json()
    assert me["setup_needed"] is True and me["permissions"] == []

    wrong = await client.post("/api/setup/claim", json={"code": "WRONG000"}, headers=as_(first))
    assert wrong.status_code == 403

    claimed = await client.post(
        "/api/setup/claim", json={"code": people.setup_code()}, headers=as_(first)
    )
    assert claimed.status_code == 200, claimed.text
    assert "access.manage" in claimed.json()["permissions"]
    assert (await client.get("/api/admin/people", headers=as_(first))).status_code == 200

    again = await client.post(
        "/api/setup/claim", json={"code": people.setup_code()}, headers=as_(second)
    )
    assert again.status_code == 403
    assert (await client.get("/api/me", headers=as_(second))).json()["setup_needed"] is False


async def test_with_an_owner_named_nothing_waits_to_be_claimed(client) -> None:
    me = (await client.get("/api/me", headers=as_(person("anyone")))).json()
    assert me["setup_needed"] is False


async def test_without_identity_admin_stays_closed_and_reading_stays_open(monkeypatch) -> None:
    """The behaviour before B-252, kept: an instance that checks no identity and did not
    opt out is not an open one."""
    for var in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "MERIDIAN_ADMIN_ALLOW_ANONYMOUS"):
        monkeypatch.delenv(var, raising=False)
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/explore/stats")).status_code == 200
        assert (await c.get("/api/admin/status")).status_code == 503
        assert (await c.get("/api/me")).json()["permissions"] == ["explore.read"]
    await dispose_engines()
