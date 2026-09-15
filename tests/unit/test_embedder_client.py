"""The remote embedder (task P2-17, spec §4, §12.5).

No model and no network: an httpx mock transport stands in for the sidecar.

The subject is what happens when the sidecar is *not* perfect, because the
perfect case is one HTTP call. An embedder that returns the wrong number of
vectors is the failure worth most of this file — every value it returns is a
valid float, every vector is the right length, and nothing downstream can tell
that each one is attached to the wrong text.
"""

from __future__ import annotations

import httpx
import pytest

from meridian_core.embedder import (
    MAX_TEXTS,
    EmbedderMismatch,
    EmbeddingUnavailable,
    RemoteEmbedder,
)

URL = "http://embedder.test"


def responder(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def vectors(n: int, dims: int = 4) -> dict:
    return {"vectors": [[0.1] * dims for _ in range(n)], "dimensions": dims, "model": "bge-m3"}


# --------------------------------------------------------------------------
# Absent is a supported state
# --------------------------------------------------------------------------


def test_no_url_means_no_embedder_rather_than_an_error(monkeypatch) -> None:
    """The same shape as `Crawl4aiClient.from_env()`: a deployment without one
    is degraded, not broken, and must still answer lexical searches."""
    monkeypatch.delenv("MERIDIAN_EMBEDDER_URL", raising=False)
    assert RemoteEmbedder.from_env() is None


def test_a_configured_url_gives_a_client(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", URL)
    assert RemoteEmbedder.from_env() is not None


# --------------------------------------------------------------------------
# The alignment check
# --------------------------------------------------------------------------


async def test_fewer_vectors_than_texts_is_refused() -> None:
    """The failure this client exists to catch.

    Paired silently, each vector would attach to the wrong text — and every
    number involved is a valid float, so nothing downstream can detect it. A
    chunk would be searchable under someone else's meaning, forever, with no
    symptom but bad results.
    """
    client = responder(lambda request: httpx.Response(200, json=vectors(2)))
    embedder = RemoteEmbedder(URL, client=client)

    with pytest.raises(EmbeddingUnavailable, match="asked for 3"):
        await embedder.embed(["a", "b", "c"])


async def test_a_response_with_no_vectors_at_all_is_refused() -> None:
    client = responder(lambda request: httpx.Response(200, json={"dimensions": 4}))

    with pytest.raises(EmbeddingUnavailable):
        await RemoteEmbedder(URL, client=client).embed(["a"])


async def test_matching_counts_are_returned_in_order() -> None:
    client = responder(lambda request: httpx.Response(200, json=vectors(3)))

    result = await RemoteEmbedder(URL, client=client).embed(["a", "b", "c"])

    assert len(result) == 3


# --------------------------------------------------------------------------
# Unavailable is not absent
# --------------------------------------------------------------------------


async def test_a_refusing_sidecar_raises_rather_than_returning_nothing() -> None:
    """`EmbeddingUnavailable`, not None. The caller decides whether to degrade;
    a client that silently returned no vectors would make an outage
    indistinguishable from a deployment that never had an embedder."""
    client = responder(lambda request: httpx.Response(503, json={"detail": "loading"}))

    with pytest.raises(EmbeddingUnavailable):
        await RemoteEmbedder(URL, client=client).embed(["a"])


async def test_an_unreachable_sidecar_raises() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EmbeddingUnavailable):
        await RemoteEmbedder(URL, client=responder(refuse)).embed(["a"])


async def test_an_unreadable_response_raises() -> None:
    client = responder(lambda request: httpx.Response(200, content=b"not json"))

    with pytest.raises(EmbeddingUnavailable):
        await RemoteEmbedder(URL, client=client).embed(["a"])


async def test_health_reports_false_rather_than_raising() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await RemoteEmbedder(URL, client=responder(refuse)).healthy() is False


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------


async def test_an_oversized_batch_is_refused_before_the_request() -> None:
    """A caller cannot ask one HTTP request to hold a corpus. Refused here
    rather than by the server, so a backfill finds out at the call site."""
    client = responder(lambda request: httpx.Response(200, json=vectors(1)))

    with pytest.raises(ValueError, match="cap"):
        await RemoteEmbedder(URL, client=client).embed(["x"] * (MAX_TEXTS + 1))


async def test_no_texts_is_no_request() -> None:
    """An empty batch is not an error and must not cost a round trip."""

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an empty batch should not have called the sidecar")

    assert await RemoteEmbedder(URL, client=responder(explode)).embed([]) == []


async def test_embed_one_returns_a_single_vector() -> None:
    client = responder(lambda request: httpx.Response(200, json=vectors(1)))

    vector = await RemoteEmbedder(URL, client=client).embed_one("a query")

    assert len(vector) == 4


# --------------------------------------------------------------------------
# A sidecar serving a different model (task P2-19)
# --------------------------------------------------------------------------
#
# The one failure in this area that cannot be detected afterwards. `<=>` accepts
# any two vectors of the right width and returns a number, so a column holding
# two models' vectors ranks confident nonsense and nothing downstream — not the
# search, not the novelty gate, not a reader — can tell.


def _embedding_response(model: str, texts: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "vectors": [[0.1] * 4 for _ in texts],
            "dimensions": 4,
            "model": model,
        },
    )


async def test_a_sidecar_serving_another_model_is_refused() -> None:
    transport = httpx.MockTransport(lambda request: _embedding_response("other-model", ["a"]))
    embedder = RemoteEmbedder(
        "http://embedder.test",
        client=httpx.AsyncClient(transport=transport),
        expect_model="bge-m3",
    )

    with pytest.raises(EmbedderMismatch, match="other-model"):
        await embedder.embed(["a"])


async def test_the_matching_model_is_accepted() -> None:
    # The converse, without which the test above would pass against a client
    # that refused everything.
    transport = httpx.MockTransport(lambda request: _embedding_response("bge-m3", ["a"]))
    embedder = RemoteEmbedder(
        "http://embedder.test",
        client=httpx.AsyncClient(transport=transport),
        expect_model="bge-m3",
    )

    assert len(await embedder.embed(["a"])) == 1


async def test_no_expectation_means_no_check() -> None:
    # A deployment that has not said which model it uses gets the previous
    # behaviour rather than a refusal it cannot act on.
    transport = httpx.MockTransport(lambda request: _embedding_response("anything", ["a"]))
    embedder = RemoteEmbedder(
        "http://embedder.test", client=httpx.AsyncClient(transport=transport)
    )

    assert len(await embedder.embed(["a"])) == 1


async def test_the_check_runs_on_every_response_not_once() -> None:
    # A sidecar can be restarted with a different model under a running client,
    # and the vectors it returns afterwards are valid floats of the right width.
    served = {"model": "bge-m3"}
    transport = httpx.MockTransport(
        lambda request: _embedding_response(served["model"], ["a"])
    )
    embedder = RemoteEmbedder(
        "http://embedder.test",
        client=httpx.AsyncClient(transport=transport),
        expect_model="bge-m3",
    )
    await embedder.embed(["a"])

    served["model"] = "something-else"

    with pytest.raises(EmbedderMismatch):
        await embedder.embed(["a"])


async def test_describe_reports_what_the_sidecar_says_it_is() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"model": "bge-m3", "loaded": True})
    )
    embedder = RemoteEmbedder(
        "http://embedder.test", client=httpx.AsyncClient(transport=transport)
    )

    assert (await embedder.describe())["model"] == "bge-m3"


async def test_describe_is_none_when_the_sidecar_is_down() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    embedder = RemoteEmbedder(
        "http://embedder.test", client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

    assert await embedder.describe() is None
