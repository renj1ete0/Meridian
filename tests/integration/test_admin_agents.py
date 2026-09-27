"""The agent registry and run history over HTTP (task `P6-23`, §11.3, §11.10).

`P6-23` was held open on its own argument: both tables are empty until phase 4
runs something, and "an empty screen teaches nothing about what the full one
should look like". They have rows now — `runs` carries real stages, statuses
and counters, and `agents` is where somebody turns on the agent that produces
the first edge — so the screen can be designed against what is actually there.

The test that matters most is not about either table. It is that a run deferred
at `extract` and a registry full of plausible-looking agents are the same
afternoon, and `unserved_tasks` is the line that ends it: §11.3 routes by task
type, so a type no enabled row declares is a stage that defers every run, and
nothing about any individual row looks wrong because the absence is *between*
them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import Agent, Run
from meridian_core.routing import TASK_TYPES

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


NOW = dt.datetime(2026, 9, 22, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def marker() -> str:
    return f"ag{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def registry(session_for, marker: str):
    """One enabled agent of our own, and one run to look at.

    Rows of its own rather than the seeded ones: the registry ships disabled
    and a test that flipped a shipped row would leave a deployment's own
    configuration changed behind it.
    """
    sess = await session_for("rw")
    await sess.rollback()

    sess.add(
        Agent(
            agent_id=marker,
            provider="anthropic",
            model="test-model-1",
            task_types=["relation_extraction"],
            quality_tier=4,
            enabled=True,
            api_key_env_var="A_VARIABLE_NOBODY_SETS",
        )
    )
    sess.add(
        Run(
            started_at=NOW,
            completed_at=NOW,
            stage="done",
            status="done",
            agent_id=marker,
            tokens_used=1234,
            edges_added=2,
            tags_added=1,
        )
    )
    await sess.commit()

    yield sess

    await sess.rollback()
    await sess.execute(delete(Run).where(Run.agent_id == marker))
    await sess.execute(delete(Agent).where(Agent.agent_id == marker))
    await sess.commit()


async def test_the_registry_lists_its_rows(client, open_admin, registry, marker: str) -> None:
    response = await client.get("/api/admin/agents")

    assert response.status_code == 200
    rows = {row["agent_id"]: row for row in response.json()["rows"]}
    assert marker in rows
    assert rows[marker]["model"] == "test-model-1"
    assert rows[marker]["quality_tier"] == 4


async def test_no_key_ever_reaches_the_response(client, open_admin, registry) -> None:
    """§11.11 keeps credentials out of the database precisely because it is
    snapshotted off-device. A screen that helpfully echoed one would undo that
    from the other end."""
    body = response_text = (await client.get("/api/admin/agents")).text

    assert "api_key" not in body.replace("api_key_env_var", "")
    assert "sk-" not in response_text


async def test_a_row_says_why_it_cannot_be_routed_to(
    client, open_admin, registry, marker: str
) -> None:
    """The variable this row names is not set here, so routing would refuse it.

    Reported as a reason rather than left for somebody to deduce: an enabled
    agent with no key looks identical to a working one until a run defers.
    """
    rows = {r["agent_id"]: r for r in (await client.get("/api/admin/agents")).json()["rows"]}

    assert rows[marker]["key_present"] is False
    assert any("unset" in reason for reason in rows[marker]["blocked_by"])


async def test_the_registry_names_the_task_types_nothing_serves(
    client, open_admin, registry
) -> None:
    """The between-the-rows failure. Every task type no enabled, usable agent
    declares is a stage that will defer, and the registry looks fine."""
    body = (await client.get("/api/admin/agents")).json()

    assert set(body["unserved_tasks"]) <= TASK_TYPES
    # The fixture's agent declares `relation_extraction` and is unusable (no
    # key), so that type must still be reported as unserved.
    assert "relation_extraction" in body["unserved_tasks"]


async def test_enabling_an_agent_is_the_one_write(
    client, open_admin, registry, marker: str
) -> None:
    """Enabling is the switch that starts spending, and the one an operator
    needs immediately. Everything else about a row is deployment config."""
    response = await client.patch(f"/api/admin/agents/{marker}", json={"enabled": False})

    assert response.status_code == 200
    rows = {r["agent_id"]: r for r in response.json()["rows"]}
    assert rows[marker]["enabled"] is False
    assert "disabled" in rows[marker]["blocked_by"]


async def test_the_model_can_be_chosen_from_admin(
    client, open_admin, registry, marker: str
) -> None:
    """`P6-06`, the operator's call: which model a (local) agent runs is chosen
    often and should not wait on a migration. It was refused until then; the
    endpoint and task types still are."""
    response = await client.patch(f"/api/admin/agents/{marker}", json={"model": "qwen-local-7b"})

    assert response.status_code == 200
    rows = {r["agent_id"]: r for r in response.json()["rows"]}
    assert rows[marker]["model"] == "qwen-local-7b"
    assert rows[marker]["enabled"] is True, "changing the model left the switch alone"

    # A variable is kept verbatim, to be read at call time.
    response = await client.patch(
        f"/api/admin/agents/{marker}", json={"model": "${LOCAL_CHAT_MODEL}"}
    )
    assert {r["agent_id"]: r for r in response.json()["rows"]}[marker]["model"] == (
        "${LOCAL_CHAT_MODEL}"
    )


@pytest.mark.parametrize(
    "body",
    [
        {"model": ""},
        {"model": "  padded  "},
        {"model": "x" * 201},
        {"endpoint": "http://elsewhere"},
        {"task_types": ["chat"]},
    ],
)
async def test_what_admin_may_not_write_is_refused(
    client, open_admin, registry, marker: str, body
) -> None:
    response = await client.patch(f"/api/admin/agents/{marker}", json=body)
    assert response.status_code == 422


async def test_an_unknown_agent_is_a_404(client, open_admin, registry) -> None:
    response = await client.patch("/api/admin/agents/nobody", json={"enabled": True})

    assert response.status_code == 404


async def test_run_history_reports_counters_not_a_verdict(
    client, open_admin, registry, marker: str
) -> None:
    """§11.9 compares cost and volume week on week. A column that said
    "successful" would hide the run that finished having written nothing."""
    body = (await client.get("/api/admin/runs", params={"limit": 50})).json()

    ours = [row for row in body["rows"] if row["agent_id"] == marker]
    assert ours, "the seeded run is missing from the history"
    assert ours[0]["tokens_used"] == 1234
    assert ours[0]["edges_added"] == 2
    assert ours[0]["tags_added"] == 1


async def test_run_history_calls_out_the_run_in_flight(
    client, open_admin, registry, marker: str
) -> None:
    """ "Is something happening right now" is the first question this screen is
    opened to answer, and scanning a status column is a worse way to find out."""
    sess = registry
    sess.add(Run(started_at=NOW, stage="extract", status="running", agent_id=marker))
    await sess.commit()

    body = (await client.get("/api/admin/runs")).json()

    assert body["active"] is not None
    assert body["active"]["status"] in ("running", "deferred")


@pytest.mark.parametrize("path", ["/api/admin/agents", "/api/admin/runs"])
async def test_neither_route_exists_under_explore(client, path: str) -> None:
    """§12.6: the prefix *is* the role boundary. A read-only surface that could
    reach these would be one route away from a writable session."""
    response = await client.get(path.replace("/api/admin", "/api/explore"))

    assert response.status_code == 404
