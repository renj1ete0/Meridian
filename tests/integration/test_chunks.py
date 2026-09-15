"""Writing chunks (task P2-02, spec §5.3, §6.3).

Against a real Postgres because the constraints are the subject: `chunks` has a
uniqueness constraint on `(source_id, chunk_index)` that makes replacement a
real operation rather than a notion, a `NOT NULL` on `text`, and a cascade from
`sources` that a double cannot have. §6.3's high-water mark is a `chunk_id`, so
what ids replacement produces is a behavioural question, not an implementation
detail.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from meridian_core.chunks import (
    ChunkWrite,
    _reclaimable,  # noqa: PLC2701
    as_writes,
    chunk_count,
    chunks_without_embeddings,
    delete_chunks,
    purge_superseded,
    replace_chunks,
    superseded_uncited,
)
from meridian_core.models import Chunk, Entity, Observation, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

#: Two distinct moments, so a re-stamped generation is visible rather than
#: merely plausible.
FIRST = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
SECOND = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


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
    """The *live* set — what every consumer of the corpus sees (`P1-32`)."""
    rows = await sess.execute(
        select(Chunk)
        .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
        .order_by(Chunk.chunk_index)
    )
    return list(rows.scalars())


async def all_chunks_for(sess, source_id: int) -> list[Chunk]:
    """Every generation, superseded included. Only this file cares."""
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source_id).order_by(Chunk.chunk_id)
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


async def test_replacing_leaves_only_the_new_set_live(session_for, url, cleanup) -> None:
    """A page that changed is a page whose old chunks describe text that is gone.

    Half the old set beside half the new one is worse than either, and the
    partial unique index on `(source_id, chunk_index) WHERE superseded_at IS
    NULL` would refuse it anyway — which is a write that fails at 3am rather
    than a design that holds.
    """
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b", "c"))

    written, superseded = await replace_chunks(sess, source.source_id, writes("x"))

    assert (written, superseded) == (1, 3)
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


# --------------------------------------------------------------------------
# Superseded, not deleted (task P1-32, spec §2.3, §2.4)
# --------------------------------------------------------------------------
#
# `edges.supporting_chunk_ids` is an array of ids with no foreign key behind it,
# because Postgres cannot enforce one on array elements. Deleting a chunk
# therefore left every edge citing it pointing at nothing — and silently, since
# §2.3 asks for provenance and an orphaned edge still *has* provenance: a list
# of ids that passes every check and resolves to nothing. Nothing looks.


async def test_the_old_rows_survive_the_replacement(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b", "c"))

    await replace_chunks(sess, source.source_id, writes("x"))
    everything = await all_chunks_for(sess, source.source_id)

    assert sorted(r.text for r in everything) == ["a", "b", "c", "x"]


async def test_a_citation_still_resolves_after_the_page_changes(
    session_for, url, cleanup
) -> None:
    # The whole point. An id taken from an edge's provenance has to keep
    # resolving to the text the edge was derived from, and §2.4 makes that text
    # the thing the graph is re-derived from — so it cannot be the current
    # page's version, which says something else.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("the original claim"))
    cited = (await chunks_for(sess, source.source_id))[0].chunk_id

    await replace_chunks(sess, source.source_id, writes("a completely different claim"))
    still_there = await sess.get(Chunk, cited)

    assert still_there is not None
    assert still_there.text == "the original claim"
    assert still_there.superseded_at is not None


async def test_the_old_generation_keeps_its_own_timestamp(session_for, url, cleanup) -> None:
    # A source re-crawled twice has two retired generations, and re-stamping the
    # older one would move its timestamp forward — which is the one thing the
    # column is for, and would make "superseded more than N days ago" mean
    # nothing.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("first"))
    await replace_chunks(sess, source.source_id, writes("second"), now=FIRST)
    await replace_chunks(sess, source.source_id, writes("third"), now=SECOND)

    stamps = {r.text: r.superseded_at for r in await all_chunks_for(sess, source.source_id)}

    assert stamps["first"] == FIRST
    assert stamps["second"] == SECOND
    assert stamps["third"] is None


async def test_the_same_chunk_index_may_be_reused_by_the_live_set(
    session_for, url, cleanup
) -> None:
    # The partial unique index doing its job. A plain constraint over
    # (source_id, chunk_index) would refuse the replacement outright, which is
    # how this design fails if the index is wrong: at write time, on a re-crawl,
    # at whatever hour the page changed.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b"))

    written, _ = await replace_chunks(sess, source.source_id, writes("c", "d"))

    assert written == 2
    assert [r.chunk_index for r in await chunks_for(sess, source.source_id)] == [0, 1]


async def test_two_live_chunks_cannot_share_an_index(session_for, url, cleanup) -> None:
    # The converse, and what makes the partial index worth having rather than
    # just permissive: uniqueness still holds where it matters.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a"))

    sess.add(Chunk(source_id=source.source_id, text="clash", chunk_index=0))
    with pytest.raises(IntegrityError):
        await sess.flush()
    await sess.rollback()


async def test_a_superseded_chunk_is_not_counted_in_the_corpus(
    session_for, url, cleanup
) -> None:
    # "How much is in here" means the text on the pages now. Counting retired
    # generations would make the corpus appear to grow every time a page
    # changed, which is the opposite of what happened.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b", "c"))

    await replace_chunks(sess, source.source_id, writes("x"))

    assert await chunk_count(sess, source.source_id) == 1
    assert await chunk_count(sess, source.source_id, live_only=False) == 4


async def test_a_superseded_chunk_is_not_queued_for_embedding(
    session_for, url, cleanup
) -> None:
    # Embedding text that is no longer on the page spends the model's time
    # producing a vector nothing may search.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("retired"))
    await replace_chunks(sess, source.source_id, writes("current"))

    queued = await chunks_without_embeddings(sess, limit=500)
    mine = [c for c in queued if c.source_id == source.source_id]

    assert [c.text for c in mine] == ["current"]


async def test_an_uncited_superseded_chunk_is_reclaimable(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b"))
    await replace_chunks(sess, source.source_id, writes("x"))

    assert await superseded_uncited(sess) >= 2


async def test_a_cited_superseded_chunk_is_not_reclaimable(session_for, url, cleanup) -> None:
    # The half that matters. If this were wrong the sweep would delete exactly
    # the chunks an edge depends on, which is the orphaning this task exists to
    # prevent — arriving through the mechanism that was supposed to prevent it.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("evidence"))
    cited = (await chunks_for(sess, source.source_id))[0].chunk_id
    await replace_chunks(sess, source.source_id, writes("rewritten"))

    subject = Entity(canonical_name=f"subject-{uuid.uuid4().hex[:8]}", node_type="finding")
    sess.add(subject)
    await sess.flush()
    sess.add(
        Observation(
            subject_entity_id=subject.entity_id,
            metric="a measured thing",
            value_numeric=1.0,
            supporting_chunk_ids=[cited],
        )
    )
    await sess.flush()

    reclaimable = list(
        await sess.scalars(
            select(Chunk.chunk_id).where(
                Chunk.chunk_id.in_(
                    select(_reclaimable().subquery().c.chunk_id)  # noqa: SLF001
                )
            )
        )
    )

    assert cited not in reclaimable
    await sess.rollback()


async def test_purging_keeps_the_live_set(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a", "b"))
    await replace_chunks(sess, source.source_id, writes("x"))

    await purge_superseded(sess)

    assert [r.text for r in await all_chunks_for(sess, source.source_id)] == ["x"]


async def test_deleting_a_source_takes_its_retired_chunks_too(
    session_for, url, cleanup
) -> None:
    # `delete_chunks` is for the caller that means it. Leaving retired rows
    # behind would leave chunks referring to a source that no longer exists —
    # the orphaning this task exists to prevent, in the other direction.
    sess = await session_for("rw")
    source = await a_source(sess, url)
    await replace_chunks(sess, source.source_id, writes("a"))
    await replace_chunks(sess, source.source_id, writes("b"))

    removed = await delete_chunks(sess, source.source_id)

    assert removed == 2
    assert await all_chunks_for(sess, source.source_id) == []


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
