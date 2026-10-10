"""Chunks are embedded as their view, and stale vectors are replaced in place (B-49)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.embedtext import VIEW_VERSION, embedding_view
from meridian_core.models import Chunk
from meridian_core.sources import upsert_source
from worker.embed import Backfill
from worker.embeddings import FakeEmbedder
from worker.reembed import run_pass
from worker.vectors import LocalEmbedder

pytestmark = pytest.mark.usefixtures("require_db")

LINKED = "Ridership rose, per [the annual report](https://example.test/report.pdf)."
PLAIN = "Ridership rose eleven per cent over the period."


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


async def chunks(sess, *texts: str) -> list[int]:
    source, _ = await upsert_source(
        sess, f"https://v{uuid.uuid4().hex[:10]}.test/a", checksum="sha256:v"
    )
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)]
    )
    return list(
        await sess.scalars(
            select(Chunk.chunk_id)
            .where(Chunk.source_id == source.source_id)
            .order_by(Chunk.chunk_index)
        )
    )


async def row(sess, chunk_id: int) -> Chunk:
    chunk = await sess.get(Chunk, chunk_id)
    await sess.refresh(chunk)
    return chunk


def vector_of(text: str) -> list[float]:
    return FakeEmbedder().embed([text])[0]


async def test_the_backfill_embeds_the_view_and_records_its_version(sess) -> None:
    ids = await chunks(sess, LINKED, PLAIN)
    backfill = Backfill(
        LocalEmbedder(FakeEmbedder()), session_factory=factory(sess), start_after=ids[0] - 1
    )

    await backfill.run_once()

    linked, plain = await row(sess, ids[0]), await row(sess, ids[1])
    assert [float(x) for x in linked.embedding] == pytest.approx(
        vector_of(embedding_view(LINKED)), abs=1e-6
    )
    assert [float(x) for x in linked.embedding] != pytest.approx(vector_of(LINKED), abs=1e-6)
    assert [float(x) for x in plain.embedding] == pytest.approx(vector_of(PLAIN), abs=1e-6)
    assert linked.embedding_view == plain.embedding_view == VIEW_VERSION
    # The stored text is untouched: it is the citation.
    assert linked.text == LINKED


async def old_vectors(sess, ids: list[int], texts: list[str]) -> None:
    """Vectors as the pre-view backfill wrote them: raw text, no version."""
    await store_embeddings(sess, {i: vector_of(t) for i, t in zip(ids, texts, strict=True)})


async def test_reembed_replaces_a_stale_linked_vector_and_only_marks_a_plain_one(sess) -> None:
    ids = await chunks(sess, LINKED, PLAIN)
    await old_vectors(sess, ids, [LINKED, PLAIN])

    stats = await run_pass(
        LocalEmbedder(FakeEmbedder()),
        apply=True,
        start_after=ids[0] - 1,
        session_factory=factory(sess),
    )

    linked, plain = await row(sess, ids[0]), await row(sess, ids[1])
    assert stats.reembedded >= 1 and stats.unchanged >= 1
    assert [float(x) for x in linked.embedding] == pytest.approx(
        vector_of(embedding_view(LINKED)), abs=1e-6
    )
    assert [float(x) for x in plain.embedding] == pytest.approx(vector_of(PLAIN), abs=1e-6)
    assert linked.embedding_view == plain.embedding_view == VIEW_VERSION


async def test_a_report_writes_nothing(sess) -> None:
    ids = await chunks(sess, LINKED)
    await old_vectors(sess, ids, [LINKED])

    stats = await run_pass(None, apply=False, start_after=ids[0] - 1, session_factory=factory(sess))

    assert stats.reembedded >= 1
    linked = await row(sess, ids[0])
    assert linked.embedding_view is None
    assert [float(x) for x in linked.embedding] == pytest.approx(vector_of(LINKED), abs=1e-6)


async def test_a_second_pass_finds_nothing_stale(sess) -> None:
    ids = await chunks(sess, LINKED, PLAIN)
    await old_vectors(sess, ids, [LINKED, PLAIN])
    await run_pass(
        LocalEmbedder(FakeEmbedder()),
        apply=True,
        start_after=ids[0] - 1,
        session_factory=factory(sess),
    )

    again = await run_pass(
        LocalEmbedder(FakeEmbedder()),
        apply=True,
        start_after=ids[0] - 1,
        session_factory=factory(sess),
    )

    assert again.examined == 0


async def test_a_chunk_with_no_vector_is_the_backfills_not_the_reembeds(sess) -> None:
    ids = await chunks(sess, LINKED)

    stats = await run_pass(None, apply=False, start_after=ids[0] - 1, session_factory=factory(sess))

    assert stats.examined == 0
    assert (await row(sess, ids[0])).embedding is None


# --------------------------------------------------------------------------
# A continued table is embedded with its header (`B-195`)
# --------------------------------------------------------------------------

HEAD = "| Region | Trips | Share |"
FIRST = f"Some prose before the table.\n\n{HEAD}\n|---|---|---|\n| North | 12 | 0.4 |"
REST = "| South | 9 | 0.3 |\n| East | 4 | 0.1 |"


async def test_a_continued_table_is_embedded_with_its_header_and_stored_without(sess) -> None:
    ids = await chunks(sess, FIRST, REST)
    backfill = Backfill(
        LocalEmbedder(FakeEmbedder()), session_factory=factory(sess), start_after=ids[0] - 1
    )
    await backfill.run_once()

    rest = await row(sess, ids[1])
    assert [float(x) for x in rest.embedding] == pytest.approx(
        vector_of(f"{HEAD}\n{embedding_view(REST)}"), abs=1e-6
    )
    assert rest.text == REST


async def test_a_header_is_not_borrowed_across_prose(sess) -> None:
    """The walk back stops at a passage that is not part of a table: the table ended there."""
    from meridian_core.chunks import table_heads

    ids = await chunks(sess, FIRST, "A paragraph with no table in it.", REST)
    rows = [await row(sess, i) for i in ids]
    assert await table_heads(sess, rows) == {}

    ids = await chunks(sess, FIRST, REST)
    rows = [await row(sess, i) for i in ids]
    assert await table_heads(sess, rows) == {ids[1]: HEAD}
