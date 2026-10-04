"""Admin → Assistant access (task `B-146`, ADRs 0003 and 0011)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.grants import PROFILE_TOOLS
from meridian_core.models import AgentToken
from meridian_core.tokens import hash_token

pytestmark = pytest.mark.usefixtures("require_db")

HELD = "test-admin-tokens"


@pytest.fixture
async def client(monkeypatch, session_for):
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    sess = await session_for("rw")
    await sess.execute(delete(AgentToken).where(AgentToken.agent_id == HELD))
    await sess.commit()
    await dispose_engines()


async def issue(client, **body) -> dict:
    response = await client.post("/api/admin/tokens", json={"held_by": HELD, **body})
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_secret_is_in_the_issuing_response_only(client, session_for) -> None:
    issued = await issue(client)
    secret = issued["secret"]
    stored = await (await session_for("ro")).scalar(
        select(AgentToken.token_hash).where(AgentToken.token_id == issued["row"]["token_id"])
    )
    assert stored == hash_token(secret)
    listing = (await client.get("/api/admin/tokens")).text
    assert secret not in listing and stored not in listing


async def test_a_token_carries_its_profiles_tools_and_says_which(client) -> None:
    row = (await issue(client, profile="analyst"))["row"]
    assert row["profile"] == "analyst"
    assert set(row["tools"]) == PROFILE_TOOLS["analyst"]
    assert row["expires_at"] is not None and row["no_expiry"] is False


async def test_a_token_that_never_expires_is_flagged(client) -> None:
    """ADR 0011: allowed, and visible as a choice."""
    row = (await issue(client, days=None))["row"]
    assert row["expires_at"] is None and row["no_expiry"] is True


async def test_revoking_hides_it_unless_asked(client) -> None:
    token_id = (await issue(client))["row"]["token_id"]
    revoked = await client.post(f"/api/admin/tokens/{token_id}/revoke")
    assert revoked.json()["state"] == "revoked"
    ids = [r["token_id"] for r in (await client.get("/api/admin/tokens")).json()["rows"]]
    assert token_id not in ids
    every = (await client.get("/api/admin/tokens", params={"all": "true"})).json()["rows"]
    assert token_id in [r["token_id"] for r in every]
    assert (await client.post("/api/admin/tokens/999999999/revoke")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"profile": "operator-with-writes"},
        {"days": 0},
        {"days": 5000},
        {"held_by": ""},
        {"held_by": "<script>"},
        {"scopes": ["add_edge"]},
    ],
)
async def test_what_cannot_be_issued_is_refused(client, body) -> None:
    payload = {"held_by": HELD, **body}
    assert (await client.post("/api/admin/tokens", json=payload)).status_code == 422


async def test_the_list_offers_the_profiles_and_no_write_tool(client) -> None:
    body = (await client.get("/api/admin/tokens")).json()
    assert set(body["profiles"]) == set(PROFILE_TOOLS)
    tools = {t for ts in body["profiles"].values() for t in ts}
    assert not {"add_edge", "tag_entity", "enqueue_seed", "advance_mark"} & tools


async def test_admin_is_gated(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        # Closed, naming the fix, when no caller identity is configured (`deps.admin_is_allowed`).
        assert (await c.get("/api/admin/tokens")).status_code == 503
    await dispose_engines()
