"""Asking the graph, against Postgres (tasks P6-06, P6-07).

The model is replaced — no model is reached from a test, and the operator's
instruction was to wire the local model and stop short of running it. What
needs a database is everything around the call: the retrieval that feeds it,
the threads and turns that are stored, the checks on what the answer may
cite, the daily cap, and the read path through the read-only role.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid

import httpx
import pytest
import yaml
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core import chat
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.models import Agent, ChatMessage, ChatThread, Entity
from meridian_core.provider import Completion, ProviderError
from meridian_core.routing import TASK_TYPES, NoAgentAvailable
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 27, 12, tzinfo=dt.UTC)


@pytest.fixture
def word() -> str:
    return f"zq{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def passages(sess, word: str, n: int = 3) -> None:
    for i in range(n):
        source, _ = await upsert_source(
            sess, f"https://c{uuid.uuid4().hex[:8]}.test/{i}", checksum=f"sha256:{uuid.uuid4().hex}"
        )
        await replace_chunks(
            sess,
            source.source_id,
            [ChunkWrite(text=f"The {word} scheme cut speeds in trial {i}.", chunk_index=0)],
        )
    await sess.flush()


@pytest.fixture
def model(monkeypatch):
    """Stands in for the model: records the prompt, returns a scripted answer."""
    seen: list[dict] = []

    def install(answer: str | Exception = "It cut speeds [1]."):
        async def fake(sess, task_type, *, prompt, system, max_tokens, timeout_s):
            seen.append({"task_type": task_type, "prompt": prompt, "system": system})
            if isinstance(answer, Exception):
                raise answer
            return Completion(
                text=answer,
                agent_id="local-chat",
                model="qwen-test",
                input_tokens=900,
                output_tokens=120,
            )

        monkeypatch.setattr(chat, "ask", fake)
        return seen

    return install


async def test_a_question_is_answered_from_retrieved_passages_and_stored(sess, word, model) -> None:
    await passages(sess, word)
    seen = model("The scheme cut speeds [1], and [7] is invented.")

    exchange = await chat.ask_corpus(sess, f"What did the {word} scheme do?", now=NOW)

    assert seen[0]["task_type"] == "chat"
    assert word in seen[0]["prompt"], "the passages retrieved were not what the model saw"
    answer = exchange.answer
    assert answer.error is None
    assert [c["n"] for c in answer.citations] == [1]
    assert "[7]" not in answer.text
    assert (answer.agent_id, answer.model, answer.input_tokens) == ("local-chat", "qwen-test", 900)
    assert exchange.thread.title == f"What did the {word} scheme do?"
    stored = list(
        await sess.scalars(
            select(ChatMessage).where(ChatMessage.thread_id == exchange.thread.thread_id)
        )
    )
    assert [m.role for m in stored] == ["user", "assistant"]


async def test_a_follow_up_carries_the_conversation(sess, word, model) -> None:
    await passages(sess, word)
    seen = model()
    first = await chat.ask_corpus(sess, f"What is {word}?", now=NOW)

    await chat.ask_corpus(sess, "And what did it cost?", thread_id=first.thread.thread_id, now=NOW)

    assert f"Reader: What is {word}?" in seen[1]["prompt"]
    assert seen[1]["prompt"].index("Reader:") < seen[1]["prompt"].index(
        "Question: And what did it cost?"
    )


async def test_the_selection_is_context_and_a_citable_node(sess, word, model) -> None:
    node = Entity(canonical_name=f"Node {word}", node_type="concept")
    sess.add(node)
    await sess.flush()
    seen = model("{N1} is the subject.")

    exchange = await chat.ask_corpus(
        sess, "How does this relate?", context_entity_ids=[node.entity_id], now=NOW
    )

    assert f"{{N1}} Node {word}" in seen[0]["prompt"]
    assert exchange.question.context_entity_ids == [node.entity_id]
    assert exchange.answer.nodes == [
        {"ref": 1, "entity_id": node.entity_id, "name": f"Node {word}", "contested": False}
    ]
    assert exchange.answer.text == f"Node {word} is the subject."


@pytest.mark.parametrize(
    ("failure", "says"),
    [
        (NoAgentAvailable("nothing declares chat"), "Admin"),
        (ProviderError("local-chat: ConnectError"), "did not answer"),
    ],
)
async def test_a_model_that_cannot_answer_is_a_stored_reason_not_an_error(
    sess, model, failure, says
) -> None:
    model(failure)
    exchange = await chat.ask_corpus(sess, "Anything at all?", now=NOW)
    assert says in exchange.answer.error
    assert exchange.answer.text == "" and exchange.answer.citations is None


@pytest.mark.parametrize("question", ["", "  ", "ab", "x" * (chat.MAX_QUESTION + 1)])
async def test_a_question_out_of_bounds_is_refused_and_nothing_stored(
    sess, model, question
) -> None:
    seen = model()
    before = await sess.scalar(
        select(ChatThread.thread_id).order_by(ChatThread.thread_id.desc()).limit(1)
    )
    with pytest.raises(chat.ChatRefused):
        await chat.ask_corpus(sess, question, now=NOW)
    assert seen == []
    assert (
        await sess.scalar(
            select(ChatThread.thread_id).order_by(ChatThread.thread_id.desc()).limit(1)
        )
        == before
    )


async def test_over_the_days_allowance_the_model_is_not_called(sess, model, monkeypatch) -> None:
    seen = model()
    used = await chat.tokens_today(sess, now=NOW)
    monkeypatch.setenv("MERIDIAN_CHAT_DAILY_TOKENS", str(used + 1000))
    await chat.ask_corpus(sess, "First question?", now=NOW)  # spends 1,020
    with pytest.raises(chat.ChatRefused, match="allowance"):
        await chat.ask_corpus(sess, "Second question?", now=NOW)
    assert len(seen) == 1


async def test_an_unknown_thread_is_refused(sess, model) -> None:
    model()
    with pytest.raises(chat.ChatRefused, match="No conversation"):
        await chat.ask_corpus(sess, "Continue?", thread_id=-1, now=NOW)


# --------------------------------------------------------------------------
# The agent: configured, not running
# --------------------------------------------------------------------------


async def test_the_chat_agent_ships_disabled_and_named_by_the_environment(sess) -> None:
    """Drift: the migration's row and config/agents.yaml's must agree, or a fresh
    database and an upgraded one route chat differently."""
    row = await sess.get(Agent, "local-chat")
    assert row is not None and row.enabled is False
    assert (row.provider, row.model, row.endpoint, row.task_types) == (
        "openai_compatible",
        "${LOCAL_CHAT_MODEL}",
        "${LOCAL_CHAT_LLM_URL}",
        ["chat"],
    )
    registry = yaml.safe_load(pathlib.Path("config/agents.yaml").read_text())
    seeded = {a["agent_id"]: a for a in registry["agents"]}["local-chat"]
    for field in ("provider", "model", "endpoint", "task_types", "token_scope", "enabled"):
        assert getattr(row, field) == seeded[field], field
    assert "chat" in TASK_TYPES


async def test_the_model_name_is_read_from_the_environment_at_call_time(monkeypatch) -> None:
    from meridian_core import provider

    agent = Agent(agent_id="local-chat", provider="openai_compatible", model="${LOCAL_CHAT_MODEL}")
    monkeypatch.setenv("LOCAL_CHAT_MODEL", "qwen-local-7b")
    assert provider.resolved(agent, agent.model, "model") == "qwen-local-7b"
    monkeypatch.delenv("LOCAL_CHAT_MODEL")
    with pytest.raises(provider.NotConfigured, match="LOCAL_CHAT_MODEL"):
        provider.resolved(agent, agent.model, "model")


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


async def test_ask_then_read_the_thread_back_through_the_read_only_role(
    client, model, session_for
) -> None:
    model("Nothing in the corpus answers this.")
    asked = await client.post(
        "/api/admin/chat/ask", json={"question": f"Is there anything on {uuid.uuid4().hex}?"}
    )
    assert asked.status_code == 200, asked.text
    thread_id = asked.json()["thread"]["thread_id"]
    try:
        listed = (await client.get("/api/explore/chat/threads")).json()
        assert thread_id in [t["thread_id"] for t in listed["threads"]]
        detail = await client.get(f"/api/explore/chat/threads/{thread_id}")
        assert [m["role"] for m in detail.json()["messages"]] == ["user", "assistant"]
        assert (await client.get("/api/explore/chat/threads/-1")).status_code == 404
    finally:
        sess = await session_for("rw")
        await sess.execute(delete(ChatThread).where(ChatThread.thread_id == thread_id))
        await sess.commit()


async def test_a_refused_question_is_a_422_in_words(client, model) -> None:
    model()
    response = await client.post("/api/admin/chat/ask", json={"question": "ab"})
    assert response.status_code == 422 and "characters" in response.json()["detail"]
