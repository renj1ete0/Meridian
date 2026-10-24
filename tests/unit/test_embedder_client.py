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
    SECONDS_PER_TEXT,
    EmbedderMismatch,
    EmbeddingUnavailable,
    RemoteEmbedder,
)
from meridian_core.models.source import EMBEDDING_DIM

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
    embedder = RemoteEmbedder("http://embedder.test", client=httpx.AsyncClient(transport=transport))

    assert len(await embedder.embed(["a"])) == 1


async def test_the_check_runs_on_every_response_not_once() -> None:
    # A sidecar can be restarted with a different model under a running client,
    # and the vectors it returns afterwards are valid floats of the right width.
    served = {"model": "bge-m3"}
    transport = httpx.MockTransport(lambda request: _embedding_response(served["model"], ["a"]))
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
    embedder = RemoteEmbedder("http://embedder.test", client=httpx.AsyncClient(transport=transport))

    assert (await embedder.describe())["model"] == "bge-m3"


async def test_describe_is_none_when_the_sidecar_is_down() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    embedder = RemoteEmbedder(
        "http://embedder.test", client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

    assert await embedder.describe() is None


# --------------------------------------------------------------------------
# A batch is not a query that got longer (task `B-27`)
# --------------------------------------------------------------------------
#
# The backfill sends `MERIDIAN_EMBED_CHUNK_BATCH` chunks — 256 — in one
# request, against a client whose timeout was fixed at 30 seconds. A real chunk
# takes the better part of half a second on CPU, so every batch call asked for
# two minutes of work and gave up at thirty seconds. The caller then fell back
# to loading the model in its own process, correctly and silently, and the only
# symptom was a second copy of 2.3 GB of weights on the machine the sidecar
# exists to spare.


def test_a_batch_waits_in_proportion_to_its_size() -> None:
    client = RemoteEmbedder(URL, timeout_s=30.0)

    assert client._batch_timeout(256) == 256 * SECONDS_PER_TEXT
    assert client._batch_timeout(256) > 30.0, (
        "a 256-chunk batch must not be held to a timeout sized for one query"
    )


def test_the_configured_timeout_is_a_floor_not_a_cap() -> None:
    """An operator who raised it for a slow box meant every call, including the
    single-text query path."""
    client = RemoteEmbedder(URL, timeout_s=120.0)

    assert client._batch_timeout(1) == 120.0
    assert client._batch_timeout(256) == 256 * SECONDS_PER_TEXT


async def test_a_real_batch_is_given_more_than_the_default_thirty_seconds() -> None:
    """The regression, end to end through the transport.

    Asserting on the timeout the request actually carries, because the failure
    was never in the arithmetic — it was that the arithmetic was never done.
    Thirty-two texts, so the computed wait clears the floor and the assertion
    is about the sizing rather than about the default.
    """
    texts = [f"passage {n}" for n in range(32)]
    seen: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout", {}).get("read"))
        return httpx.Response(
            200,
            json={
                "vectors": [[0.1] * EMBEDDING_DIM] * len(texts),
                "model": "BAAI/bge-m3",
                "dimensions": EMBEDDING_DIM,
            },
        )

    client = RemoteEmbedder(URL, timeout_s=30.0, client=responder(handler))
    await client.embed(texts)

    assert seen and seen[0] == len(texts) * SECONDS_PER_TEXT
    assert seen[0] > 30.0, "the batch was held to the query timeout"


async def test_the_client_sends_the_token_from_the_environment(monkeypatch) -> None:
    """P3-12: the other half of the sidecar's check; a mismatch here is a 401 on
    every batch, which reads as an embedder that is down."""
    import httpx

    from meridian_core.embedder import RemoteEmbedder

    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"vectors": [[0.0] * 4], "dimensions": 4, "model": "m"})

    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", "http://embedder.lan:8100")
    monkeypatch.setenv("MERIDIAN_EMBEDDER_TOKEN", "s3cret")
    client = RemoteEmbedder.from_env()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await client.embed(["a"])
    monkeypatch.delenv("MERIDIAN_EMBEDDER_TOKEN")
    bare = RemoteEmbedder.from_env()
    bare._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await bare.embed(["a"])

    assert seen == ["Bearer s3cret", None]


# --------------------------------------------------------------------------
# A machine that is switched off (`B-223`)
# --------------------------------------------------------------------------


def refusing(calls: list[str], exc: type[httpx.HTTPError] = httpx.ConnectError):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise exc("no route to host", request=request)

    return handler


@pytest.mark.parametrize(
    ("call", "read_s"),
    [
        (lambda e: e.embed(["a", "b"]), max(30.0, 2 * SECONDS_PER_TEXT)),
        (lambda e: e.rerank("q", ["a"]), 60.0),
        (lambda e: e.describe(), 5.0),
    ],
)
async def test_every_call_waits_briefly_for_a_connection_and_long_for_an_answer(
    call, read_s
) -> None:
    """One timeout for both made a search wait all 30 s on a host that never refuses."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={**vectors(2), "scores": [0.5]})

    embedder = RemoteEmbedder(URL, client=responder(handler), connect_timeout_s=3.0)
    await call(embedder)

    assert seen[0]["connect"] == 3.0
    assert seen[0]["read"] == read_s


async def test_a_refused_connection_is_remembered_across_clients() -> None:
    """The API opens a client per search, so the memory cannot live on the instance."""
    calls: list[str] = []
    first = RemoteEmbedder(URL, client=responder(refusing(calls)))
    with pytest.raises(EmbeddingUnavailable):
        await first.embed_one("q")

    second = RemoteEmbedder(URL, client=responder(refusing(calls)))
    with pytest.raises(EmbeddingUnavailable, match="not asked again"):
        await second.embed_one("q")
    with pytest.raises(EmbeddingUnavailable, match="not asked again"):
        await second.rerank("q", ["a"])

    assert calls == ["/embed"], "a remembered outage still went to the network"


async def test_an_outage_is_remembered_per_sidecar() -> None:
    calls: list[str] = []
    with pytest.raises(EmbeddingUnavailable):
        await RemoteEmbedder(URL, client=responder(refusing(calls))).embed_one("q")

    other = RemoteEmbedder(
        "http://other.test", client=responder(lambda r: httpx.Response(200, json=vectors(1)))
    )
    assert len(await other.embed_one("q")) == 4


@pytest.mark.parametrize(
    "answer",
    [
        lambda r: httpx.Response(500, json={}),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    ],
    ids=["error status", "slow answer"],
)
async def test_a_sidecar_that_answers_badly_is_not_remembered_as_down(answer) -> None:
    """A slow GPU or a 500 is a machine that is on; refusing the next search would hide it."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return answer(request)

    embedder = RemoteEmbedder(URL, client=responder(handler))
    for _ in range(2):
        with pytest.raises(EmbeddingUnavailable):
            await embedder.embed_one("q")
    assert calls == ["/embed", "/embed"]


async def test_the_health_probe_is_asked_while_down_and_brings_it_back() -> None:
    """The probe is how the backfill learns the machine is on again (`B-222`)."""
    calls: list[str] = []
    with pytest.raises(EmbeddingUnavailable):
        await RemoteEmbedder(URL, client=responder(refusing(calls))).embed_one("q")

    def up(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "model": "bge-m3"})
        return httpx.Response(200, json=vectors(1))

    back = RemoteEmbedder(URL, client=responder(up))
    assert await back.healthy() is True
    assert len(await back.embed_one("q")) == 4
    assert calls == ["/embed", "/health", "/embed"]


async def test_a_failed_health_probe_keeps_it_down() -> None:
    calls: list[str] = []
    embedder = RemoteEmbedder(URL, client=responder(refusing(calls, httpx.ConnectTimeout)))
    assert await embedder.healthy() is False
    with pytest.raises(EmbeddingUnavailable, match="not asked again"):
        await embedder.embed_one("q")
    assert calls == ["/health"]


async def test_the_memory_expires(monkeypatch) -> None:
    import meridian_core.embedder as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    calls: list[str] = []
    embedder = RemoteEmbedder(URL, client=responder(refusing(calls)), down_for_s=30.0)
    with pytest.raises(EmbeddingUnavailable):
        await embedder.embed_one("q")
    now[0] += 29.0
    with pytest.raises(EmbeddingUnavailable, match="not asked again for 1 s"):
        await embedder.embed_one("q")
    now[0] += 2.0
    with pytest.raises(EmbeddingUnavailable):
        await embedder.embed_one("q")
    assert calls == ["/embed", "/embed"]


async def test_a_zero_window_turns_the_memory_off() -> None:
    calls: list[str] = []
    embedder = RemoteEmbedder(URL, client=responder(refusing(calls)), down_for_s=0)
    for _ in range(2):
        with pytest.raises(EmbeddingUnavailable):
            await embedder.embed_one("q")
    assert calls == ["/embed", "/embed"]


def test_both_settings_are_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", URL)
    monkeypatch.setenv("MERIDIAN_EMBEDDER_CONNECT_TIMEOUT_S", "1.5")
    monkeypatch.setenv("MERIDIAN_EMBEDDER_DOWN_FOR_S", "90")
    embedder = RemoteEmbedder.from_env()
    assert embedder._connect_timeout == 1.5
    assert embedder._down_for == 90.0


async def test_a_connect_timeout_above_the_read_timeout_is_capped_by_it() -> None:
    """httpx would otherwise wait longer to connect than the caller allowed in all."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"status": "ok"})

    await RemoteEmbedder(URL, client=responder(handler), connect_timeout_s=60.0).describe()
    assert seen[0]["connect"] == 5.0
