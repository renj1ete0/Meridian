"""Writing chunks (task P2-02, spec §5.3, §6.3).

Against a real Postgres because the constraints are the subject: `chunks` has a
uniqueness constraint on `(source_id, chunk_index)` that makes replacement a
real operation rather than a notion, a `NOT NULL` on `text`, and a cascade from
`sources` that a double cannot have. §6.3's high-water mark is a `chunk_id`, so
what ids replacement produces is a behavioural question, not an implementation
detail.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import (
    ChunkWrite,
    as_writes,
    chunk_count,
    delete_chunks,
    replace_chunks,
)
from meridian_core.models import Chunk, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def url() -> str:
    return f"https://t{uuid.uuid4().hex[:12]}.test/a"


@pytest.fixture
async def cleanup(session_for, url):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.url == url))
    await sess.commit()


async def a_source(sess, url: str) -> Source:
    source, _ = await upsert_source(sess, url, checksum="sha256:one")
    return source


def writes(*texts: str) -> list[ChunkWrite]:
    offset = 0
    out = []
    for index, text in enumerate(texts):
        out.append(ChunkWrite(text=text, chunk_index=index, page_or_offset=offset))
        offset += len(text) + 2
    return out


async def chunks_for(sess, source_id: int) -> list[Chunk]:
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source_id).order_by(Chunk.chunk_index)
    )
    return list(rows.scalars())


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


async def test_chunks_are_written_with_their_offsets(session_for, url, cleanup) -> None:
    """§5.3: the offset is captured at extraction time, and this is where it lands."""
    sess = await session_for("rw")
    source = await a_source(sess, url)

    written, deleted = await replace_chunks(sess, source.source_id, writes("first", "second"))

    assert (written, deleted) == (2, 0)
    rows = await chunks_for(sess, source.source_id)
    assert [r.text for r in rows] == ["first", "second"]
    assert [r.chunk_index for r in rows] == [0, 1]
    assert [r.page_or_offset for r in rows] == [0, 7]


async def test_an_embedding_is_not_required(session_for, url, cleanup) -> None:
    """Chunking runs in the fast loop; embedding is `P2-01` and comes later.

    A chunk that had to wait for a model would put the ingestion loop behind
    one, which §2.1 forbids outright.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)

    await replace_chunks(sess, source.source_id, writes("text with no vector"))

    assert (await chunks_for(sess, source.source_id))[0].embedding is None


async def test_an_empty_chunk_is_dropped_rather_than_stored(session_for, url, cleanup) -> None:
    """It satisfies the NOT NULL and means nothing — it would embed to noise and
    cite nothing, so it is dropped here rather than guarded against everywhere."""
    sess = await session_for("rw")
    source = await a_source(sess, url)

    written, _ = await replace_chunks(
        sess,
        source.source_id,
        [
            ChunkWrite(text="real", chunk_index=0),
            ChunkWrite(text="   \n ", chunk_index=1),
        ],
    )

    assert written == 1
    assert [r.text for r in await chunks_for(sess, source.source_id)] == ["real"]


# --------------------------------------------------------------------------
# Replacement
# --------------------------------------------------------------------------


async def test_replacing_leaves_no_trace_of_the_old_set(session_for, url, cleanup) -> None:
    """A page that changed is a page whose old chunks describe text that is gone.

    Half the old set beside half the new one is worse than either, and the
    uniqueness constraint on `(source_id, chunk_index)` would refuse it anyway
    — which is a write that fails at 3am rather than a design that holds.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b", "c"))

    written, deleted = await replace_chunks(sess, source.source_id, writes("x"))

    assert (written, deleted) == (1, 3)
    assert [r.text for r in await chunks_for(sess, source.source_id)] == ["x"]


async def test_replacement_gives_new_ids_so_the_slow_loop_re_reads(
    session_for, url, cleanup
) -> None:
    """§6.3's high-water mark is the last `chunk_id` consumed.

    New ids are exactly what should happen to a page whose content changed —
    nothing has to notice the change or schedule the re-read, the mark simply
    falls behind the new rows.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("original"))
    before = {r.chunk_id for r in await chunks_for(sess, source.source_id)}

    await replace_chunks(sess, source.source_id, writes("rewritten"))
    after = {r.chunk_id for r in await chunks_for(sess, source.source_id)}

    assert before.isdisjoint(after)
    assert min(after) > max(before)


async def test_replacing_touches_only_the_named_source(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    mine = await a_source(sess, url)
    theirs, _ = await upsert_source(sess, f"{url}/other", checksum="sha256:two")
    await replace_chunks(sess, theirs.source_id, writes("theirs one", "theirs two"))
    await replace_chunks(sess, mine.source_id, writes("mine"))

    await replace_chunks(sess, mine.source_id, writes("mine again"))

    assert await chunk_count(sess, theirs.source_id) == 2
    await sess.execute(delete(Source).where(Source.source_id == theirs.source_id))


async def test_replacing_with_nothing_clears_the_set(session_for, url, cleanup) -> None:
    """A page that used to extract and now does not.

    Leaving the old chunks would keep the corpus asserting text the source no
    longer has, which is the failure `text_available` exists to make visible.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b"))

    written, deleted = await replace_chunks(sess, source.source_id, [])

    assert (written, deleted) == (0, 2)
    assert await chunk_count(sess, source.source_id) == 0


# --------------------------------------------------------------------------
# Cascade and counting
# --------------------------------------------------------------------------


async def test_deleting_a_source_takes_its_chunks_with_it(session_for, url, cleanup) -> None:
    """The FK cascade, exercised rather than assumed.

    Without it a purged source leaves chunks that search still returns and that
    resolve to a citation nothing can render.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b"))
    source_id = source.source_id

    await sess.execute(delete(Source).where(Source.source_id == source_id))
    await sess.flush()

    assert await chunk_count(sess, source_id) == 0


async def test_delete_chunks_reports_how_many_it_removed(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b", "c"))

    assert await delete_chunks(sess, source.source_id) == 3
    assert await delete_chunks(sess, source.source_id) == 0


async def test_counting_scopes_to_a_source_or_the_whole_corpus(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)
    corpus_before = await chunk_count(sess)

    await replace_chunks(sess, source.source_id, writes("a", "b"))

    assert await chunk_count(sess, source.source_id) == 2
    assert await chunk_count(sess) == corpus_before + 2


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------


def test_as_writes_adapts_the_chunkers_output() -> None:
    """`meridian_core` must not import from a service to describe its own table.

    The API and the orchestrator depend on this package; a dependency on
    `worker.extract` to talk about `chunks` would drag the extractor into both.
    """
    from worker.extract.chunk import TextChunk

    adapted = as_writes([TextChunk(text="body", offset=42, index=3)])

    assert adapted == [ChunkWrite(text="body", chunk_index=3, page_or_offset=42)]


async def test_the_chunkers_output_round_trips_through_the_database(
    session_for, url, cleanup
) -> None:
    """The whole path: text in, rows out, offsets still locating the passage."""
    from worker.extract.chunk import chunk_text

    text = "\n\n".join(f"Paragraph {i}. " + "Ridership rose. " * 30 for i in range(6))
    sess = await session_for("rw")
    source = await a_source(sess, url)

    await replace_chunks(sess, source.source_id, as_writes(chunk_text(text)))

    rows = await chunks_for(sess, source.source_id)
    assert len(rows) > 1
    for row in rows:
        assert text[row.page_or_offset : row.page_or_offset + len(row.text)] == row.text, (
            f"chunk {row.chunk_index}'s stored offset does not locate its text"
        )
