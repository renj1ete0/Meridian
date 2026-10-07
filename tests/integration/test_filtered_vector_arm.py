"""A filtered search scans past what its filters remove, and returns nearest first (B-152).

Measured on the live corpus: with one topic as the filter, the vector arm returned about one
passage of the hundred asked for, because the index offered its nearest candidates and the filter
removed nearly all of them afterwards. Search then ran on words alone without saying so.
"""

from __future__ import annotations

import math
import random
import uuid

import pytest
from sqlalchemy import select, text

from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.models import Chunk
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.search import SearchFilters, _vector
from meridian_core.sources import upsert_source
from meridian_core.vectorindex import MAX_SCAN_TUPLES

pytestmark = pytest.mark.usefixtures("require_db")


#: A direction of this run's own, so vectors other tests left in the dev database are not near.
BASE = random.Random().randrange(500, EMBEDDING_DIM)


def at(similarity: float, axis: int) -> list[float]:
    """A unit vector at ``similarity`` to the base direction, off along ``axis`` (< 500)."""
    vector = [0.0] * EMBEDDING_DIM
    vector[BASE] = similarity
    vector[axis] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def source_with(sess, tier: str, vectors: list[list[float]]) -> list[int]:
    source, _ = await upsert_source(
        sess,
        f"https://v{uuid.uuid4().hex[:10]}.test/p",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier=tier,
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=f"Passage {i}.", chunk_index=i) for i in range(len(vectors))],
    )
    ids = list(
        await sess.scalars(
            select(Chunk.chunk_id)
            .where(Chunk.source_id == source.source_id)
            .order_by(Chunk.chunk_index)
        )
    )
    await store_embeddings(sess, dict(zip(ids, vectors, strict=True)))
    return ids


async def test_the_arm_scans_on_past_what_its_filters_remove(sess) -> None:
    """The mechanism, pinned. A synthetic cluster small enough for a test sits apart from the rest
    of the index's graph, where no scan reaches it, so the effect cannot be shown on the dev
    database; it was measured on a real corpus instead (docs/features/embedding.md#filtered-scans).
    """
    await _vector(sess, at(1.0, 1), SearchFilters(source_tiers=["government"]), 20)

    settings = (
        await sess.execute(
            text(
                "SELECT current_setting('hnsw.iterative_scan'), "
                "current_setting('hnsw.max_scan_tuples')"
            )
        )
    ).one()
    assert tuple(settings) == ("relaxed_order", str(MAX_SCAN_TUPLES))


async def test_results_come_back_nearest_first(sess) -> None:
    ids = await source_with(
        sess, "government", [at(s, 2 + i) for i, s in enumerate((0.7, 0.95, 0.8))]
    )
    for setting in ("enable_seqscan", "enable_sort"):
        await sess.execute(text(f"SET LOCAL {setting} = off"))

    found = await _vector(sess, at(1.0, 1), SearchFilters(source_tiers=["government"]), 50)
    mine = [i for i in found if i in ids]

    assert mine == [ids[1], ids[2], ids[0]]
