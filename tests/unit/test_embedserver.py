"""The embedding sidecar (task P2-17, spec §4).

`FakeEmbedder` stands in for bge-m3, so the whole surface is exercised without a
2.3GB download — which is the same reason `P2-01` built it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from worker.embeddings import EmbeddingError, FakeEmbedder
from worker.embedserver import MAX_TEXTS, create_app


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
