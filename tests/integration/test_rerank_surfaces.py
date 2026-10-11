"""Where reranking applies: Find's pages, the MCP search, Ask (task `B-167`).

A scripted scorer stands in for the cross-encoder: it ranks a passage by the trial
number in its text, which is the opposite of nothing in particular, so a surface that
ignored it would show. Each surface also keeps answering when the reranker is off.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid

import pytest

from api import search_service
from api.mcp import server
from meridian_core import chat
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.provider import Completion
from meridian_core.rerank import RERANK_DEPTH
from meridian_core.search import SearchFilters
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 10, 11, 12, tzinfo=dt.UTC)
COUNT = 12


class ByTrial:
    """Scores a passage by its trial number: the highest number first."""

    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, query, texts):
        self.calls += 1
        return [float(re.search(r"trial (\d+)", t).group(1)) for t in texts]


def trial(text: str) -> int:
    return int(re.search(r"trial (\d+)", text).group(1))


@pytest.fixture
def word() -> str:
    return f"zr{uuid.uuid4().hex[:10]}"


@pytest.fixture
def scope() -> str:
    return f"zz-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def sess(session_for, word, scope):
    s = await session_for("rw")
    for i in range(COUNT):
        source, _ = await upsert_source(
            s,
            f"https://r{uuid.uuid4().hex[:8]}.test/{i}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            language=scope,
            trust_state="cleared",
        )
        await replace_chunks(
            s,
            source.source_id,
            [ChunkWrite(text=f"The {word} scheme was measured in trial {i}.", chunk_index=0)],
        )
    await s.flush()
    yield s
    await s.rollback()


@pytest.fixture
def lexical_only(monkeypatch):
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)


async def test_find_pages_agree_on_the_reranked_order(
    sess, word, scope, monkeypatch, lexical_only
) -> None:
    """Page two must continue page one's order, not the fused one."""
    monkeypatch.setattr(search_service, "reranker", ByTrial)
    filters = SearchFilters(languages=[scope])
    pages = [
        await search_service.paged_search(
            sess, word, filters=filters, limit=5, offset=offset, rerank=True
        )
        for offset in (0, 5, 10)
    ]
    order = [trial(h.text) for page in pages for h in page.hits]
    assert order == list(range(COUNT - 1, -1, -1))
    assert all(page.reranked for page in pages)


async def test_find_without_asking_keeps_the_fused_order(
    sess, word, scope, monkeypatch, lexical_only
) -> None:
    scorer = ByTrial()
    monkeypatch.setattr(search_service, "reranker", lambda: scorer)
    page = await search_service.paged_search(
        sess, word, filters=SearchFilters(languages=[scope]), limit=5, offset=0
    )
    assert page.reranked is False and scorer.calls == 0


async def test_find_says_when_no_reranker_answered(sess, word, scope, lexical_only) -> None:
    """No embedding service at all: the order is fused, and the response says so."""
    page = await search_service.paged_search(
        sess, word, filters=SearchFilters(languages=[scope]), limit=5, offset=0, rerank=True
    )
    assert page.reranked is False and len(page.hits) == 5


async def test_the_answer_view_groups_the_reranked_pool(
    sess, word, scope, monkeypatch, lexical_only
) -> None:
    monkeypatch.setattr(search_service, "reranker", ByTrial)
    answer = await search_service.answer_search(
        sess, word, filters=SearchFilters(languages=[scope]), candidates=50, top=3, rerank=True
    )
    assert answer.reranked is True


async def test_an_assistant_gets_the_reranked_top(sess, word, monkeypatch, lexical_only) -> None:
    """Committed first: the MCP tool reads on its own connection."""
    await sess.commit()
    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", "true")
    monkeypatch.setattr(server, "reranker", ByTrial)
    try:
        result = await server.build_mcp(version="test").call_tool(
            "search_chunks", {"query": word, "limit": 3}
        )
        body = json.loads(result.content[0].text)
        assert body["reranked"] is True
        assert [trial(r["text"]) for r in body["results"]] == [11, 10, 9]
    finally:
        from sqlalchemy import delete, select

        from meridian_core.db import dispose_engines
        from meridian_core.models import Chunk, Source

        mine = select(Chunk.source_id).where(Chunk.text.like(f"%{word}%"))
        await sess.execute(delete(Source).where(Source.source_id.in_(mine)))
        await sess.commit()
        await dispose_engines()


async def test_ask_reads_the_passages_the_reranker_put_first(
    sess, word, monkeypatch, lexical_only
) -> None:
    seen = []

    async def fake(sess, task_type, *, prompt, system, max_tokens, timeout_s):
        seen.append(prompt)
        return Completion(
            text="It was measured [1].", agent_id="a", model="m", input_tokens=1, output_tokens=1
        )

    monkeypatch.setattr(chat, "ask", fake)
    await chat.ask_corpus(sess, f"What did the {word} scheme measure?", rerank=ByTrial(), now=NOW)
    shown = [int(n) for n in re.findall(r"trial (\d+)\.", seen[0])]
    assert shown[: chat.MAX_PASSAGES] == list(range(COUNT - 1, COUNT - 1 - chat.MAX_PASSAGES, -1))
    assert COUNT <= RERANK_DEPTH, "the fixture must fit within what is reranked"
