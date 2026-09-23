"""The corpus map against a real Postgres (tasks P6-26, P6-29).

The claim worth checking is that the map draws the corpus *search* sees. Every
exclusion it inherits — superseded passages, near-duplicates, junk, chunks the
embedder has not reached — is a WHERE clause, and a clause that silently fell
off would draw a picture of a different corpus with nothing looking wrong.

**Scoped by a topic label unique to the run.** The map is unscoped by design —
it is a picture of everything — so the tests narrow it with its own topic
filter, which also exercises that filter reaching the SQL. Fixtures commit,
because the API reads on its own read-only connection.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.chunks import ChunkWrite, replace_chunks, store_embeddings
from meridian_core.corpusmap import MAX_SAMPLE, corpus_map
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, Source
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")


def axis(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    return vector


@pytest.fixture
def topic() -> str:
    return f"map-{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def corpus(session_for, topic: str):
    """Two sources: one ordinary, one junk. The ordinary one carries six chunks
    of which only three are drawable — one of each exclusion beside them.

    Returns the chunk ids that should appear on the map.
    """
    sess = await session_for("rw")
    host = f"{topic}.test"

    async def a_source(name: str, texts: list[str], **kwargs) -> list[Chunk]:
        source, _ = await upsert_source(
            sess,
            f"https://{host}/{name}",
            checksum=f"sha256:{uuid.uuid4().hex}",
            title=f"Title {name}",
            **kwargs,
        )
        source.topic_labels = [topic, "second-label"]
        await replace_chunks(
            sess,
            source.source_id,
            [ChunkWrite(text=t, chunk_index=i) for i, t in enumerate(texts)],
        )
        rows = await sess.execute(
            select(Chunk).where(Chunk.source_id == source.source_id).order_by(Chunk.chunk_index)
        )
        return list(rows.scalars())

    kept = await a_source(
        "kept", ["alpha", "beta", "gamma", "duplicate", "superseded", "not embedded"]
    )
    junk = await a_source("junk", ["junk passage"], retention_tier="junk")

    await store_embeddings(
        sess, {chunk.chunk_id: axis(i + 1) for i, chunk in enumerate([*kept[:5], *junk])}
    )
    kept[3].duplicate_of = kept[0].chunk_id
    kept[4].superseded_at = dt.datetime.now(dt.UTC)
    await sess.commit()

    yield [c.chunk_id for c in kept[:3]]

    await sess.execute(delete(Source).where(Source.url.like(f"https://{host}/%")))
    await sess.commit()


async def test_the_map_draws_exactly_what_search_would_return(session_for, topic, corpus) -> None:
    sess = await session_for("ro")
    result = await corpus_map(sess, topics=[topic])

    assert sorted(p.chunk_id for p in result.points) == sorted(corpus)
    assert result.eligible == 3
    # Drawn in the source's first label, so a colour means one thing.
    assert {p.topic for p in result.points} == {topic}
    assert all(p.title == "Title kept" for p in result.points)
    assert {p.snippet for p in result.points} == {"alpha", "beta", "gamma"}


async def test_a_sample_is_smaller_than_eligible_and_says_so(session_for, topic, corpus) -> None:
    sess = await session_for("ro")
    result = await corpus_map(sess, topics=[topic], sample=2)

    assert len(result.points) == 2
    assert result.eligible == 3, "the reader must be told the picture is a sample"


async def test_the_same_corpus_samples_and_draws_the_same_way(session_for, topic, corpus) -> None:
    """Hash-ordered, not random: a map that reshuffled on every refresh would
    make "that cluster moved" a statement about the sampler."""
    sess = await session_for("ro")
    first = await corpus_map(sess, topics=[topic], sample=2)
    second = await corpus_map(sess, topics=[topic], sample=2)

    assert [(p.chunk_id, p.x, p.y, p.z) for p in first.points] == [
        (p.chunk_id, p.x, p.y, p.z) for p in second.points
    ]


async def test_a_topic_with_nothing_in_it_is_an_empty_map(session_for) -> None:
    sess = await session_for("ro")
    result = await corpus_map(sess, topics=[f"absent-{uuid.uuid4().hex}"])

    assert result.points == [] and result.eligible == 0
    assert result.explained_variance == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("sample", [0, -1, MAX_SAMPLE + 1])
async def test_an_out_of_range_sample_is_refused(session_for, sample: int) -> None:
    sess = await session_for("ro")
    with pytest.raises(ValueError, match="sample"):
        await corpus_map(sess, sample=sample)


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


async def test_the_endpoint_serves_the_map_through_the_read_only_role(
    client, topic, corpus
) -> None:
    response = await client.get("/api/explore/map", params={"topic": topic})

    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(p["chunk_id"] for p in body["points"]) == sorted(corpus)
    assert body["eligible"] == 3
    assert len(body["explained_variance"]) == 3
    for point in body["points"]:
        for axis in ("x", "y", "z"):
            assert -1.0 <= point[axis] <= 1.0, (axis, point)


@pytest.mark.parametrize("sample", ["0", str(MAX_SAMPLE + 1), "many"])
async def test_the_endpoint_refuses_a_bad_sample_before_querying(client, sample: str) -> None:
    response = await client.get("/api/explore/map", params={"sample": sample})
    assert response.status_code == 422
