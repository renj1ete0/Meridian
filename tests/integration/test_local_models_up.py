"""Which local models the synthesis daemon watches (task `B-227`, §11.7).

Against the seeded registry, because what is chosen is decided by rows: enabled, the
availability, a health check that resolves, and whether the row does synthesis at all.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from meridian_core.db import dispose_engines
from meridian_core.models import Agent
from meridian_core.provider import forget_agent_outages
from worker import orchestrate

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def rows(session_for):
    """Enable the two seeded local rows, put back afterwards."""
    await dispose_engines()
    sess = await session_for("rw")
    ids = ("local-llamacpp", "local-chat")
    agents = list(await sess.scalars(select(Agent).where(Agent.agent_id.in_(ids))))
    before = {a.agent_id: (a.enabled, a.health_url) for a in agents}
    assert set(before) == set(ids), "the seed no longer has both local rows"
    for agent in agents:
        agent.enabled = True
    await sess.commit()
    yield {a.agent_id: a for a in agents}
    # `local_models_up` opens the shared engine inside this test's loop.
    await dispose_engines()
    for agent in agents:
        agent.enabled, agent.health_url = before[agent.agent_id]
    await sess.commit()


@pytest.fixture
def lan(monkeypatch):
    probed: list[str] = []
    answer = {"status": 200}
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        probed.append(str(request.url))
        return httpx.Response(answer["status"], json={})

    class Routed(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)
    return probed, answer


async def test_the_synthesis_model_is_watched_and_the_chat_model_is_not(
    rows, lan, monkeypatch, session_for
) -> None:
    """The chat row has no health check seeded and does only `chat`; its machine coming
    up is no reason to run synthesis."""
    probed, answer = lan
    monkeypatch.setenv("LOCAL_LLM_HEALTH_URL", "http://gpu.test:8080/health")

    assert await orchestrate.local_models_up() == {"local-llamacpp": True}
    assert probed == ["http://gpu.test:8080/health"]

    answer["status"] = 503
    forget_agent_outages()
    assert await orchestrate.local_models_up() == {"local-llamacpp": False}


async def test_a_chat_row_given_a_health_check_is_still_not_watched(
    rows, lan, monkeypatch, session_for
) -> None:
    sess = await session_for("rw")
    chat = await sess.get(Agent, "local-chat")
    chat.health_url = "http://gpu.test:8081/health"
    await sess.commit()
    monkeypatch.delenv("LOCAL_LLM_HEALTH_URL", raising=False)

    assert await orchestrate.local_models_up() == {}


async def test_an_unset_health_variable_means_nothing_to_watch(rows, lan, monkeypatch) -> None:
    probed, _ = lan
    monkeypatch.delenv("LOCAL_LLM_HEALTH_URL", raising=False)
    assert await orchestrate.local_models_up() == {}
    assert probed == []


async def test_a_disabled_row_is_not_watched(rows, lan, monkeypatch, session_for) -> None:
    monkeypatch.setenv("LOCAL_LLM_HEALTH_URL", "http://gpu.test:8080/health")
    sess = await session_for("rw")
    (await sess.get(Agent, "local-llamacpp")).enabled = False
    await sess.commit()
    assert await orchestrate.local_models_up() == {}
