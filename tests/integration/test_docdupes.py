"""Document-level duplicates against Postgres (task B-44)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import Source
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source
from worker.docdupes import run_pass

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def a_source(sess, url: str, texts: list[str], lang: str) -> int:
    source, _ = await upsert_source(sess, url, checksum=f"sha256:{uuid.uuid4().hex}", language=lang)
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)]
    )
    await sess.flush()
    return source.source_id


async def test_a_url_variant_is_marked_a_copy_and_hidden_from_search(sess) -> None:
    lang = f"zz-{uuid.uuid4().hex[:6]}"
    word = f"qdup{uuid.uuid4().hex[:8]}"
    texts = [f"A study of {word} and its outcomes.", f"The results for {word} were mixed."]
    host = f"d{uuid.uuid4().hex[:8]}.test"
    first = await a_source(sess, f"https://{host}/paper", texts, lang)
    copy = await a_source(sess, f"https://{host}/paper?ref=list", texts, lang)

    await run_pass(apply=True, session_factory=factory(sess))

    row = await sess.get(Source, copy)
    await sess.refresh(row)
    assert (row.duplicate_of, row.duplicate_reason) == (first, "exact")
    hits = (await search(sess, word, filters=SearchFilters(languages=[lang]))).hits
    assert {h.source_id for h in hits} == {first}


async def test_a_source_cannot_be_its_own_duplicate(sess) -> None:
    from sqlalchemy.exc import IntegrityError

    sid = await a_source(sess, f"https://s{uuid.uuid4().hex[:8]}.test/a", ["x y z"], "zz")
    row = await sess.get(Source, sid)
    row.duplicate_of, row.duplicate_reason = sid, "exact"
    with pytest.raises(IntegrityError):
        await sess.flush()


async def test_a_mark_needs_a_reason(sess) -> None:
    from sqlalchemy.exc import IntegrityError

    a = await a_source(sess, f"https://s{uuid.uuid4().hex[:8]}.test/a", ["one"], "zz")
    b = await a_source(sess, f"https://s{uuid.uuid4().hex[:8]}.test/b", ["two"], "zz")
    row = await sess.get(Source, b)
    row.duplicate_of = a
    with pytest.raises(IntegrityError):
        await sess.flush()
