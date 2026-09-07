"""The embedding backfill (task P2-01, spec §6.1).

Against a real Postgres because the queue *is* the table: `embedding IS NULL` is
the whole work list, `Vector(1024)` is what rejects a wrong-width vector, and
whether a float round-trips through pgvector unchanged is not something a double
can tell you.

The embedder is a fake throughout — the wrapper's own tests cover the model, and
what needs proving here is that the pass reads the right rows, writes the right
columns, and survives being interrupted.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import (
    ChunkWrite,
    chunks_without_embeddings,
    embedding_backlog,
    replace_chunks,
    store_embeddings,
)
from meridian_core.models import Chunk, Source
from meridian_core.sources import upsert_source
from worker.embed import Backfill
from worker.embeddings import EmbeddingError, FakeEmbedder

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def url() -> str:
    return f"https://t{uuid.uuid4().hex[:12]}.test/a"


@pytest.fixture
async def cleanup(session_for, url):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.url.like(f"{url[:40]}%")))
    await sess.commit()


async def a_source_with_chunks(sess, url: str, count: int) -> tuple[Source, list[Chunk]]:
    source, _ = await upsert_source(sess, url, checksum="sha256:one")
    await replace_chunks(
        sess,
        source.source_id,
        [
            ChunkWrite(text=f"Chunk {i}: ridership rose eleven per cent.", chunk_index=i)
            for i in range(count)
        ],
    )
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_index)
    )
    return source, list(rows.scalars())


def backfill_for(sess, chunks, embedder=None, **kwargs) -> Backfill:
    """A backfill scoped to *this test's* chunks.

    `start_after` is not decoration. The queue is `embedding IS NULL` over the
    whole table, and this dev database holds a real crawl whose chunks have no
    vectors either — so a backfill starting at 0 embeds the corpus and never
    reaches the rows the test just wrote, while `stats.embedded` looks right.
    The same trap `test_worker_run.py` documents for the seeded frontier.
    """
    return Backfill(
        embedder or FakeEmbedder(),
        session_factory=factory(sess),
        start_after=min(c.chunk_id for c in chunks) - 1,
        **kwargs,
    )


def factory(sess):
    """Hand the backfill the test's own transaction, commits downgraded."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


async def test_a_new_chunk_has_no_vector_and_is_therefore_queued(session_for, url, cleanup) -> None:
    """`P2-02` writes chunks with NULL embeddings by design — embedding is a
    separate pass so the fetch loop never waits on a model. So a NULL here means
    "not embedded yet" and nothing else."""
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 3)

    assert all(chunk.embedding is None for chunk in chunks)
    queued = await chunks_without_embeddings(sess, limit=100)
    assert {c.chunk_id for c in chunks} <= {c.chunk_id for c in queued}


async def test_an_embedded_chunk_leaves_the_queue(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 2)
    vectors = FakeEmbedder().embed([c.text for c in chunks])

    await store_embeddings(sess, dict(zip([c.chunk_id for c in chunks], vectors, strict=True)))

    queued = {c.chunk_id for c in await chunks_without_embeddings(sess, limit=1000)}
    assert not ({c.chunk_id for c in chunks} & queued)


async def test_the_queue_pages_by_id_not_by_offset(session_for, url, cleanup) -> None:
    """A backfill that pages with OFFSET re-scans what it has read on every
    page, and shifts under its own feet as the crawl writes new chunks."""
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 6)
    ids = [c.chunk_id for c in chunks]

    first = await chunks_without_embeddings(sess, limit=2, after_id=ids[0] - 1)
    second = await chunks_without_embeddings(sess, limit=2, after_id=first[-1].chunk_id)

    assert [c.chunk_id for c in first] == ids[:2]
    assert [c.chunk_id for c in second] == ids[2:4]


async def test_the_backlog_is_countable(session_for, url, cleanup) -> None:
    """A backlog that only grows means the embedder has stopped — otherwise
    invisible, since the crawl keeps working and nothing becomes searchable."""
    sess = await session_for("rw")
    before = await embedding_backlog(sess)

    await a_source_with_chunks(sess, url, 4)

    assert await embedding_backlog(sess) == before + 4


# --------------------------------------------------------------------------
# Storing vectors
# --------------------------------------------------------------------------


async def test_a_vector_round_trips_through_pgvector(session_for, url, cleanup) -> None:
    """The one thing a double cannot tell you.

    A float that changed on the way through would move every cosine the novelty
    gate and the search path depend on.
    """
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 1)
    vector = FakeEmbedder().embed([chunks[0].text])[0]

    await store_embeddings(sess, {chunks[0].chunk_id: vector})
    await sess.refresh(chunks[0])

    stored = [float(v) for v in chunks[0].embedding]
    assert stored == pytest.approx(vector, abs=1e-6)


async def test_a_vector_of_the_wrong_width_is_refused_by_the_column(
    session_for, url, cleanup
) -> None:
    """`Vector(1024)` is the last line of defence behind the wrapper's check.

    Asserted rather than assumed — a migration that widened the column without
    anyone noticing would make the wrapper's check the only one left.
    """
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 1)

    with pytest.raises(Exception):  # noqa: B017 - asyncpg/pgvector raise their own
        await store_embeddings(sess, {chunks[0].chunk_id: [0.1] * 384})
    await sess.rollback()


async def test_a_chunk_that_vanished_does_not_lose_the_batch(session_for, url, cleanup) -> None:
    """A source re-crawled between the read and the write has had its chunks
    replaced, and the new ones are already queued behind this batch.

    Losing a 256-chunk batch over one deleted row would make a long backfill
    fragile in exactly the situation it is most likely to meet.
    """
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 3)
    vectors = FakeEmbedder().embed([c.text for c in chunks])
    await sess.execute(delete(Chunk).where(Chunk.chunk_id == chunks[1].chunk_id))
    await sess.flush()

    written = await store_embeddings(
        sess, dict(zip([c.chunk_id for c in chunks], vectors, strict=True))
    )

    assert written == 2


# --------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------


async def test_a_pass_embeds_everything_waiting(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 5)
    backfill = backfill_for(sess, chunks, batch_size=2)

    stats = await backfill.run_once()

    assert stats.embedded >= 5
    for chunk in chunks:
        await sess.refresh(chunk)
        assert chunk.embedding is not None


async def test_a_second_pass_finds_nothing_to_do(session_for, url, cleanup) -> None:
    """Idempotent, because the queue is a predicate rather than a cursor."""
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 4)
    backfill = backfill_for(sess, chunks, batch_size=10)
    await backfill.run_once()

    stats = await backfill.run_once()

    assert stats.embedded == 0
    assert stats.batches == 0


async def test_each_chunk_keeps_its_own_vector(session_for, url, cleanup) -> None:
    """The misalignment that nothing downstream would ever notice.

    If the pass zipped vectors onto ids in the wrong order, every chunk in the
    batch would carry somebody else's meaning and every test above would still
    pass.
    """
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 5)
    embedder = FakeEmbedder()
    backfill = backfill_for(sess, chunks, embedder, batch_size=2)

    await backfill.run_once()

    for chunk in chunks:
        await sess.refresh(chunk)
        expected = embedder.embed([chunk.text])[0]
        assert [float(v) for v in chunk.embedding] == pytest.approx(expected, abs=1e-6)


async def test_a_failing_batch_is_skipped_rather_than_retried_forever(
    session_for, url, cleanup
) -> None:
    """The cursor advances past a failed batch on purpose.

    Re-querying from zero would make one bad batch an infinite loop over the
    same rows, and a backfill that never reaches the good ones behind it.
    """
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 6)

    class Broken(FakeEmbedder):
        calls = 0

        def embed(self, texts):
            Broken.calls += 1
            raise EmbeddingError("the model fell over")

    backfill = backfill_for(sess, chunks, Broken(), batch_size=2, max_batches=4)

    stats = await backfill.run_once()

    assert stats.failed_batches == stats.batches > 1
    assert stats.embedded == 0


async def test_work_committed_before_a_stop_survives_it(session_for, url, cleanup) -> None:
    """`SIGTERM` during a four-hour backfill must not mean losing all of it.

    Everything committed before the signal stays committed — which is the whole
    reason to commit per batch rather than per pass — and the batch in flight
    finishes rather than being abandoned mid-write.
    """
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 8)
    backfill = backfill_for(sess, chunks, batch_size=2)

    class StopAfterOne(FakeEmbedder):
        def embed(self, texts):
            vectors = super().embed(texts)
            backfill.stop()
            return vectors

    backfill._embedder = StopAfterOne()
    await backfill.run_once()

    done = 0
    for chunk in chunks:
        await sess.refresh(chunk)
        done += chunk.embedding is not None
    assert done == 2, "the committed batch did not survive the stop"


async def test_max_batches_bounds_a_first_run(session_for, url, cleanup) -> None:
    """`--max-batches` exists so the first run on a new machine is a
    measurement rather than a commitment."""
    sess = await session_for("rw")
    _, chunks = await a_source_with_chunks(sess, url, 10)
    backfill = backfill_for(sess, chunks, batch_size=2, max_batches=2)

    stats = await backfill.run_once()

    assert stats.batches == 2
    assert stats.embedded == 4
