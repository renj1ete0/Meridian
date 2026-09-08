"""The novelty gate against real pgvector (task P2-03, spec §6.1, §5.4, §12.5).

Against a real Postgres because every load-bearing part of this is the
database: ``<=>`` is what computes the cosine, a LATERAL join is what finds the
nearest earlier chunk, ``ON DELETE SET NULL`` is what happens to a verdict when
its survivor is re-crawled away, and a partial index is what makes the queue
cheap. None of those are things a double can tell you about.

Vectors are constructed rather than embedded. ``FakeEmbedder`` gives identical
text identical vectors and different text near-orthogonal ones, which covers
"the same page twice" and nothing in between — and *in between* is where a
threshold lives. So the vectors here are built at a chosen angle:
``at(0.96, 1)`` and ``at(0.90, 1)`` sit at exactly those cosines from ``BASE``.
"""

from __future__ import annotations

import math
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.models import Chunk, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.novelty import (
    NoveltySettings,
    chunks_awaiting_novelty,
    demote_duplicate_sources,
    judge,
    nearest_earlier_neighbours,
    novelty_health,
    record_verdicts,
)
from meridian_core.sources import upsert_source
from worker.novelty import NoveltyPass

pytestmark = pytest.mark.usefixtures("require_db")


def at(similarity: float, axis: int, *, base: int = 0) -> list[float]:
    """A unit vector whose cosine with ``at(1.0, _, base=base)`` is ``similarity``.

    Two vectors built on *different* axes at the same similarity ``s`` are
    ``s**2`` apart from each other — 0.92 for s=0.96 — so the fixtures can put
    a chunk near the base without accidentally putting it on top of its
    siblings. Changing ``base`` moves the whole family into an orthogonal
    subspace, which is how a test gets two sources that cannot match each
    other at all.
    """
    vector = [0.0] * EMBEDDING_DIM
    vector[base] = similarity
    vector[axis] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


BASE = at(1.0, 1)


@pytest.fixture
def url() -> str:
    return f"https://n{uuid.uuid4().hex[:12]}.test/a"


@pytest.fixture
async def cleanup(session_for, url):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.url.like(f"{url[:40]}%")))
    await sess.commit()


async def a_source(
    sess, url: str, vectors: list[list[float]], **kwargs
) -> tuple[Source, list[Chunk]]:
    """A source whose chunks already carry the given vectors, in order."""
    source, _ = await upsert_source(sess, url, checksum=f"sha256:{uuid.uuid4().hex}", **kwargs)
    await replace_chunks(
        sess,
        source.source_id,
        [
            ChunkWrite(text=f"Ridership rose {i} per cent in the year to June.", chunk_index=i)
            for i in range(len(vectors))
        ],
    )
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_index)
    )
    chunks = list(rows.scalars())
    await store_embeddings(
        sess, {chunk.chunk_id: vector for chunk, vector in zip(chunks, vectors, strict=True)}
    )
    return source, chunks


def gate_for(sess, chunks, **kwargs) -> NoveltyPass:
    """A pass scoped to *this test's* chunks.

    ``start_after`` is not decoration: the queue is a predicate over the whole
    table and this dev database holds a real crawl whose chunks are embedded
    and unjudged, so a pass starting at 0 judges the corpus and the assertions
    below read a stale row. The same trap ``test_embedding_backfill.py``
    documents for the backfill.
    """
    settings = kwargs.pop("settings", None) or NoveltySettings(batch_size=kwargs.pop("batch", 100))
    return NoveltyPass(
        settings,
        session_factory=factory(sess),
        start_after=min(c.chunk_id for c in chunks) - 1,
        **kwargs,
    )


def factory(sess):
    """Hand the pass the test's own transaction, commits downgraded."""

    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


async def refreshed(sess, chunks: list[Chunk]) -> list[Chunk]:
    for chunk in chunks:
        await sess.refresh(chunk)
    return chunks


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


async def test_an_embedded_unjudged_chunk_is_queued(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, at(0.9, 2)])

    queued = await chunks_awaiting_novelty(sess, after_id=chunks[0].chunk_id - 1)

    assert [chunk_id for chunk_id, _ in queued] == [c.chunk_id for c in chunks]


async def test_a_chunk_without_a_vector_waits_rather_than_being_judged(
    session_for, url, cleanup
) -> None:
    """It is not novel, it is unjudgeable.

    Marking it would make the gate's verdict depend on which pass won the race
    with the embedder, and a chunk marked novel while its vector was NULL would
    never be compared against anything at all.
    """
    sess = await session_for("rw")
    source, chunks = await a_source(sess, url, [BASE])
    unembedded = Chunk(source_id=source.source_id, text="No vector yet.", chunk_index=9)
    sess.add(unembedded)
    await sess.flush()

    queued = {
        cid for cid, _ in await chunks_awaiting_novelty(sess, after_id=chunks[0].chunk_id - 1)
    }

    assert chunks[0].chunk_id in queued
    assert unembedded.chunk_id not in queued


async def test_the_queue_pages_by_id_not_by_offset(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [at(0.5, i + 2) for i in range(5)])
    ids = [c.chunk_id for c in chunks]

    first = await chunks_awaiting_novelty(sess, limit=2, after_id=ids[0] - 1)
    second = await chunks_awaiting_novelty(sess, limit=2, after_id=first[-1][0])

    assert [cid for cid, _ in first] == ids[:2]
    assert [cid for cid, _ in second] == ids[2:4]


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------


async def test_a_chunk_is_never_its_own_nearest_neighbour(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE])

    neighbours = await nearest_earlier_neighbours(sess, [c.chunk_id for c in chunks])

    assert all(n.chunk_id != cid for cid, n in neighbours.items())


async def test_only_earlier_chunks_are_candidates(session_for, url, cleanup) -> None:
    """The whole reason two identical chunks do not delete each other.

    Compared without the ``chunk_id <`` restriction each is the other's nearest
    neighbour at similarity 1.0, both clear the threshold, and the corpus loses
    the text entirely rather than deduplicating it.
    """
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE])
    first, second = chunks

    neighbours = await nearest_earlier_neighbours(sess, [c.chunk_id for c in chunks])

    assert neighbours[second.chunk_id].chunk_id == first.chunk_id
    assert neighbours[second.chunk_id].similarity == pytest.approx(1.0, abs=1e-5)
    assert first.chunk_id not in neighbours or neighbours[first.chunk_id].chunk_id < first.chunk_id


async def test_the_nearest_neighbour_is_the_nearest_one(session_for, url, cleanup) -> None:
    """Not merely *a* neighbour: the gate's verdict is the maximum similarity,
    so picking any other candidate silently lowers every score."""
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [at(0.30, 2), at(0.96, 3), BASE])
    far, close, subject = chunks

    neighbours = await nearest_earlier_neighbours(sess, [subject.chunk_id])

    assert neighbours[subject.chunk_id].chunk_id == close.chunk_id
    assert neighbours[subject.chunk_id].similarity == pytest.approx(0.96, abs=1e-4)
    assert far.chunk_id != close.chunk_id


async def test_a_known_duplicate_is_not_offered_as_a_neighbour(session_for, url, cleanup) -> None:
    """So ``duplicate_of`` always names a chunk that is still in the corpus,
    rather than the head of a chain every consumer has to walk."""
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE, BASE])
    survivor, marked, subject = chunks
    await record_verdicts(sess, judge([marked.chunk_id], {marked.chunk_id: _n(survivor.chunk_id)}))

    neighbours = await nearest_earlier_neighbours(sess, [subject.chunk_id])

    assert neighbours[subject.chunk_id].chunk_id == survivor.chunk_id


def _n(chunk_id: int):
    from meridian_core.novelty import Neighbour

    return Neighbour(chunk_id=chunk_id, similarity=1.0)


# --------------------------------------------------------------------------
# Recording the verdict
# --------------------------------------------------------------------------


async def test_the_later_copy_is_marked_and_the_earlier_one_survives(
    session_for, url, cleanup
) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE])
    first, second = chunks

    await gate_for(sess, chunks).run_once()
    await refreshed(sess, chunks)

    assert first.duplicate_of is None, "the first copy was dropped"
    assert second.duplicate_of == first.chunk_id
    assert second.nearest_similarity == pytest.approx(1.0, abs=1e-5)


async def test_a_merely_similar_chunk_is_kept(session_for, url, cleanup) -> None:
    """0.90 is not 0.96. A gate that dropped this would be deduplicating a
    topic rather than a document."""
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, at(0.90, 2)])

    await gate_for(sess, chunks).run_once()
    await refreshed(sess, chunks)

    assert chunks[1].duplicate_of is None
    assert chunks[1].nearest_similarity == pytest.approx(0.90, abs=1e-4)


async def test_the_threshold_is_what_decides(session_for, url, cleanup) -> None:
    """The same 0.96 pair, two thresholds, opposite verdicts — so the number in
    `NoveltySettings` is genuinely the one being applied.

    The second pair lives in an orthogonal subspace (``base=10``) and is
    written only after the first pass has run, because the queue is a predicate
    over the whole table: a pass started before the second source existed would
    otherwise judge it too, at the first threshold.
    """
    sess = await session_for("rw")
    _, lenient = await a_source(sess, url, [BASE, at(0.96, 1)])
    await gate_for(sess, lenient, settings=NoveltySettings(threshold=0.95)).run_once()

    _, strict = await a_source(sess, url + "b", [at(1.0, 11, base=10), at(0.96, 11, base=10)])
    await gate_for(sess, strict, settings=NoveltySettings(threshold=0.99)).run_once()
    await refreshed(sess, lenient + strict)

    assert lenient[1].duplicate_of == lenient[0].chunk_id
    assert strict[1].duplicate_of is None


async def test_every_judged_chunk_is_stamped(session_for, url, cleanup) -> None:
    """``novelty_checked_at`` is the queue, so a judged chunk that was not
    stamped is judged again on every pass, forever."""
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, at(0.2, 2)])

    await gate_for(sess, chunks).run_once()
    await refreshed(sess, chunks)

    assert all(chunk.novelty_checked_at is not None for chunk in chunks)


async def test_a_second_pass_finds_nothing_to_do(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE, at(0.3, 2)])
    gate = gate_for(sess, chunks)
    await gate.run_once()

    stats = await gate.run_once()

    assert (stats.judged, stats.batches) == (0, 0)


async def test_a_verdict_is_not_overwritten_by_a_racing_pass(session_for, url, cleanup) -> None:
    """Two passes over one batch must not re-time a verdict.

    ``novelty_checked_at`` says how big the corpus was when the call was made;
    a second pass restamping it would quietly claim the judgement was made
    against everything crawled since.
    """
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE])
    await gate_for(sess, chunks).run_once()
    await refreshed(sess, chunks)
    stamped, marked = chunks[1].novelty_checked_at, chunks[1].duplicate_of

    written = await record_verdicts(sess, judge([chunks[1].chunk_id], {}))
    await refreshed(sess, chunks)

    assert written == 0
    assert (chunks[1].novelty_checked_at, chunks[1].duplicate_of) == (stamped, marked)


async def test_a_duplicate_never_points_at_another_duplicate(session_for, url, cleanup) -> None:
    """Three copies of one page: both later ones must name the survivor."""
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [BASE, BASE, BASE])

    await gate_for(sess, chunks, batch=1).run_once()
    await refreshed(sess, chunks)

    assert [c.duplicate_of for c in chunks] == [None, chunks[0].chunk_id, chunks[0].chunk_id]


async def test_deleting_the_survivor_releases_its_duplicate(session_for, url, cleanup) -> None:
    """``ON DELETE SET NULL``, asserted against the real constraint.

    A re-crawl replaces a source's chunks (`P2-02`), so a survivor can vanish
    under a verdict that names it. The duplicate is then the only copy of that
    text left, and cascading the delete would lose the content entirely.
    """
    sess = await session_for("rw")
    _, first_source = await a_source(sess, url, [BASE])
    _, second_source = await a_source(sess, url + "b", [BASE])
    await gate_for(sess, first_source + second_source).run_once()
    await refreshed(sess, second_source)
    assert second_source[0].duplicate_of == first_source[0].chunk_id

    await sess.execute(delete(Chunk).where(Chunk.chunk_id == first_source[0].chunk_id))
    await sess.flush()
    await refreshed(sess, second_source)

    assert second_source[0].duplicate_of is None
    assert second_source[0].text, "the surviving copy of the text was deleted too"


# --------------------------------------------------------------------------
# Source demotion (§5.4)
# --------------------------------------------------------------------------


async def test_a_source_made_of_duplicates_becomes_junk(session_for, url, cleanup) -> None:
    """§5.4's retention tier is the gate's only source-level act — and it is a
    mark, not a deletion. `P1-31`'s sweep is what spends it."""
    sess = await session_for("rw")
    _, original = await a_source(sess, url, [BASE, at(0.5, 2)])
    mirror_source, mirror = await a_source(sess, url + "b", [BASE, at(0.5, 2)])

    await gate_for(sess, original + mirror).run_once()
    await sess.refresh(mirror_source)

    assert [c.duplicate_of for c in await refreshed(sess, mirror)] != [None, None]
    assert mirror_source.retention_tier == "junk"


async def test_the_original_keeps_its_retention_tier(session_for, url, cleanup) -> None:
    sess = await session_for("rw")
    original_source, original = await a_source(sess, url, [BASE, at(0.5, 2)])
    _, mirror = await a_source(sess, url + "b", [BASE, at(0.5, 2)])

    await gate_for(sess, original + mirror).run_once()
    await sess.refresh(original_source)

    assert original_source.retention_tier == "background"


async def test_a_primary_source_is_never_demoted(session_for, url, cleanup) -> None:
    """§5.4 keeps the raw file for government documents and papers *because*
    link rot makes them unrecoverable.

    A mirror that happens to be crawled second is still the citable copy of a
    real document: the cost of keeping a duplicate government PDF is a few
    megabytes, and the cost of dropping the only local copy of a page since
    reorganised away is a citation nobody can ever check again.
    """
    sess = await session_for("rw")
    _, original = await a_source(sess, url, [BASE, at(0.5, 2)])
    mirror_source, mirror = await a_source(
        sess, url + "b", [BASE, at(0.5, 2)], retention_tier="primary"
    )

    await gate_for(sess, original + mirror).run_once()
    await sess.refresh(mirror_source)

    assert all(c.duplicate_of is not None for c in await refreshed(sess, mirror))
    assert mirror_source.retention_tier == "primary", "a primary source lost its raw file"


async def test_a_source_is_not_demoted_while_any_chunk_is_unjudged(
    session_for, url, cleanup
) -> None:
    """Demoting on a partial view would junk a long document whose first page
    happened to be boilerplate."""
    sess = await session_for("rw")
    _, original = await a_source(sess, url, [BASE, at(0.5, 2)])
    mirror_source, mirror = await a_source(sess, url + "b", [BASE, at(0.5, 2)])
    sess.add(Chunk(source_id=mirror_source.source_id, text="Not embedded.", chunk_index=7))
    await sess.flush()

    await gate_for(sess, original + mirror).run_once()
    await sess.refresh(mirror_source)

    assert mirror_source.retention_tier == "background"


async def test_a_partly_novel_source_is_kept(session_for, url, cleanup) -> None:
    """Half a page of new material is a source, not a near-duplicate."""
    sess = await session_for("rw")
    _, original = await a_source(sess, url, [BASE, at(0.5, 2)])
    mirror_source, mirror = await a_source(
        sess, url + "b", [BASE, at(0.5, 3), at(0.4, 4), at(0.2, 5)]
    )

    await gate_for(sess, original + mirror).run_once()
    await sess.refresh(mirror_source)

    assert mirror_source.retention_tier == "background"


async def test_demotion_needs_source_ids_to_consider(session_for, url, cleanup) -> None:
    sess = await session_for("rw")

    assert await demote_duplicate_sources(sess, []) == []


# --------------------------------------------------------------------------
# The health line (§12.5)
# --------------------------------------------------------------------------


async def test_the_health_line_moves_with_the_pass(session_for, url, cleanup) -> None:
    """A pass rate that collapses is how a mirror, or a site serving one page
    under every URL, becomes visible — every other number on the line reads as
    a healthy crawl."""
    sess = await session_for("rw")
    before = await novelty_health(sess)
    _, chunks = await a_source(sess, url, [BASE, BASE, at(0.3, 2)])

    assert (await novelty_health(sess)).pending == before.pending + 3

    stats = await gate_for(sess, chunks).run_once()
    after = await novelty_health(sess)

    assert (stats.judged, stats.duplicates) == (3, 1)
    assert stats.pass_rate == pytest.approx(2 / 3, abs=1e-3)
    assert after.judged == before.judged + 3
    assert after.duplicates == before.duplicates + 1
    assert after.pending == before.pending


async def test_a_bounded_pass_stops_where_it_was_told(session_for, url, cleanup) -> None:
    """``--max-batches`` exists so a first run on a real corpus is a
    measurement rather than a commitment."""
    sess = await session_for("rw")
    _, chunks = await a_source(sess, url, [at(0.4, i + 2) for i in range(6)])

    stats = await gate_for(sess, chunks, batch=2, max_batches=2).run_once()

    assert (stats.batches, stats.judged) == (2, 4)
    unjudged = [c for c in await refreshed(sess, chunks) if c.novelty_checked_at is None]
    assert len(unjudged) == 2
