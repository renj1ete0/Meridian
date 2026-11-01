"""The remote embedder against an OpenAI-compatible server such as llama.cpp (`B-248`).

A mock transport stands in for llama.cpp, answering the way the real server was seen to
on 2026-11-01: results in any order (rerank sorts by score), the requested model name
echoed back, long inputs refused rather than cut, ``/tokenize`` without special tokens
unless asked, 501 from ``/v1/rerank`` on a server started without ``--reranking``.
"""

from __future__ import annotations

import json
import math

import httpx
import pytest

from meridian_core.embedder import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_RERANK_MAX_TOKENS,
    RERANK_PAIR_MARGIN,
    EmbedderMismatch,
    EmbeddingUnavailable,
    RemoteEmbedder,
    RerankingOff,
)

EMBED = "http://gpu.test:8101"
RERANK = "http://gpu.test:8102"
CLS, SEP = 0, 2
MODEL = "BAAI/bge-m3"


class FakeLlama:
    """One llama.cpp server: a token per character, a vector per input, a score per pair."""

    def __init__(self, model: str = MODEL, *, reverse: bool = False) -> None:
        self.model = model
        self.reverse = reverse
        self.requests: list[tuple[str, dict]] = []
        self.status: dict[str, int] = {}
        self.override: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.requests.append((path, body))
        if path in self.status:
            return httpx.Response(self.status[path], json={"error": {"message": "no"}})
        if path in self.override:
            return httpx.Response(200, json=self.override[path])
        if path == "/tokenize":
            tokens = [100 + ord(c) % 1000 for c in body["content"]]
            return httpx.Response(
                200, json={"tokens": [CLS, *tokens, SEP] if body.get("add_special") else tokens}
            )
        if path == "/detokenize":
            return httpx.Response(200, json={"content": "x" * len(body["tokens"])})
        if path == "/v1/embeddings":
            rows = [
                {"index": i, "embedding": [3.0, 4.0] if i % 2 == 0 else [0.0, 2.0]}
                for i in range(len(body["input"]))
            ]
            if self.reverse:
                rows.reverse()
            # llama.cpp names the model the request asked for, else its own.
            return httpx.Response(200, json={"model": body.get("model", self.model), "data": rows})
        if path == "/v1/rerank":
            scored = [
                {"index": i, "relevance_score": float(len(d))}
                for i, d in enumerate(body["documents"])
            ]
            scored.sort(key=lambda r: -r["relevance_score"])
            return httpx.Response(200, json={"model": self.model, "results": scored})
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": self.model}]})
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]


def both(embed: FakeLlama, rerank: FakeLlama | None = None):
    """A transport routing by port, as two servers on one GPU machine."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8102:
            return (rerank or FakeLlama())(request)
        return embed(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def client(embed: FakeLlama, rerank: FakeLlama | None = None, **kw) -> RemoteEmbedder:
    kw.setdefault("expect_model", MODEL)
    return RemoteEmbedder(EMBED, api="openai", rerank_url=RERANK, client=both(embed, rerank), **kw)


# --------------------------------------------------------------------------
# Embedding
# --------------------------------------------------------------------------


async def test_vectors_come_back_in_input_order_whatever_order_the_server_uses() -> None:
    server = FakeLlama(reverse=True)
    vectors = await client(server).embed(["a", "b", "c"])
    assert vectors == [[0.6, 0.8], [0.0, 1.0], [0.6, 0.8]]


async def test_vectors_are_scaled_to_length_one() -> None:
    """The worker stores unit vectors; a server started with `--embd-normalize -1` must
    not put longer ones beside them."""
    for vector in await client(FakeLlama()).embed(["a", "b"]):
        assert math.isclose(math.hypot(*vector), 1.0)


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [1.0]}],  # one short
        [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}],  # repeated
        [{"index": 0, "embedding": [1.0]}, {"index": 2, "embedding": [1.0]}],  # a gap
        [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [0.0]}],  # zero vector
        [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": []}],  # empty
    ],
)
async def test_rows_that_do_not_name_each_text_once_are_refused(data) -> None:
    server = FakeLlama()
    server.override["/v1/embeddings"] = {"model": MODEL, "data": data}
    with pytest.raises(EmbeddingUnavailable):
        await client(server).embed(["a", "b"])


async def test_a_short_text_is_sent_as_text_without_tokenizing() -> None:
    server = FakeLlama()
    await client(server).embed(["short"])
    assert "/tokenize" not in server.paths()
    assert server.requests[-1][1]["input"] == ["short"]


async def test_a_long_text_is_cut_as_the_worker_cuts_it() -> None:
    """Leading tokens and the closing special token, `max_tokens` in all: measured
    against PyTorch at cosine 0.9993 on a 2,103-token text (docs/features/embedding.md)."""
    server = FakeLlama()
    await client(server, max_tokens=10).embed(["abcdefghijklmnop", "ok"])
    sent = server.requests[-1][1]["input"]
    assert len(sent[0]) == 10
    assert sent[0][0] == CLS and sent[0][-1] == SEP
    assert sent[0][1:9] == [100 + ord(c) for c in "abcdefgh"]
    assert sent[1] == "ok"


async def test_a_long_text_that_fits_in_tokens_is_sent_as_text() -> None:
    server = FakeLlama()
    await client(server, max_tokens=8).embed(["abcdef"])  # 6 + 2 special tokens
    assert server.requests[-1][1]["input"] == ["abcdef"]


async def test_without_a_tokenizer_the_text_goes_whole_and_the_server_truncates() -> None:
    """`/tokenize` is llama.cpp's; Ollama, vLLM and the rest answer 404 and cut for
    themselves. Asked once per server, not once per text."""
    server = FakeLlama()
    server.status["/tokenize"] = 404
    embedder = client(server, max_tokens=5)
    await embedder.embed(["abcdefghij", "klmnopqrst"])
    await embedder.embed(["uvwxyzabcd"])
    assert server.paths().count("/tokenize") == 1
    assert server.requests[-1][1]["input"] == ["uvwxyzabcd"]


async def test_every_request_names_the_model_a_router_needs() -> None:
    """A server holding several models routes by the name; without it llama.cpp's router
    answers 400 "model name is missing"."""
    server = FakeLlama()
    await client(server, max_tokens=5).embed(["abcdefgh"])
    for path, body in server.requests:
        if path != "/v1/models":
            assert body["model"] == MODEL, path


async def test_the_model_check_reads_the_list_not_the_echo() -> None:
    """Found running llama.cpp: asked for `other/model`, a one-model server answered
    `other/model`, so a check against the response passed against any server."""
    server = FakeLlama("gpustack/bge-m3-GGUF:FP16")
    with pytest.raises(EmbedderMismatch, match="--alias"):
        await client(server).embed(["a"])
    assert "/v1/embeddings" not in server.paths(), "a mismatched server was asked to embed"


async def test_the_servers_own_name_for_the_model_can_be_given() -> None:
    """Ollama calls it `bge-m3`; the corpus still knows it as `BAAI/bge-m3`."""
    server = FakeLlama("bge-m3")
    vectors = await client(server, served_model="bge-m3").embed(["a"])
    assert len(vectors) == 1
    assert server.requests[-1][1]["model"] == "bge-m3"


async def test_the_list_is_read_once_not_per_search() -> None:
    """The API opens a client per search; the list is remembered across clients."""
    server = FakeLlama()
    for _ in range(3):
        await client(server).embed(["a"])
    assert server.paths().count("/v1/models") == 1


async def test_an_error_status_is_an_outage() -> None:
    server = FakeLlama()
    server.status["/v1/embeddings"] = 500
    with pytest.raises(EmbeddingUnavailable):
        await client(server).embed(["a"])


# --------------------------------------------------------------------------
# Reranking
# --------------------------------------------------------------------------


async def test_scores_are_returned_per_text_although_the_server_sorts_them() -> None:
    reranker = FakeLlama("bge-reranker")
    scores = await client(FakeLlama(), reranker).rerank("q", ["aa", "a", "aaa"])
    assert scores == [2.0, 1.0, 3.0]
    assert reranker.paths() == ["/v1/rerank"]


async def test_a_rerank_goes_to_the_reranker_not_the_embedder() -> None:
    embed, reranker = FakeLlama(), FakeLlama("bge-reranker")
    await client(embed, reranker).rerank("q", ["a", "b"])
    assert embed.requests == []


@pytest.mark.parametrize("status", [404, 501])
async def test_a_server_without_a_reranker_is_off_not_down(status) -> None:
    reranker = FakeLlama()
    reranker.status["/v1/rerank"] = status
    with pytest.raises(RerankingOff):
        await client(FakeLlama(), reranker).rerank("q", ["a", "b"])


async def test_a_failing_reranker_is_an_outage() -> None:
    reranker = FakeLlama()
    reranker.status["/v1/rerank"] = 500
    with pytest.raises(EmbeddingUnavailable):
        await client(FakeLlama(), reranker).rerank("q", ["a", "b"])


@pytest.mark.parametrize(
    "results",
    [
        [{"index": 0, "relevance_score": 1.0}],
        [{"index": 0, "relevance_score": 1.0}, {"index": 0, "relevance_score": 2.0}],
        [{"index": 0, "relevance_score": 1.0}, {"index": 1, "relevance_score": "high"}],
    ],
)
async def test_results_that_do_not_score_each_text_once_are_refused(results) -> None:
    reranker = FakeLlama()
    reranker.override["/v1/rerank"] = {"results": results}
    with pytest.raises(EmbeddingUnavailable):
        await client(FakeLlama(), reranker).rerank("q", ["a", "b"])


async def test_a_long_passage_is_cut_to_fit_beside_the_question() -> None:
    """llama.cpp refuses a pair longer than its batch rather than cutting it."""
    reranker = FakeLlama()
    limit = 30
    await client(FakeLlama(), reranker, rerank_max_tokens=limit).rerank("qq", ["y" * 100, "short"])
    detok = [body for path, body in reranker.requests if path == "/detokenize"]
    assert len(detok[0]["tokens"]) == limit - 2 - RERANK_PAIR_MARGIN
    documents = reranker.requests[-1][1]["documents"]
    assert len(documents[0]) == limit - 2 - RERANK_PAIR_MARGIN
    assert documents[1] == "short"


async def test_a_question_longer_than_the_reranker_reads_is_refused() -> None:
    with pytest.raises(ValueError, match="question alone"):
        await client(FakeLlama(), rerank_max_tokens=12).rerank("q" * 20, ["y" * 20])


# --------------------------------------------------------------------------
# Health, outages, configuration
# --------------------------------------------------------------------------


async def test_describe_reports_both_servers_in_the_sidecar_shape() -> None:
    described = await client(FakeLlama(), FakeLlama("bge-reranker")).describe()
    assert described == {
        "status": "ok",
        "model": MODEL,
        "loaded": True,
        "rerank_model": "bge-reranker",
        "rerank_loaded": True,
    }


async def test_describe_says_a_listed_model_is_not_yet_loaded() -> None:
    """A server that loads on request is up with the model unloaded: up, so the backfill
    sends the batch whose arrival loads it (ADR 0023)."""
    server = FakeLlama()
    server.override["/v1/models"] = {
        "data": [{"id": "other"}, {"id": MODEL, "status": {"value": "unloaded"}}]
    }
    described = await client(server).describe()
    assert described["model"] == MODEL and described["loaded"] is False


async def test_describe_is_none_while_the_model_loads() -> None:
    """llama.cpp answers 503 until the weights are in."""
    server = FakeLlama()
    server.status["/v1/models"] = 503
    assert await client(server).describe() is None


async def test_describe_without_a_reranker_says_so() -> None:
    reranker = FakeLlama()
    reranker.status["/v1/models"] = 503
    described = await client(FakeLlama(), reranker).describe()
    assert described["rerank_model"] is None and described["rerank_loaded"] is False


async def test_a_reranker_machine_that_is_off_does_not_stop_embedding() -> None:
    """The outage is remembered per server: two ports, two memories."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8102:
            raise httpx.ConnectError("no route to host", request=request)
        return FakeLlama()(request)

    embedder = RemoteEmbedder(
        EMBED,
        api="openai",
        rerank_url=RERANK,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(EmbeddingUnavailable):
        await embedder.rerank("q", ["a", "b"])
    with pytest.raises(EmbeddingUnavailable, match="not asked again"):
        await embedder.rerank("q", ["a", "b"])
    assert len(await embedder.embed(["a"])) == 1


async def test_the_token_goes_with_every_request_including_tokenize() -> None:
    """llama.cpp's `--api-key` guards every route; a missed one is a 401 that reads as down."""
    seen: list[tuple[str, str | None]] = []
    server = FakeLlama()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        return server(request)

    embedder = RemoteEmbedder(
        EMBED,
        api="openai",
        token="s3cret",
        max_tokens=4,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await embedder.embed(["abcdefgh"])
    await embedder.describe()
    assert {path for path, _ in seen} == {"/tokenize", "/v1/embeddings", "/v1/models"}
    assert {auth for _, auth in seen} == {"Bearer s3cret"}


def test_the_environment_chooses_the_api_and_the_reranker(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", EMBED)
    monkeypatch.delenv("MERIDIAN_EMBEDDER_API", raising=False)
    monkeypatch.delenv("MERIDIAN_RERANKER_URL", raising=False)
    default = RemoteEmbedder.from_env()
    assert default.api == "meridian" and default.rerank_url == EMBED

    monkeypatch.setenv("MERIDIAN_EMBEDDER_API", "OpenAI")
    monkeypatch.setenv("MERIDIAN_RERANKER_URL", RERANK + "/")
    monkeypatch.setenv("MERIDIAN_EMBED_MAX_TOKENS", "512")
    chosen = RemoteEmbedder.from_env()
    assert chosen.api == "openai" and chosen.rerank_url == RERANK
    assert chosen._max_tokens == 512


@pytest.mark.parametrize("value", ["llama", "ollama", "tei"])
def test_an_unknown_api_is_refused_rather_than_defaulted(monkeypatch, value) -> None:
    """The wrong API reads every answer as an outage, which looks like a machine that is off."""
    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", EMBED)
    monkeypatch.setenv("MERIDIAN_EMBEDDER_API", value)
    with pytest.raises(ValueError, match="MERIDIAN_EMBEDDER_API"):
        RemoteEmbedder.from_env()
    with pytest.raises(ValueError, match="unknown embedder API"):
        RemoteEmbedder(EMBED, api=value)  # type: ignore[arg-type]


def test_the_client_cuts_where_the_worker_and_the_sidecar_cut() -> None:
    """Drift: vectors made through llama.cpp must match the stored ones, and a cut at a
    different length makes different vectors for every long passage."""
    from worker.embeddings import DEFAULT_MAX_TOKENS as WORKER_MAX
    from worker.rerank import DEFAULT_MAX_TOKENS as RERANK_MAX

    assert DEFAULT_MAX_TOKENS == WORKER_MAX
    assert DEFAULT_RERANK_MAX_TOKENS == RERANK_MAX


# --------------------------------------------------------------------------
# A server that loads on request (ADR 0023)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [503, 429])
async def test_loading_is_waited_out_for_as_long_as_the_server_says(status) -> None:
    server = FakeLlama()
    calls = {"n": 0}
    real = server.__call__

    def loading(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/embeddings":
            calls["n"] += 1
            return httpx.Response(status, headers={"Retry-After": "45"})
        return real(request)

    embedder = RemoteEmbedder(
        EMBED,
        api="openai",
        expect_model=MODEL,
        client=httpx.AsyncClient(transport=httpx.MockTransport(loading)),
    )
    with pytest.raises(EmbeddingUnavailable, match="45 s"):
        await embedder.embed(["a"])
    with pytest.raises(EmbeddingUnavailable, match="not asked again"):
        await embedder.embed(["a"])
    assert calls["n"] == 1, "a loading server was asked again before its Retry-After"


async def test_describe_still_asks_a_loading_server_so_it_can_come_back() -> None:
    server = FakeLlama()
    server.status["/v1/embeddings"] = 503
    embedder = client(server)
    with pytest.raises(EmbeddingUnavailable):
        await embedder.embed(["a"])
    assert (await embedder.describe())["model"] == MODEL


def test_retry_after_reads_seconds_dates_and_milliseconds() -> None:
    from meridian_core.httpwait import MAX_WAIT_S, retry_after_s

    assert retry_after_s({"Retry-After": "12"}, 30) == 12
    assert retry_after_s({"retry-after-ms": "1500", "Retry-After": "12"}, 30) == 1.5
    assert retry_after_s({}, 30) == 30
    assert retry_after_s({"Retry-After": "soon"}, 30) == 30
    assert retry_after_s({"Retry-After": "-5"}, 30) == 0
    assert retry_after_s({"Retry-After": "nan"}, 30) == 30
    assert retry_after_s({"Retry-After": "99999"}, 30) == MAX_WAIT_S
    when = "Sun, 01 Nov 2026 00:01:00 GMT"
    now = 1793491200.0  # 2026-11-01T00:00:00Z
    assert retry_after_s({"Retry-After": when}, 30, now=now) == 60
