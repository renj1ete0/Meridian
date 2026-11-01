"""GDELT as a second source for news queries (task B-267).

GDELT answers status 200 whatever happened, so the tests are about telling its answers apart:
articles, none, a refusal to answer this fast (throttled: retried), and a query it cannot run
(an empty answer). And about the merge: one backend failing must not fail a query the other
answered.
"""

from __future__ import annotations

import json

import httpx
import pytest

from worker.search import (
    GDELT_MAX_RESULTS,
    GdeltClient,
    NewsSearch,
    SearchError,
    SearchResults,
    gdelt_params,
)


def test_only_a_news_query_goes_to_gdelt_with_its_language() -> None:
    assert gdelt_params("walkability study") is None
    assert gdelt_params("!news") is None
    plain = gdelt_params("!news bus lanes")
    assert plain["query"] == "bus lanes" and plain["timespan"] == "1w" and plain["format"] == "json"
    assert gdelt_params("!news :de Busspur")["query"] == "Busspur sourcelang:german"
    assert gdelt_params("!news :xx words")["query"] == "words", "an unknown language is dropped"


def client(body: str, status: int = 200) -> GdeltClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return GdeltClient(
        min_interval_s=0, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_articles_come_back_as_urls_in_order_without_repeats() -> None:
    body = json.dumps(
        {
            "articles": [
                {"url": "https://news.example.test/a"},
                {"url": "https://news.example.test/a"},
                {"url": "javascript:alert(1)"},
                {"url": "https://wire.example.test/b"},
            ]
        }
    )
    found = await client(body).search("!news bus lanes")
    assert found.urls == ("https://news.example.test/a", "https://wire.example.test/b")


async def test_no_articles_is_an_answer_not_a_throttle() -> None:
    found = await client("{}").search("!news bus lanes")
    assert found.empty and not found.throttled


async def test_a_refusal_to_answer_this_fast_is_throttled_and_retried() -> None:
    found = await client("Please limit requests to one every 5 seconds or contact …").search(
        "!news bus lanes"
    )
    assert found.throttled


async def test_a_query_gdelt_cannot_run_is_an_empty_answer() -> None:
    found = await client("Your search contained a phrase that was too short.").search("!news a")
    assert found.empty and not found.throttled
    assert found.dropped == {"gdelt_refused_query": 1}


async def test_an_error_status_is_a_backend_failure() -> None:
    with pytest.raises(SearchError, match="gdelt returned 502"):
        await client("bad gateway", 502).search("!news bus lanes")


class Fixed:
    def __init__(self, answer):
        self.answer = answer
        self.asked: list[str] = []

    async def search(self, query: str):
        self.asked.append(query)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    async def healthy(self, timeout_s: float = 5.0) -> bool:
        return True

    async def aclose(self) -> None:
        return None


async def test_news_answers_are_interleaved_and_others_ask_searxng_alone() -> None:
    searx = Fixed(SearchResults(query="q", urls=("https://a.test/1", "https://a.test/2")))
    gdelt = Fixed(SearchResults(query="q", urls=("https://b.test/1", "https://a.test/1")))
    news = NewsSearch(searx, gdelt)

    merged = await news.search("!news bus lanes")
    assert merged.urls == ("https://a.test/1", "https://b.test/1", "https://a.test/2")

    await news.search("bus lanes study")
    assert gdelt.asked == ["!news bus lanes"], "a non-news query never reaches GDELT"


async def test_one_backend_failing_does_not_fail_the_query() -> None:
    searx = Fixed(SearchError("searxng returned 500"))
    gdelt = Fixed(SearchResults(query="q", urls=("https://b.test/1",)))
    found = await NewsSearch(searx, gdelt).search("!news bus lanes")
    assert found.urls == ("https://b.test/1",)
    assert any("500" in reason for reason in found.unresponsive)


async def test_both_failing_is_a_backend_failure() -> None:
    news = NewsSearch(Fixed(SearchError("searx down")), Fixed(SearchError("gdelt down")))
    with pytest.raises(SearchError, match="searx down"):
        await news.search("!news bus lanes")


def test_gdelt_is_on_unless_switched_off(monkeypatch) -> None:
    monkeypatch.delenv("MERIDIAN_GDELT", raising=False)
    assert GdeltClient.from_env() is not None
    monkeypatch.setenv("MERIDIAN_GDELT", "false")
    assert GdeltClient.from_env() is None
    assert GDELT_MAX_RESULTS <= 250, "GDELT's own cap on maxrecords"
