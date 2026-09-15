"""Hybrid retrieval against a real Postgres (task P2-06, spec §12.5).

Against a real database because both arms are database features: `@@` and
`ts_rank_cd` are Postgres text search, `<=>` is pgvector, and "filters before
the vector search" is a claim about where a predicate sits in a query plan.
None of that is observable from a double.

**Scoping matters here more than in most of this suite.** The dev database holds
a real crawl, and a vector search is over the *whole* corpus by construction —
there is no WHERE clause hiding the rest of it. So every test filters to its own
fixtures by a per-run language code, and the lexical assertions use a nonsense
term that cannot appear in crawled text. A test that searched unscoped would
pass or fail depending on what was last crawled.
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.models import Chunk, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.search import SearchFilters, search
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


def at(similarity: float, axis: int) -> list[float]:
    """A unit vector at a chosen cosine from ``QUERY``. Same device as the
    novelty tests: constructed angles rather than an embedder, because the
    distances are the subject and a real model's are not reproducible."""
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = similarity
    vector[axis] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


QUERY = at(1.0, 1)


@pytest.fixture
def scope() -> str:
    """A language code unique to this test run.

    Doubles as the scoping filter and as a live exercise of the filter path —
    if filtering were broken, these tests would drown in the dev corpus rather
    than quietly passing.
    """
    return f"zz-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def term() -> str:
    """A token that cannot occur in crawled text, so lexical hits are ours."""
    return f"qxz{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def cleanup(session_for, scope):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(Source).where(Source.language == scope))
    await sess.commit()


async def a_source(sess, scope: str, texts, vectors, **kwargs) -> Source:
    source, _ = await upsert_source(
        sess,
        f"https://s{uuid.uuid4().hex[:12]}.test/a",
        checksum=f"sha256:{uuid.uuid4().hex}",
        language=scope,
        **kwargs,
    )
    await replace_chunks(
        sess,
        source.source_id,
        [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)],
    )
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_index)
    )
    chunks = list(rows.scalars())
    await store_embeddings(sess, {c.chunk_id: v for c, v in zip(chunks, vectors, strict=True)})
    await sess.flush()
    return source


def only(scope: str, **kwargs) -> SearchFilters:
    return SearchFilters(languages=[scope], **kwargs)


# --------------------------------------------------------------------------
# The two arms, and what happens when one is missing
# --------------------------------------------------------------------------


async def test_a_lexical_match_is_found(session_for, scope, term, cleanup) -> None:
    sess = await session_for("rw")
    await a_source(sess, scope, [f"The {term} programme was reviewed."], [at(0.2, 5)])

    result = await search(sess, term, filters=only(scope))

    assert [h.text for h in result.hits] == [f"The {term} programme was reviewed."]
    assert result.arms == {"lexical"}
    assert result.degraded is True, "a search with no query vector ran half a search"


async def test_a_vector_neighbour_is_found_with_no_matching_words(
    session_for, scope, cleanup
) -> None:
    """The half of hybrid search that exists because wording and meaning differ."""
    sess = await session_for("rw")
    await a_source(sess, scope, ["Entirely unrelated wording."], [at(0.99, 3)])

    result = await search(sess, "", query_vector=QUERY, filters=only(scope))

    assert [h.text for h in result.hits] == ["Entirely unrelated wording."]
    assert result.arms == {"vector"}


async def test_both_arms_report_themselves(session_for, scope, term, cleanup) -> None:
    sess = await session_for("rw")
    await a_source(sess, scope, [f"A {term} report."], [at(0.98, 3)])

    result = await search(sess, term, query_vector=QUERY, filters=only(scope))

    hit = result.hits[0]
    assert hit.lexical_rank == 1 and hit.vector_rank == 1
    assert result.degraded is False


async def test_an_empty_search_returns_nothing_rather_than_raising(session_for, scope) -> None:
    """An empty search box is a state a UI has on first render, not an error."""
    sess = await session_for("rw")
    result = await search(sess, "   ", filters=only(scope))
    assert result.hits == [] and result.arms == frozenset()


# --------------------------------------------------------------------------
# Filters go *before* the vector search — §12.5
# --------------------------------------------------------------------------


async def test_the_filter_is_inside_the_vector_query_not_applied_to_its_output(
    session_for, scope, cleanup
) -> None:
    """The anti-pattern this test exists to catch.

    Take the top-k by distance, then drop what fails the filter, and a search
    for government sources returns whatever is left over — frequently nothing.
    It is invisible in a result set: an empty page reads as a corpus with no
    government material rather than as a query built the wrong way round.

    So: the two nearest neighbours are `press`, and the candidate pool is
    exactly two. Post-filtering would leave zero hits. A predicate inside the
    query reaches past them to the government chunks behind.
    """
    sess = await session_for("rw")
    await a_source(sess, scope, ["Nearest press."], [at(0.99, 3)], source_tier="press")
    await a_source(sess, scope, ["Also press."], [at(0.98, 4)], source_tier="press")
    await a_source(
        sess, scope, ["Government, further away."], [at(0.70, 5)], source_tier="government"
    )

    result = await search(
        sess,
        "",
        query_vector=QUERY,
        filters=only(scope, source_tiers=["government"]),
        candidates=2,
    )

    assert [h.text for h in result.hits] == ["Government, further away."]
    assert all(h.source_tier == "government" for h in result.hits)


async def test_the_lexical_arm_narrows_the_same_way(session_for, scope, term, cleanup) -> None:
    """Both arms must narrow identically, or fusion compares two populations.

    The arm that drifted would be the one quietly returning material the caller
    excluded — and because RRF rewards agreement, a hit only the wider arm found
    still lands in the result set.
    """
    sess = await session_for("rw")
    await a_source(sess, scope, [f"A {term} press item."], [at(0.99, 3)], source_tier="press")

    lexical_only = await search(sess, term, filters=only(scope, source_tiers=["government"]))
    vector_only = await search(
        sess, "", query_vector=QUERY, filters=only(scope, source_tiers=["government"])
    )

    assert lexical_only.hits == [] and vector_only.hits == []


async def test_a_date_window_applies(session_for, scope, term, cleanup) -> None:
    import datetime as dt

    sess = await session_for("rw")
    await a_source(
        sess, scope, [f"Old {term}."], [at(0.9, 3)], publication_date=dt.date(2019, 1, 1)
    )
    await a_source(
        sess, scope, [f"Recent {term}."], [at(0.9, 4)], publication_date=dt.date(2026, 1, 1)
    )

    result = await search(sess, term, filters=only(scope, published_after=dt.date(2024, 1, 1)))

    assert [h.text for h in result.hits] == [f"Recent {term}."]


# --------------------------------------------------------------------------
# What the novelty gate's verdict is for
# --------------------------------------------------------------------------


async def test_near_duplicates_are_excluded_by_default_and_available_on_request(
    session_for, scope, term, cleanup
) -> None:
    """`P2-03` marks rather than deletes precisely so this is a search-time
    choice. Excluded by default because the gate said they add nothing;
    retrievable because a surface has to be able to show why."""
    sess = await session_for("rw")
    source = await a_source(
        sess, scope, [f"First {term}.", f"Second {term}."], [at(0.9, 3), at(0.9, 4)]
    )
    rows = await sess.execute(
        select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_index)
    )
    first, second = list(rows.scalars())
    second.duplicate_of = first.chunk_id
    await sess.flush()

    default = await search(sess, term, filters=only(scope))
    widened = await search(sess, term, filters=only(scope, include_duplicates=True))

    assert [h.chunk_id for h in default.hits] == [first.chunk_id]
    assert {h.chunk_id for h in widened.hits} == {first.chunk_id, second.chunk_id}
    assert (
        next(h for h in widened.hits if h.chunk_id == second.chunk_id).duplicate_of
        == first.chunk_id
    )


async def test_junk_sources_do_not_answer_questions(session_for, scope, term, cleanup) -> None:
    """§5.4's junk tier is material a sweep will drop (`P1-31`). Until it runs,
    it should not be citable."""
    sess = await session_for("rw")
    await a_source(sess, scope, [f"Boilerplate {term}."], [at(0.9, 3)], retention_tier="junk")

    assert (await search(sess, term, filters=only(scope))).hits == []
    assert (await search(sess, term, filters=only(scope, include_junk=True))).hits


# --------------------------------------------------------------------------
# Provenance and stability
# --------------------------------------------------------------------------


async def test_every_hit_carries_the_citation(session_for, scope, term, cleanup) -> None:
    """§2 principle 3: nothing is assertable without a citation you can follow.
    A hit that returned text and left the caller to look up its source would
    make this a RAG endpoint."""
    sess = await session_for("rw")
    source = await a_source(
        sess, scope, [f"A {term} finding."], [at(0.9, 3)], source_tier="government", title="A title"
    )

    hit = (await search(sess, term, filters=only(scope))).hits[0]

    assert hit.url == source.url
    assert hit.title == "A title"
    assert hit.source_tier == "government"
    assert hit.page_or_offset is not None or hit.chunk_index == 0


async def test_stemming_reaches_search(session_for, scope, cleanup) -> None:
    """End-to-end proof that the query is parsed under the same configuration
    the column was generated under (`P2-05`). A mismatch does not error — it
    stops matching inflected forms, which reads as a thin corpus."""
    sess = await session_for("rw")
    await a_source(sess, scope, ["Several pedestrian crossings were added."], [at(0.2, 5)])

    result = await search(sess, "crossing", filters=only(scope))

    assert len(result.hits) == 1


async def test_identical_searches_return_an_identical_order(
    session_for, scope, term, cleanup
) -> None:
    """Ties are common in a small corpus, and a result set that reshuffles
    between identical queries is indistinguishable from one that changed."""
    sess = await session_for("rw")
    await a_source(
        sess,
        scope,
        [f"{term} one.", f"{term} two.", f"{term} three."],
        [at(0.5, 3), at(0.5, 4), at(0.5, 5)],
    )

    first = await search(sess, term, query_vector=QUERY, filters=only(scope))
    second = await search(sess, term, query_vector=QUERY, filters=only(scope))

    assert [h.chunk_id for h in first.hits] == [h.chunk_id for h in second.hits]


async def test_limit_bounds_the_result_not_the_candidate_pool(
    session_for, scope, term, cleanup
) -> None:
    """Fusion can only reorder what the arms handed it, so the pool must be
    deeper than the result. A `limit` that also capped candidates would make
    RRF a no-op — every hit would already be in its final order."""
    sess = await session_for("rw")
    await a_source(
        sess, scope, [f"{term} {i}." for i in range(5)], [at(0.5, 3 + i) for i in range(5)]
    )

    result = await search(sess, term, query_vector=QUERY, filters=only(scope), limit=2)

    assert len(result.hits) == 2
    assert result.lexical_candidates == 5, "the arm was truncated to the result size"


# --------------------------------------------------------------------------
# Superseded chunks are never retrieved (task P1-32)
# --------------------------------------------------------------------------
#
# A superseded chunk is text a page used to carry. Retrieving one would have the
# corpus quote a document as saying something it no longer says, with a citation
# that opens the current page and does not contain the passage — which is the
# one failure a corpus built on "nothing is assertable without a citation you
# can follow" cannot afford.


async def test_a_superseded_chunk_is_not_found_lexically(
    session_for, scope, term, cleanup
) -> None:
    sess = await session_for("rw")
    source = await a_source(sess, scope, [f"The {term} programme was reviewed."], [at(0.2, 5)])
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text="Something else.", chunk_index=0)]
    )

    result = await search(sess, term, filters=only(scope))

    assert result.hits == []


async def test_a_superseded_chunk_is_not_found_by_vector(session_for, scope, cleanup) -> None:
    # The second arm, separately: `_conditions` is the single filter source for
    # both, and a filter that reached only the lexical query would leave the
    # vector arm serving retired text — visible only on queries that happened to
    # match by meaning rather than by words.
    sess = await session_for("rw")
    source = await a_source(sess, scope, ["Entirely unrelated wording."], [at(0.99, 3)])
    await replace_chunks(sess, source.source_id, [ChunkWrite(text="Replaced.", chunk_index=0)])

    result = await search(sess, "", query_vector=QUERY, filters=only(scope))

    assert [h.text for h in result.hits] == []


async def test_the_replacement_is_found_in_its_place(session_for, scope, term, cleanup) -> None:
    # The converse, without which the two tests above would pass against a
    # search that had stopped returning anything at all.
    sess = await session_for("rw")
    source = await a_source(sess, scope, ["Original wording."], [at(0.2, 5)])
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text=f"The {term} programme.", chunk_index=0)]
    )

    result = await search(sess, term, filters=only(scope))

    assert [h.text for h in result.hits] == [f"The {term} programme."]


async def test_a_caller_cannot_ask_for_superseded_chunks(
    session_for, scope, term, cleanup
) -> None:
    # Not a filter a caller may turn off. `include_duplicates` exists because a
    # near-duplicate is a *verdict* worth re-examining; a superseded chunk is
    # not a verdict, it is text the page no longer has.
    sess = await session_for("rw")
    source = await a_source(sess, scope, [f"The {term} programme was reviewed."], [at(0.2, 5)])
    await replace_chunks(
        sess, source.source_id, [ChunkWrite(text="Something else.", chunk_index=0)]
    )

    result = await search(
        sess, term, filters=only(scope, include_duplicates=True, include_junk=True)
    )

    assert result.hits == []
