"""The search backend client (task P1-34, spec §6.4).

§6.4 is unusually specific about the failure model — SearXNG scrapes upstream
engines, engines break and rate-limit routinely, and *a dead engine must never
stall the queue* — so most of this file is about the difference between the
three ways a search can go wrong, because they settle the queue row three
different ways:

- an engine is down          → routine; the other engines' results are the answer
- every engine returned none → answered, with nothing; the query is done
- SearXNG itself is down     → transient and local; the query retries

The transport is `httpx.MockTransport`, so request construction, parameters and
error semantics all run the production path. Only the socket is replaced.
"""

from __future__ import annotations

import httpx
import pytest

from worker.search import (
    DEFAULT_MAX_RESULTS,
    SearchError,
    SearchResults,
    SearxClient,
)

BASE = "http://searxng.internal:8080"


def client_for(handler, **kwargs) -> SearxClient:
    transport = httpx.MockTransport(handler)
    return SearxClient(BASE, client=httpx.AsyncClient(transport=transport), **kwargs)


def json_response(payload, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def results_for(*urls: str) -> dict:
    return {"results": [{"url": url, "title": "t"} for url in urls]}


# --------------------------------------------------------------------------
# Asking the question
# --------------------------------------------------------------------------


async def test_the_query_goes_out_as_json(monkeypatch) -> None:
    """`format=json` is not the default, and a deployment that never enabled it
    answers 200 with HTML — which must not read as "nothing matched"."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=results_for("https://example.test/a"))

    await client_for(handler).search("walkability thermal comfort")

    assert seen["params"] == {"q": "walkability thermal comfort", "format": "json"}
    assert str(seen["url"]).startswith(f"{BASE}/search")


async def test_results_come_back_in_rank_order() -> None:
    """SearXNG has already fused several engines' rankings; reordering here or
    truncating from the wrong end would throw that fusion away."""
    payload = results_for("https://a.test/1", "https://b.test/2", "https://c.test/3")

    results = await client_for(json_response(payload)).search("q")

    assert results.urls == ("https://a.test/1", "https://b.test/2", "https://c.test/3")


async def test_an_empty_query_is_refused_before_the_request() -> None:
    """A blank `url_or_query` would otherwise ask the backend for everything."""
    with pytest.raises(SearchError, match="empty query"):
        await client_for(json_response(results_for())).search("   ")


# --------------------------------------------------------------------------
# What comes back, and what is dropped from it
# --------------------------------------------------------------------------


async def test_the_same_page_from_two_engines_is_one_candidate() -> None:
    payload = results_for("https://a.test/1", "https://a.test/1", "https://b.test/2")

    results = await client_for(json_response(payload)).search("q")

    assert results.urls == ("https://a.test/1", "https://b.test/2")
    assert results.dropped == {"duplicate": 1}


async def test_a_non_http_result_is_dropped() -> None:
    """`javascript:` and `magnet:` do turn up in scraped result sets."""
    payload = {
        "results": [
            {"url": "magnet:?xt=urn:btih:deadbeef"},
            {"url": "javascript:void(0)"},
            {"url": "https://good.test/a"},
        ]
    }

    results = await client_for(json_response(payload)).search("q")

    assert results.urls == ("https://good.test/a",)
    assert results.dropped == {"bad_scheme": 2}


async def test_a_result_with_no_url_is_dropped_rather_than_crashing() -> None:
    """An upstream engine's answer is not a schema. A result object missing the
    one field that matters must not take the whole query down with it."""
    payload = {"results": [{"title": "no url here"}, {"url": ""}, {"url": "https://ok.test/a"}]}

    results = await client_for(json_response(payload)).search("q")

    assert results.urls == ("https://ok.test/a",)
    assert results.dropped == {"no_url": 2}


async def test_one_query_cannot_flood_the_frontier() -> None:
    """A bound on what a single queue row may produce, so one seed query cannot
    push a thousand rows in ahead of everything already waiting."""
    payload = results_for(*[f"https://a.test/{i}" for i in range(20)])

    results = await client_for(json_response(payload), max_results=5).search("q")

    assert len(results.urls) == 5
    assert results.dropped == {"over_max_results": 15}
    assert DEFAULT_MAX_RESULTS > 5, "the default must not be the test's bound"


# --------------------------------------------------------------------------
# §6.4's failure model
# --------------------------------------------------------------------------


async def test_a_dead_engine_does_not_lose_the_engines_that_answered() -> None:
    """The routine case. §6.4: configure several engines and treat failure as
    normal — a query answered by three of four is answered."""
    payload = {
        "results": [{"url": "https://a.test/1"}],
        "unresponsive_engines": [["bing", "timeout"], ["google scholar", "CAPTCHA"]],
    }

    results = await client_for(json_response(payload)).search("q")

    assert results.urls == ("https://a.test/1",)
    assert set(results.unresponsive) == {"bing", "google scholar"}


async def test_unresponsive_engines_reported_as_bare_strings_also_parse() -> None:
    """SearXNG has used both shapes across versions, and this is a log label —
    a format change must not raise in the middle of a good result set."""
    payload = {"results": [{"url": "https://a.test/1"}], "unresponsive_engines": ["duckduckgo"]}

    results = await client_for(json_response(payload)).search("q")

    assert results.unresponsive == ("duckduckgo",)


async def test_nothing_found_is_an_answer_not_an_error() -> None:
    """The distinction the queue depends on: a query answered with nothing is
    `done`, and one whose backend was unreachable is `retry`. Collapsing them
    means either retrying forever or abandoning on the first outage."""
    results = await client_for(json_response({"results": []})).search("q")

    assert isinstance(results, SearchResults)
    assert results.empty and results.urls == ()


async def test_an_unreachable_backend_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SearchError, match="ConnectError"):
        await client_for(handler).search("q")


async def test_a_5xx_from_the_backend_raises() -> None:
    with pytest.raises(SearchError, match="502"):
        await client_for(json_response({}, status=502)).search("q")


async def test_html_instead_of_json_is_a_misconfiguration_not_an_empty_result() -> None:
    """`formats: [json]` is a `settings.yml` option. A deployment that never set
    it answers 200 with a search page, and reporting that as "no results" would
    quietly disable frontier widening on a stack that looked healthy."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>results</body></html>")

    with pytest.raises(SearchError, match="did not return JSON"):
        await client_for(handler).search("q")


async def test_a_json_array_is_refused() -> None:
    """Valid JSON of the wrong shape. `.get()` on a list raises an
    AttributeError far from the cause."""
    with pytest.raises(SearchError, match="expected an object"):
        await client_for(json_response([1, 2, 3])).search("q")


# --------------------------------------------------------------------------
# Health (§12.5)
# --------------------------------------------------------------------------


async def test_a_reachable_backend_is_healthy() -> None:
    assert await client_for(json_response({"status": "ok"})).healthy() is True


async def test_a_404_on_healthz_still_counts_as_up() -> None:
    """`/healthz` is disabled in some deployments. A response of any kind means
    the service is answering, which is the question being asked."""
    assert await client_for(json_response({}, status=404)).healthy() is True


async def test_an_unreachable_backend_is_not_healthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert await client_for(handler).healthy() is False


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_no_url_means_no_client(monkeypatch) -> None:
    """A worker with no search backend is degraded, not broken — it crawls
    everything it already has and simply cannot widen the frontier."""
    monkeypatch.delenv("SEARXNG_URL", raising=False)

    assert SearxClient.from_env() is None


def test_the_client_is_built_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080/")
    monkeypatch.setenv("MERIDIAN_SEARCH_TIMEOUT_S", "12.5")
    monkeypatch.setenv("MERIDIAN_SEARCH_MAX_RESULTS", "7")

    client = SearxClient.from_env()

    assert client is not None
    assert (client.base_url, client.timeout_s, client.max_results) == (
        "http://searxng:8080",
        12.5,
        7,
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("MERIDIAN_SEARCH_TIMEOUT_S", "soon"),
        ("MERIDIAN_SEARCH_TIMEOUT_S", "-1"),
        ("MERIDIAN_SEARCH_MAX_RESULTS", "all"),
        ("MERIDIAN_SEARCH_MAX_RESULTS", "0"),
    ],
)
def test_a_nonsense_setting_fails_loudly(monkeypatch, name: str, value: str) -> None:
    """Rather than falling back to the default and running a differently
    configured backend than the operator asked for."""
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        SearxClient.from_env()
