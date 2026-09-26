"""The embedding sidecar (task P2-17, spec §4).

`FakeEmbedder` stands in for bge-m3, so the whole surface is exercised without a
2.3GB download — which is the same reason `P2-01` built it.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from worker.embeddings import EmbeddingError, FakeEmbedder
from worker.embedserver import MAX_TEXTS, SLICE, Abandoned, create_app, embed_in_slices


@pytest.fixture
def client():
    return TestClient(create_app(FakeEmbedder()))


def test_it_returns_one_vector_per_text_in_order(client) -> None:
    response = client.post("/embed", json={"texts": ["alpha", "beta", "gamma"]})

    assert response.status_code == 200
    body = response.json()
    assert len(body["vectors"]) == 3
    assert body["dimensions"] == len(body["vectors"][0])


def test_every_response_names_the_model(client) -> None:
    """So a caller can tell it is talking to the model its corpus was built
    with. Vectors from a different one compare without erroring and rank
    nonsense confidently — that is the whole reason this service exists rather
    than letting callers supply their own."""
    assert client.post("/embed", json={"texts": ["a"]}).json()["model"]


def test_an_empty_batch_is_refused(client) -> None:
    """Not an error the client should have to distinguish from a real one — the
    client refuses it before the request, and the server agrees."""
    assert client.post("/embed", json={"texts": []}).status_code == 422


def test_an_oversized_batch_is_refused(client) -> None:
    """Capped on both sides, so the refusal is the same whichever end is older
    after a deploy."""
    response = client.post("/embed", json={"texts": ["x"] * (MAX_TEXTS + 1)})

    assert response.status_code == 422


def test_a_model_that_cannot_load_is_a_503_not_a_500(client) -> None:
    """A dependency problem the caller should retry past, and one that
    `RemoteEmbedder` turns into a degraded search rather than an error page. A
    500 would read as a bug in this service."""

    class _Broken:
        dimensions = 1024

        def embed(self, texts):
            raise EmbeddingError("weights not present")

    broken = TestClient(create_app(_Broken()), raise_server_exceptions=False)

    assert broken.post("/embed", json={"texts": ["a"]}).status_code == 503


def test_health_says_whether_the_weights_are_resident(client) -> None:
    """Two facts, because they mean different things to whoever is waiting. A
    process that is up with no model loaded is starting and the first request
    will be slow; one up for ten minutes still reporting `loaded: false` has
    never been asked for anything."""
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert "loaded" in body
    assert body["dimensions"] > 0


def test_health_answers_before_the_model_is_loaded(client) -> None:
    """A supervisor uses this to decide whether the process is alive, and it
    must not have to wait for a 2.3GB load to find out."""
    assert client.get("/health").status_code == 200


# -- B-82: a batch nobody is waiting for is not finished -----------------------------


def _counting_embedder():
    calls: list[int] = []
    fake = FakeEmbedder()

    def embed(texts):
        calls.append(len(texts))
        return fake.embed(texts)

    return embed, calls


def test_slices_give_the_same_vectors_in_order() -> None:
    embed, calls = _counting_embedder()
    texts = [f"text {i}" for i in range(SLICE * 2 + 5)]

    async def present() -> bool:
        return False

    vectors = asyncio.run(embed_in_slices(embed, texts, present))
    assert vectors == FakeEmbedder().embed(texts)
    assert calls == [SLICE, SLICE, 5]


def test_a_client_that_left_stops_the_batch_after_the_current_slice() -> None:
    embed, calls = _counting_embedder()
    texts = [f"text {i}" for i in range(SLICE * 4)]
    asked = 0

    async def gone_after_first() -> bool:
        nonlocal asked
        asked += 1
        return True

    with pytest.raises(Abandoned, match=f"after {SLICE} of {SLICE * 4}"):
        asyncio.run(embed_in_slices(embed, texts, gone_after_first))
    assert calls == [SLICE], "only the slice already running was computed"


def test_a_small_batch_is_never_asked_about_the_client() -> None:
    """One slice is the whole job; checking would only add a round trip."""
    embed, calls = _counting_embedder()

    async def never() -> bool:
        raise AssertionError("asked")

    assert len(asyncio.run(embed_in_slices(embed, ["a", "b"], never))) == 2


@pytest.mark.parametrize("size", [0, -3])
def test_an_empty_slice_is_refused(size) -> None:
    embed, _ = _counting_embedder()

    async def present() -> bool:
        return False

    with pytest.raises(ValueError):
        asyncio.run(embed_in_slices(embed, ["a"], present, size))


def test_the_route_still_answers_a_full_batch(client) -> None:
    texts = [f"t{i}" for i in range(MAX_TEXTS)]
    response = client.post("/embed", json={"texts": texts})
    assert response.status_code == 200
    assert len(response.json()["vectors"]) == MAX_TEXTS
