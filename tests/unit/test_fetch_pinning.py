"""The fetcher connects to the address it validated (task P1-24, spec §11.8).

`P1-20` put the guard in place: `netguard` resolves a hostname and refuses the
private answers. What it could not do from where it sat is stop the HTTP client
from resolving the same name a second time, a moment later, and connecting to
whatever it got then. That window is the whole of DNS rebinding, and these tests
exist to keep it shut.

The assertions are all on the request that would have reached the socket — the
URL's host, the ``Host`` header and the TLS SNI hostname — because that is the
only place where "which machine did we actually talk to" is visible.
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest
from http_doubles import streamed

from worker.fetch import Fetcher, authority, pinned_url

PUBLIC = "93.184.216.34"
# Not a TEST-NET range: 192.0.2/24, 198.51.100/24 and 203.0.113/24 are all
# classified non-global by `ipaddress`, so netguard rightly refuses them — which
# makes them useless as stand-ins for a second public host.
OTHER_PUBLIC = "104.18.32.7"


def ok(request: httpx.Request) -> httpx.Response:
    return streamed(200, headers={"content-type": "text/html"}, chunks=[b"<p>hello</p>"])


# --------------------------------------------------------------------------
# pinned_url / authority — the pure parts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,address,expected",
    [
        ("https://example.test/a?b=1", PUBLIC, f"https://{PUBLIC}/a?b=1"),
        ("https://example.test", PUBLIC, f"https://{PUBLIC}/"),
        ("https://example.test:8443/x", PUBLIC, f"https://{PUBLIC}:8443/x"),
        # An IPv6 literal must be bracketed or the port becomes part of the host.
        (
            "https://example.test:8443/x",
            "2606:2800:220:1::1",
            "https://[2606:2800:220:1::1]:8443/x",
        ),
        # Userinfo is a URL-confusion vector, and a fragment never goes on the wire.
        ("https://user:pw@example.test/p#frag", PUBLIC, f"https://{PUBLIC}/p"),
    ],
)
def test_pinned_url_rewrites_the_host_and_keeps_everything_else(
    url: str, address: str, expected: str
) -> None:
    assert pinned_url(url, ipaddress.ip_address(address)) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.test/a", "example.test"),
        ("https://example.test:8443/a", "example.test:8443"),
        ("https://user:pw@example.test/a", "example.test"),
        # A default port written out explicitly is still explicit; the origin
        # server is entitled to see the authority the client meant to address.
        ("https://example.test:443/a", "example.test:443"),
    ],
)
def test_authority_is_the_hostname_the_origin_expects(url: str, expected: str) -> None:
    assert authority(url) == expected


# --------------------------------------------------------------------------
# The pinning itself
# --------------------------------------------------------------------------


async def test_request_is_addressed_to_the_validated_ip_not_the_hostname(
    policy, resolver, recorder
) -> None:
    """The socket goes to the address netguard judged, not to a fresh lookup."""
    rec = recorder(ok)
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})
    ) as fetcher:
        result = await fetcher.fetch_static("https://example.test/page", policy())

    assert result.ok, result.detail
    assert rec.hosts == [PUBLIC], "the request should address the validated literal"
    assert rec.host_headers == ["example.test"], "the origin still needs the real Host"
    assert rec.sni_hostnames == ["example.test"], (
        "TLS must still be verified against the hostname, or pinning would "
        "trade an SSRF hole for a certificate-validation one"
    )


async def test_a_changed_second_dns_answer_cannot_move_the_connection(
    policy, resolver, recorder
) -> None:
    """DNS rebinding: the check sees a public address, the client would see 127.0.0.1.

    This is the exact scenario `P1-24` was filed for. With pinning, the second
    answer is never consulted — the request still goes to the address that was
    judged, and the number of lookups stays at one.
    """
    resolve = resolver(sequence={"rebind.test": [[PUBLIC], ["127.0.0.1"], ["127.0.0.1"]]})
    rec = recorder(ok)
    async with Fetcher(client=rec.client(), resolver=resolve) as fetcher:
        result = await fetcher.fetch_static("https://rebind.test/page", policy())

    assert result.ok
    assert rec.hosts == [PUBLIC]
    assert resolve.calls["rebind.test"] == 1, (
        "a second lookup is a second chance for the attacker to answer differently"
    )


async def test_redirect_hops_are_each_revalidated_and_repinned(policy, resolver, recorder) -> None:
    """Every hop is judged before it is connected to, not only the first URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "start.test":
            return streamed(302, headers={"location": "https://second.test/landing"})
        return ok(request)

    rec = recorder(handler)
    resolve = resolver({"start.test": [PUBLIC], "second.test": [OTHER_PUBLIC]})
    async with Fetcher(client=rec.client(), resolver=resolve) as fetcher:
        result = await fetcher.fetch_static("https://start.test/", policy())

    assert result.ok
    assert result.final_url == "https://second.test/landing"
    assert result.redirect_chain == ("https://second.test/landing",)
    assert rec.hosts == [PUBLIC, OTHER_PUBLIC], "each hop pinned to its own validated address"
    assert rec.host_headers == ["start.test", "second.test"]


async def test_a_redirect_into_private_space_is_refused_before_it_is_connected_to(
    policy, resolver, recorder
) -> None:
    """A 200 from a public host can 302 to the LAN. The second request never goes out."""

    def handler(request: httpx.Request) -> httpx.Response:
        return streamed(302, headers={"location": "http://internal.test/admin"})

    rec = recorder(handler)
    resolve = resolver({"start.test": [PUBLIC], "internal.test": ["10.0.0.5"]})
    async with Fetcher(client=rec.client(), resolver=resolve) as fetcher:
        result = await fetcher.fetch_static("https://start.test/", policy())

    assert result.outcome == "unsafe_target"
    assert "private address" in result.detail
    assert rec.hosts == [PUBLIC], "the refused hop must not have been requested"


async def test_the_client_is_not_allowed_to_follow_redirects_by_itself(
    policy, resolver, recorder
) -> None:
    """A client-followed redirect connects without handing the URL back for judgement.

    Asserted structurally rather than by outcome: if httpx ever followed the
    redirect internally, the transport would see the second request carrying the
    *hostname* — because httpx would have resolved it — rather than the pinned
    literal this fetcher put there.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "start.test":
            return streamed(301, headers={"location": "https://second.test/"})
        return ok(request)

    rec = recorder(handler)
    resolve = resolver({"start.test": [PUBLIC], "second.test": [OTHER_PUBLIC]})
    async with Fetcher(client=rec.client(), resolver=resolve) as fetcher:
        await fetcher.fetch_static("https://start.test/", policy())

    assert all(not r.url.host.endswith(".test") for r in rec.requests), (
        "a request addressed to a hostname means something other than this module resolved it"
    )


async def test_a_redirect_loop_stops_at_the_configured_hop_count(
    policy, resolver, recorder
) -> None:
    """An endless redirect must cost a bounded number of requests, not a hang."""
    rec = recorder(lambda r: streamed(302, headers={"location": "https://loop.test/next"}))
    async with Fetcher(client=rec.client(), resolver=resolver({"loop.test": [PUBLIC]})) as fetcher:
        result = await fetcher.fetch_static("https://loop.test/", policy(max_redirects=3))

    assert result.outcome == "too_many_redirects"
    assert len(rec.requests) == 4, "max_redirects hops plus the original request"


async def test_relative_redirects_resolve_against_the_hop_that_sent_them(
    policy, resolver, recorder
) -> None:
    """A bare `Location: /elsewhere` is legal and must not become a bad URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return streamed(302, headers={"location": "/landing"})
        return ok(request)

    rec = recorder(handler)
    async with Fetcher(client=rec.client(), resolver=resolver({"rel.test": [PUBLIC]})) as fetcher:
        result = await fetcher.fetch_static("https://rel.test/start", policy())

    assert result.ok
    assert result.final_url == "https://rel.test/landing"
    assert rec.hosts == [PUBLIC, PUBLIC]
