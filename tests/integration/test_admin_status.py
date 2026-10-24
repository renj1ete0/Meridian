"""What the stack depends on beyond itself, over HTTP (task `B-228`).

The embedder and the local models live on another machine that may be off; this route is
how the operator sees which. The probes are replaced: what is tested is which rows are
asked, what each answer becomes, and that the backlog is the same number the backfill uses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select

from api.main import create_app
from api.routes import admin
from meridian_core.chunks import embedding_backlog
from meridian_core.db import dispose_engines
from meridian_core.embedder import RemoteEmbedder
from meridian_core.models import Agent

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def registry(session_for):
    """Only the two local rows enabled, everything put back afterwards."""
    sess = await session_for("rw")
    rows = list(await sess.scalars(select(Agent)))
    before = {a.agent_id: a.enabled for a in rows}
    for agent in rows:
        agent.enabled = agent.agent_id in {"local-llamacpp", "local-chat", "hosted-frontier"}
    await sess.commit()
    yield
    for agent in rows:
        agent.enabled = before[agent.agent_id]
    await sess.commit()


@pytest.fixture
def no_embedder(monkeypatch) -> None:
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)


async def test_the_status_is_behind_the_admin_gate(client, monkeypatch) -> None:
    """It names machines on the LAN; the public `/health` stays liveness only."""
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)

    assert (await client.get("/api/admin/status")).status_code == 503


async def test_an_unconfigured_embedder_is_said_apart_from_a_down_one(
    client, open_admin, registry, monkeypatch
) -> None:
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)
    body = (await client.get("/api/admin/status")).json()
    assert body["embedder"] == {
        "configured": False,
        "up": None,
        "model": None,
        "loaded": None,
        "rerank_model": None,
    }

    async def silent(self):
        return None

    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", "http://gpu.test:8100")
    monkeypatch.setattr(RemoteEmbedder, "describe", silent)
    body = (await client.get("/api/admin/status")).json()
    assert body["embedder"]["configured"] is True
    assert body["embedder"]["up"] is False


async def test_an_embedder_that_answers_says_what_it_runs(
    client, open_admin, registry, monkeypatch
) -> None:
    async def described(self):
        return {"status": "ok", "model": "bge-m3", "loaded": True, "rerank_model": None}

    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", "http://gpu.test:8100")
    monkeypatch.setattr(RemoteEmbedder, "describe", described)

    embedder = (await client.get("/api/admin/status")).json()["embedder"]

    assert embedder["up"] is True
    assert embedder["model"] == "bge-m3"
    assert embedder["loaded"] is True


async def test_each_enabled_local_model_is_reported_and_hosted_ones_are_not(
    client, open_admin, registry, no_embedder, monkeypatch
) -> None:
    """The probed row says why it is down; the row with no health check says it was not asked."""
    monkeypatch.setenv("LOCAL_LLM_HEALTH_URL", "http://gpu.test:8080/health")
    asked: list[str] = []

    async def loading(agent):
        asked.append(agent.agent_id)
        return f"{agent.agent_id}: not ready (health check answered 503)"

    monkeypatch.setattr(admin, "not_up", loading)

    models = (await client.get("/api/admin/status")).json()["local_models"]

    assert asked == ["local-llamacpp"], "only a row with a health check is probed"
    assert models == [
        {
            "agent_id": "local-chat",
            "availability": "opportunistic",
            "probed": False,
            "up": None,
            "reason": "no health check configured",
        },
        {
            "agent_id": "local-llamacpp",
            "availability": "opportunistic",
            "probed": True,
            "up": False,
            "reason": "local-llamacpp: not ready (health check answered 503)",
        },
    ]


async def test_a_model_that_answers_is_up_with_no_reason(
    client, open_admin, registry, no_embedder, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_LLM_HEALTH_URL", "http://gpu.test:8080/health")

    async def ready(agent):
        return None

    monkeypatch.setattr(admin, "not_up", ready)
    models = (await client.get("/api/admin/status")).json()["local_models"]
    probed = [m for m in models if m["probed"]]
    assert probed == [
        {
            "agent_id": "local-llamacpp",
            "availability": "opportunistic",
            "probed": True,
            "up": True,
            "reason": None,
        }
    ]


async def test_the_backlog_is_the_number_the_backfill_works_from(
    client, open_admin, registry, no_embedder, session_for
) -> None:
    sess = await session_for("ro")
    expected = await embedding_backlog(sess)
    body = (await client.get("/api/admin/status")).json()
    assert body["embedding_backlog"] == expected
    assert body["checked_at"].endswith("Z") or "+00:00" in body["checked_at"]
