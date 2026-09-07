"""Reporting whether the browser is there (`P1-26`, §12.5).

The fetcher degrades to static when the browser is missing. That is correct —
most of the corpus needs no rendering — and it is silent, which is the problem:
a worker that lost its browser a week ago looks exactly like one that never had
one, and the only symptom is that JS-dependent pages quietly extract worse.

So the health line has to distinguish three states, and the tests below are
mostly about the distinction rather than the probe.
"""

from __future__ import annotations

import httpx
import pytest

from worker.fetch import Crawl4aiClient


def client(handler, token: str | None = "t") -> Crawl4aiClient:
    return Crawl4aiClient(
        "http://browser.test:11235",
        token,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_a_200_means_healthy() -> None:
    c = client(lambda r: httpx.Response(200, json={"status": "ok"}))
    assert await c.healthy() is True


@pytest.mark.parametrize("status", [401, 403, 404, 500, 502, 503])
async def test_any_non_200_is_unhealthy(status: int) -> None:
    """Including 401. 0.9.2 leaves `/health` unauthenticated, but a browser we
    cannot authenticate to is one we cannot use, and a version that does guard
    it should read as unhealthy rather than as fine."""
    c = client(lambda r: httpx.Response(status))
    assert await c.healthy() is False


async def test_a_connection_error_is_unhealthy_not_an_exception() -> None:
    """The probe runs inside the housekeeping tick.

    An unreachable browser is the condition being reported, not a failure to
    report it — a probe that raises would take down the pass that prunes the
    attempt log and logs the health line.
    """
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert await client(boom).healthy() is False


async def test_a_timeout_is_unhealthy_not_an_exception() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    assert await client(slow).healthy() is False


async def test_the_token_is_sent() -> None:
    """Sent even though 0.9.2's `/health` does not require it.

    Verified against the running image: `/health` answers 200 with a wrong
    token or none, while `/schema` and `/crawl` refuse. Sending it anyway costs
    nothing and means the probe does not silently start failing if a future
    version puts `/health` behind the same guard as everything else.
    """
    seen: dict[str, str] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    await client(capture).healthy()
    assert seen.get("authorization") == "Bearer t"


async def test_no_token_sends_no_authorization_header() -> None:
    seen: dict[str, str] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    await client(capture, token=None).healthy()
    assert "authorization" not in seen


async def test_the_probe_hits_the_health_endpoint() -> None:
    seen: list[str] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    await client(capture).healthy()
    assert seen == ["http://browser.test:11235/health"]
