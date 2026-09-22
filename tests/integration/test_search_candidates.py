"""The vector arm returns the candidates it was asked for (`B-24`, §12.5).

Every other search test uses a handful of vectors, which is exactly the size
at which this failure is invisible: pgvector's HNSW index yields at most
`hnsw.ef_search` tuples per scan and the default is 40, so a corpus with fewer
than forty vectors can never show the ceiling. Above it, `LIMIT 100` quietly
returns 33.

That matters twice over. Fusion can only reorder what the arms hand it, so an
arm truncated to a third of its depth is systematically under-weighted against
one that is not — and a recall figure measured at k=100 would be capped at 40%
by arithmetic rather than by the index, which is a fourth way a benchmark lies.

So this file seeds more vectors than the ceiling and asks for more than it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, text

from meridian_core.models import Chunk, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.search import DEFAULT_CANDIDATES, EF_SEARCH_FACTOR, SearchFilters, _vector

pytestmark = pytest.mark.usefixtures("require_db")

#: Comfortably over pgvector's default `ef_search` of 40, and over the pool the
#: test asks for, so a shortfall is the index's doing rather than the corpus's.
SEEDED = DEFAULT_CANDIDATES + 40


def vector(seed: int) -> list[float]:
    """A deterministic unit-ish vector, spread so neighbours differ.

    Not random: a test that fails once every few hundred runs because two
    vectors collided is worse than no test.
    """
    return [((seed * 7 + index * 13) % 1000) / 1000.0 for index in range(EMBEDDING_DIM)]


@pytest.fixture
async def corpus(session_for):
    """More embedded chunks than the index's default ceiling."""
    marker = f"cand{uuid.uuid4().hex[:8]}"
    sess = await session_for("rw")
    await sess.rollback()

    source = Source(
        url=f"https://{marker}.test/doc",
        source_tier="government",
        retention_tier="primary",
        checksum=f"sha256:{uuid.uuid4().hex}",
        text_available=True,
    )
    sess.add(source)
    await sess.flush()
    # As an int, before the commit below expires every attribute on the row:
    # reading it again in teardown would be lazy IO outside the async context.
    source_id = source.source_id

    sess.add_all(
        [
            Chunk(
                source_id=source_id,
                text=f"{marker} passage {index} about transport policy and measurement",
                chunk_index=index,
                embedding=vector(index),
            )
            for index in range(SEEDED)
        ]
    )
    await sess.commit()

    yield sess, source_id, marker

    await sess.rollback()
    await sess.execute(delete(Chunk).where(Chunk.source_id == source_id))
    await sess.execute(delete(Source).where(Source.source_id == source_id))
    await sess.commit()


async def test_the_vector_arm_widens_the_index_search(corpus) -> None:
    """The mechanism, because the emergent behaviour cannot be tested here.

    What should be asserted is "ask for 100 candidates, get 100". It cannot be,
    honestly, on a fixture this size: a few hundred vectors is small enough
    that the planner prefers a sequential scan — exact, full limit, `ef_search`
    never consulted — and forcing `enable_seqscan = off` does not help either,
    because a graph that small is traversed almost entirely whatever the
    candidate list is bounded to. The test passed against the bug both ways.

    The shortfall is real and was measured on a live corpus of 4,480 vectors:
    `LIMIT 100` returned **33**. What this file can pin is that the arm sets
    the depth at all, and to a figure derived from what it asked for — which
    is the line that was missing.
    """
    sess, _, _ = corpus

    await _vector(sess, vector(0), SearchFilters(), DEFAULT_CANDIDATES)
    setting = await sess.scalar(text("SELECT current_setting('hnsw.ef_search', true)"))

    assert setting == str(DEFAULT_CANDIDATES * EF_SEARCH_FACTOR), (
        f"the vector arm left hnsw.ef_search at {setting!r}; pgvector's default "
        "of 40 is a ceiling on rows returned, so the arm would answer short of "
        "the pool it asked for and nothing would say so"
    )


async def test_the_setting_is_local_to_the_transaction(corpus) -> None:
    """`set_config(..., is_local => true)`, so a pooled connection does not
    carry one caller's depth into the next one's query."""
    sess, _, _ = corpus

    await _vector(sess, vector(0), SearchFilters(), DEFAULT_CANDIDATES)
    await sess.rollback()

    current = await sess.scalar(text("SELECT current_setting('hnsw.ef_search', true)"))
    assert current in (None, "", "40"), (
        f"ef_search survived the transaction as {current!r}; it must not outlive it"
    )


async def test_the_pool_covers_what_a_filter_discards(corpus) -> None:
    """The index cannot see the filter, so it spends candidates on rows the
    filter then drops. A factor of one would be a pool that is correct only for
    an unfiltered query — which is not the query this system makes."""
    assert EF_SEARCH_FACTOR >= 2
