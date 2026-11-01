"""A stub is marked wherever it is shown, and replaced when the article arrives (`B-256`).

Against Postgres, because the mark lives in `sources.extra` and every surface reads it from
there: search, Find and Ask through the API, the source page, and the MCP tools an assistant
calls. The failure worth guarding is a surface that shows a teaser as if it were the article.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select, update

from api.main import create_app
from api.mcp.server import build_mcp
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.db import dispose_engines
from meridian_core.framing import STUB_NOTE
from meridian_core.models import Chunk, Source
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source
from meridian_core.stubs import replace_stubs, set_stub

pytestmark = pytest.mark.usefixtures("require_db")

MARK = {"reason": "paywall", "rule": "isAccessibleForFree", "from": "page"}


@pytest.fixture
def run() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
def scope(run: str) -> str:
    """The language every fixture source carries, which is how they are cleaned up."""
    return f"zz-{run}"


@pytest.fixture
def term(run: str) -> str:
    return f"qxs{run}"


async def a_source(sess, scope: str, url: str, text: str, *, stub: bool) -> Source:
    source, _ = await upsert_source(
        sess,
        url,
        checksum=f"sha256:{uuid.uuid4().hex}",
        language=scope,
        title=text,
        source_tier="informal",
    )
    if stub:
        set_stub(source, MARK)
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"{text}, written out at length for a passage.", chunk_index=0)],
    )
    await sess.execute(
        update(Source).where(Source.source_id == source.source_id).values(trust_state="cleared")
    )
    await sess.flush()
    return source


@pytest.fixture
async def made(session_for, scope, term, run):
    """A stub and a whole article that both match ``term``, committed for the API and MCP."""
    sess = await session_for("rw")
    walled = await a_source(sess, scope, f"https://w{run}.test/a", f"{term} walled", stub=True)
    whole = await a_source(sess, scope, f"https://f{run}.test/a", f"{term} whole", stub=False)
    await sess.commit()
    yield {"walled": walled.source_id, "whole": whole.source_id}
    await sess.rollback()
    await sess.execute(delete(Source).where(Source.language == scope))
    await sess.commit()
    await dispose_engines()


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


async def call(mcp, name: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(name, arguments)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def partial_by_source(rows: list[dict[str, Any]], made: dict[str, int]) -> dict[str, bool]:
    ids = {source_id: name for name, source_id in made.items()}
    return {ids[r["source_id"]]: r["partial"] for r in rows if r["source_id"] in ids}


async def test_a_search_hit_says_whether_its_source_is_a_stub(session_for, made, scope, term):
    sess = await session_for("ro")
    result = await search(sess, term, filters=SearchFilters(languages=[scope]))
    flags = {h.source_id: h.partial for h in result.hits}
    assert flags == {made["walled"]: True, made["whole"]: False}


async def test_find_ask_and_the_source_page_carry_the_mark(client, made, scope, term) -> None:
    found = await client.get("/api/explore/search", params={"q": term, "language": scope})
    answered = await client.get("/api/explore/answer", params={"q": term, "language": scope})
    walled = await client.get(f"/api/explore/sources/{made['walled']}")
    whole = await client.get(f"/api/explore/sources/{made['whole']}")

    assert found.status_code == 200, found.text
    assert partial_by_source(found.json()["hits"], made) == {"walled": True, "whole": False}
    assert answered.status_code == 200, answered.text
    body = answered.json()
    items = [
        item
        for group in [*body["groups"], body.get("unplaced"), body.get("ungrouped")]
        if group
        for item in group["items"]
    ]
    assert partial_by_source(items, made) == {"walled": True, "whole": False}
    assert walled.json()["partial"] is True and walled.json()["partial_reason"] == "paywall"
    assert whole.json()["partial"] is False and whole.json()["partial_reason"] is None


async def test_an_assistant_is_told_a_stub_is_not_the_article(made, term) -> None:
    """In the structured result, in the framed text it pastes into a prompt, and in the
    metadata and walking tools."""
    mcp = build_mcp(version="test")
    body = await call(mcp, "search_chunks", query=term, limit=10)
    ours = {r["source_id"]: r for r in body["results"] if r["source_id"] in made.values()}
    assert ours[made["walled"]]["partial"] is True
    assert ours[made["walled"]]["partial_note"] == STUB_NOTE
    assert ours[made["whole"]]["partial"] is False and "partial_note" not in ours[made["whole"]]
    assert body["framed"].count(STUB_NOTE) == 1

    meta = await call(mcp, "get_source_metadata", source_id=made["walled"])
    assert meta["partial"] is True and meta["partial_note"] == STUB_NOTE

    walked = await call(mcp, "list_new_since", mark=0, limit=200)
    flags = partial_by_source(walked["passages"], made)
    assert flags.get("walled", True) is True and flags.get("whole", False) is False


async def test_the_article_handed_in_replaces_the_stub(session_for, scope, run) -> None:
    """The stub's passage is superseded, never deleted; its mark goes; the source that
    replaced it is recorded. A whole article at another address is left alone."""
    sess = await session_for("rw")
    try:
        walled = await a_source(sess, scope, f"https://w{run}.test/a", "walled", stub=True)
        other = await a_source(sess, scope, f"https://o{run}.test/a", "other", stub=False)
        upload = await a_source(sess, scope, f"upload://{run}", "whole article", stub=False)

        replaced = await replace_stubs(
            sess, [walled.url, other.url, ""], by_source_id=upload.source_id
        )

        assert replaced == [walled.source_id]
        await sess.refresh(walled)
        assert "stub" not in walled.extra and walled.extra["stub_replaced_by"] == upload.source_id
        assert walled.text_available is False
        live = await sess.scalars(
            select(Chunk).where(Chunk.source_id == walled.source_id, Chunk.superseded_at.is_(None))
        )
        assert list(live) == []
        kept = await sess.scalars(select(Chunk).where(Chunk.source_id == walled.source_id))
        assert len(list(kept)) == 1, "the stub's passage was deleted, not superseded"
        assert await replace_stubs(sess, [], by_source_id=upload.source_id) == []
    finally:
        await sess.rollback()


async def test_the_source_page_names_what_replaced_a_stub(session_for, client, scope, run):
    sess = await session_for("rw")
    walled = await a_source(sess, scope, f"https://w{run}.test/a", "walled", stub=True)
    upload = await a_source(sess, scope, f"upload://{run}", "whole article", stub=False)
    await replace_stubs(sess, [walled.url], by_source_id=upload.source_id)
    await sess.commit()
    try:
        page = await client.get(f"/api/explore/sources/{walled.source_id}")
        assert page.json()["partial"] is False
        assert page.json()["replaced_by"] == upload.source_id
    finally:
        await sess.execute(delete(Source).where(Source.language == scope))
        await sess.commit()
