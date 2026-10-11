"""Reranking search results (task `B-167`, docs/features/search.md#reranking).

The sidecar route, its client, and the re-ordering, each without a model download.
The refusals and fallbacks are the subject: a misaligned score moves a passage
silently, and a reranker that is off or down must leave search answering as before.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from fastapi.testclient import TestClient

from meridian_core.embedder import EmbeddingUnavailable, RemoteEmbedder, RerankingOff
from meridian_core.rerank import RERANK_DEPTH, reorder, rerank_hits
from worker.embeddings import FakeEmbedder
from worker.embedserver import MAX_TEXTS, create_app
from worker.rerank import FakeReranker, RerankerSettings


@dataclasses.dataclass(frozen=True)
class Hit:
    text: str


# --------------------------------------------------------------------------
# The sidecar
# --------------------------------------------------------------------------


@pytest.fixture
def on():
    return TestClient(create_app(FakeEmbedder(), FakeReranker()))


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv("MERIDIAN_RERANK_MODEL", raising=False)
    return TestClient(create_app(FakeEmbedder()))


def test_scores_come_back_one_per_text_in_order(on) -> None:
    body = on.post(
        "/rerank", json={"query": "bus cost", "texts": ["nothing", "bus cost per trip", "bus"]}
    ).json()
    assert body["scores"] == [0.0, 2.0, 1.0]
    assert body["model"] == "fake-reranker"


def test_off_is_a_404_not_an_error(off) -> None:
    """So the client can tell "not configured" from "failed"."""
    response = off.post("/rerank", json={"query": "q", "texts": ["a"]})
    assert response.status_code == 404
    assert off.get("/health").json()["rerank_model"] is None


def test_health_names_the_reranker(on) -> None:
    assert on.get("/health").json()["rerank_model"] == "fake-reranker"


@pytest.mark.parametrize(
    "body",
    [
        {"query": "", "texts": ["a"]},
        {"query": "q", "texts": []},
        {"query": "q", "texts": ["a"] * (MAX_TEXTS + 1)},
        {"texts": ["a"]},
    ],
)
def test_a_malformed_request_is_refused(on, body) -> None:
    assert on.post("/rerank", json=body).status_code == 422


def test_the_shared_secret_guards_reranking_too(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBEDDER_TOKEN", "s3cret")
    client = TestClient(create_app(FakeEmbedder(), FakeReranker()))
    assert client.post("/rerank", json={"query": "q", "texts": ["a"]}).status_code == 401
    allowed = client.post(
        "/rerank",
        json={"query": "q", "texts": ["a"]},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert allowed.status_code == 200


def test_a_reranker_that_fails_is_a_503(monkeypatch) -> None:
    from worker.embeddings import EmbeddingError

    class Broken(FakeReranker):
        def score(self, query, texts):
            raise EmbeddingError("no weights")

    client = TestClient(create_app(FakeEmbedder(), Broken()))
    assert client.post("/rerank", json={"query": "q", "texts": ["a"]}).status_code == 503


def test_no_model_named_means_off(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_RERANK_MODEL", "  ")
    assert RerankerSettings.from_env().enabled is False


def test_the_reranker_follows_the_embedders_device(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBED_DEVICE", "cuda")
    monkeypatch.delenv("MERIDIAN_RERANK_DEVICE", raising=False)
    settings = RerankerSettings.from_env()
    assert (settings.device, settings.dtype) == ("cuda", "float16")
    monkeypatch.setenv("MERIDIAN_RERANK_DEVICE", "cpu")
    assert RerankerSettings.from_env().device == "cpu"


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------


def client_answering(handler) -> RemoteEmbedder:
    return RemoteEmbedder(
        "http://sidecar.test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_the_client_returns_the_scores() -> None:
    client = client_answering(lambda r: httpx.Response(200, json={"scores": [1, 3], "model": "m"}))
    assert await client.rerank("q", ["a", "b"]) == [1.0, 3.0]


async def test_a_404_reads_as_off_not_as_an_outage() -> None:
    client = client_answering(lambda r: httpx.Response(404, json={"detail": "off"}))
    with pytest.raises(RerankingOff):
        await client.rerank("q", ["a"])


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"scores": [1.0], "model": "m"}),
        httpx.Response(200, json={"model": "m"}),
        httpx.Response(503, json={"detail": "down"}),
        httpx.Response(200, text="not json"),
    ],
)
async def test_short_missing_or_failed_scores_are_an_outage(response) -> None:
    client = client_answering(lambda r: response)
    with pytest.raises(EmbeddingUnavailable):
        await client.rerank("q", ["a", "b"])


async def test_no_texts_is_no_request() -> None:
    def refuse(request):
        raise AssertionError("asked the sidecar for nothing")

    assert await client_answering(refuse).rerank("q", []) == []


# --------------------------------------------------------------------------
# Re-ordering
# --------------------------------------------------------------------------


def test_the_top_is_reordered_and_the_rest_keeps_its_place() -> None:
    assert reorder(list("abcde"), [0.1, 0.9, 0.5]) == list("bcade")


def test_ties_keep_the_fused_order() -> None:
    assert reorder(list("abc"), [1.0, 1.0, 1.0]) == list("abc")


def test_more_scores_than_hits_is_refused() -> None:
    with pytest.raises(ValueError):
        reorder(["a"], [1.0, 2.0])


class Scorer:
    def __init__(self, scores=None, raises=None) -> None:
        self.scores, self.raises, self.asked = scores, raises, None

    async def rerank(self, query, texts):
        self.asked = list(texts)
        if self.raises:
            raise self.raises
        return self.scores or [float(i) for i in range(len(texts))]


async def test_only_the_depth_is_sent_and_reordered() -> None:
    hits = [Hit(f"t{i}") for i in range(RERANK_DEPTH + 5)]
    scorer = Scorer()
    ranked, done = await rerank_hits(scorer, "q", hits)
    assert done is True
    assert len(scorer.asked) == RERANK_DEPTH
    assert ranked[:RERANK_DEPTH] == hits[:RERANK_DEPTH][::-1]
    assert ranked[RERANK_DEPTH:] == hits[RERANK_DEPTH:]


@pytest.mark.parametrize(
    "scorer",
    [None, Scorer(raises=RerankingOff("off")), Scorer(raises=EmbeddingUnavailable("down"))],
)
async def test_without_a_reranker_the_order_is_kept_and_said_so(scorer) -> None:
    hits = [Hit("a"), Hit("b"), Hit("c")]
    assert await rerank_hits(scorer, "q", hits) == (hits, False)


async def test_one_hit_is_not_worth_a_request() -> None:
    scorer = Scorer()
    assert await rerank_hits(scorer, "q", [Hit("a")]) == ([Hit("a")], False)
    assert scorer.asked is None
