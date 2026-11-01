"""The headline filter's centroids, read from a real Postgres (`B-251`).

Rolled back at the end; the topic names are unique to the run so the dev database's own
passages do not enter the topic centroids. See docs/features/discovery.md#headline-filter.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from sqlalchemy import select, update

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.headlinetopics import (
    MIN_CENTROID_PASSAGES,
    offtopic_centroid,
    topic_centroids,
)
from meridian_core.models import Chunk, ChunkTopics
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.sources import upsert_source


def axis(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def passages(sess, count: int, vector: list[float], labels: list[str]) -> list[int]:
    """One source with ``count`` embedded, labelled passages."""
    source, _ = await upsert_source(
        sess, f"https://h{uuid.uuid4().hex[:12]}.test/doc", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"passage {i}", chunk_index=i) for i in range(count)],
    )
    await sess.flush()
    ids = list(
        await sess.scalars(select(Chunk.chunk_id).where(Chunk.source_id == source.source_id))
    )
    await sess.execute(update(Chunk).where(Chunk.chunk_id.in_(ids)).values(embedding=vector))
    for chunk_id in ids:
        sess.add(
            ChunkTopics(chunk_id=chunk_id, topic_labels=labels, topic_scores={}, topic_basis="t")
        )
    await sess.flush()
    return ids


async def test_a_topic_centroid_is_its_passages_mean_and_a_thin_topic_has_none(sess) -> None:
    rich, thin, empty = (f"headline-test-{uuid.uuid4().hex[:8]}" for _ in range(3))
    await passages(sess, MIN_CENTROID_PASSAGES + 5, axis(3), [rich])
    await passages(sess, MIN_CENTROID_PASSAGES - 1, axis(4), [thin])

    got = await topic_centroids(sess, [rich, thin, empty])

    assert set(got) == {rich}
    assert got[rich].shape == (EMBEDDING_DIM,)
    assert float(np.dot(got[rich], axis(3))) == pytest.approx(1.0, abs=1e-4)


async def test_superseded_passages_are_not_in_a_centroid(sess) -> None:
    topic = f"headline-test-{uuid.uuid4().hex[:8]}"
    ids = await passages(sess, MIN_CENTROID_PASSAGES + 5, axis(5), [topic])
    await sess.execute(
        update(Chunk).where(Chunk.chunk_id.in_(ids)).values(superseded_at=Chunk.created_at)
    )
    await sess.flush()

    assert await topic_centroids(sess, [topic]) == {}


async def test_the_offtopic_centroid_is_found_on_a_small_corpus_too(sess) -> None:
    """The 1% page sample can miss every row of a small database; the full read must not.

    Only the shape is asserted: the dev database may hold unlabelled passages of its own.
    """
    await passages(sess, MIN_CENTROID_PASSAGES + 5, axis(6), [])

    got = await offtopic_centroid(sess)

    assert got is not None and got.shape == (EMBEDDING_DIM,)
    assert float(np.linalg.norm(got)) <= 1.0 + 1e-6
