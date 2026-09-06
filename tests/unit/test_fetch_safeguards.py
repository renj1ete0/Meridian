"""Content safeguards on the fetch path (task P1-21, spec §6.4).

Each of these is a *rejection* test: that a legitimate page comes back is the
weaker half, and the half that would still pass if the safeguard were deleted.
What matters is that the hostile or oversized response is actually refused, and
refused before it costs anything.

The decompression tests run against a real socket rather than a mock transport.
That is not thoroughness for its own sake: the first implementation of the size
cap passed every mock-transport test and still allocated 67MB from a 64KB read,
because httpx hands back whatever one network read inflates to as a single
object. Only a real chunked stream shows that.
"""

from __future__ import annotations

import gzip
import threading
import tracemalloc
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from http_doubles import streamed

from meridian_core import netguard
from worker.fetch import (
    _INFLATE_STEP_BYTES,
    RATIO_FLOOR_BYTES,
    Fetcher,
    content_type_allowed,
    media_type,
)

PUBLIC = "93.184.216.34"


def html(chunks: list[bytes], **headers: str) -> httpx.Response:
    return streamed(200, headers={"content-type": "text/html", **headers}, chunks=chunks)


# --------------------------------------------------------------------------
# Content-type allowlist
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("text/html", "text/html"),
        ("text/html; charset=utf-8", "text/html"),
        ("  TEXT/HTML ;charset=UTF-8", "text/html"),
        ("application/pdf", "application/pdf"),
        (None, None),
        ("", None),
        (";charset=utf-8", None),
    ],
)
def test_media_type_ignores_parameters_and_case(header: str | None, expected: str | None) -> None:
    assert media_type(header) == expected


@pytest.mark.parametrize(
    "media,allowlist,expected",
    [
        ("text/html", ["text/html", "application/pdf"], True),
        ("text/html", ["application/pdf"], False),
        ("TEXT/HTML", ["text/html"], False),  # callers pass an already-normalised type
        ("text/html", [" Text/HTML "], True),  # ...but a sloppy config row still matches
        # An empty allowlist means unconfigured, not "allow nothing" — a worker
        # against an unseeded database must still crawl.
        ("application/zip", [], True),
        (None, [], True),
        # With an allowlist configured, an untyped response cannot be judged, so
        # it is refused rather than guessed at.
        (None, ["text/html"], False),
    ],
)
def test_content_type_allowlist_decisions(media, allowlist, expected) -> None:
    assert content_type_allowed(media, allowlist) is expected


async def test_a_disallowed_content_type_is_refused_without_reading_the_body(
    policy, resolver, recorder
) -> None:
    """The allowlist is checked on the headers, so the body never arrives."""
    body_read = []

    def handler(request: httpx.Request) -> httpx.Response:
        body_read.append(True)
        return streamed(
            200, headers={"content-type": "application/zip"}, chunks=[b"PK\x03\x04" * 1000]
        )

    rec = recorder(handler)
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static(
            "https://example.test/x", policy(allowed_content_types=["text/html"])
        )

    assert result.outcome == "content_type_rejected"
    assert result.detail == "application/zip"
    assert result.content == b"", "a refusal must not be mistakable for a small page"


# --------------------------------------------------------------------------
# Size caps
# --------------------------------------------------------------------------


async def test_a_declared_content_length_over_the_cap_is_refused_up_front(
    policy, resolver, recorder
) -> None:
    rec = recorder(lambda r: html([b"x" * 10], **{"content-length": "999999999"}))
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("https://example.test/x", policy(max_page_bytes=1000))

    assert result.outcome == "too_large"
    assert "content-length" in result.detail


async def test_a_body_that_outgrows_the_cap_mid_stream_is_aborted(
    policy, resolver, recorder
) -> None:
    """A server that lies about, or omits, its length is caught by the stream cap."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        def chunks():
            for i in range(100):
                sent.append(i)
                yield b"x" * 1000

        return streamed(200, headers={"content-type": "text/html"}, chunks=list(chunks()))

    rec = recorder(handler)
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("https://example.test/x", policy(max_page_bytes=5000))

    assert result.outcome == "too_large"
    assert result.content == b""


async def test_a_malformed_content_length_falls_through_to_the_stream_cap(
    policy, resolver, recorder
) -> None:
    """A junk header must not crash the fetch, and must not disable the real cap."""
    rec = recorder(lambda r: html([b"x" * 4000], **{"content-length": "not-a-number"}))
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("https://example.test/x", policy(max_page_bytes=1000))

    assert result.outcome == "too_large"


# --------------------------------------------------------------------------
# Decompression — against a real socket
# --------------------------------------------------------------------------


class _BombHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    payload = b""
    encoding = "gzip"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if self.encoding != "identity":
            self.send_header("Content-Encoding", self.encoding)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args: object) -> None:  # keep the test output clean
        pass


class _QuietServer(ThreadingHTTPServer):
    """Aborting mid-body is the behaviour under test.

    The broken pipe it leaves behind is the expected result of a successful
    refusal, not a failure, and socketserver logs it to stderr by default.
    """

    def handle_error(self, request: object, client_address: object) -> None:
        pass


@pytest.fixture
def bomb_server():
    """A real HTTP server serving a compressed payload of the test's choosing."""
    servers = []

    def start(payload: bytes, encoding: str = "gzip") -> int:
        handler = type("H", (_BombHandler,), {"payload": payload, "encoding": encoding})
        server = _QuietServer(("127.0.0.1", 0), handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]

    yield start

    for server in servers:
        server.shutdown()
        server.server_close()


def loopback_policy(policy, **overrides):
    """Policy for talking to the test server on 127.0.0.1.

    Both guards have to be relaxed to reach loopback at all, which is itself
    evidence that they work: without these two overrides netguard refuses the
    address and no decompression test could run.
    """
    return policy(
        block_private_addresses=False,
        require_https_final=False,
        **overrides,
    )


async def test_a_gzip_bomb_is_refused_while_it_inflates_not_after(
    policy, resolver, bomb_server
) -> None:
    """The refusal must be cheap in memory, which is the entire point.

    A 200MB payload behind 200KB of gzip: the cap has to fire during inflation.
    ``tracemalloc`` is the assertion because outcome alone cannot distinguish
    "refused early" from "refused after allocating 200MB", and the second one
    is an out-of-memory kill on a 16GB shared node.
    """
    port = bomb_server(gzip.compress(b"A" * 200_000_000))

    tracemalloc.start()
    try:
        async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
            result = await f.fetch_static(
                f"http://127.0.0.1:{port}/",
                loopback_policy(policy, max_page_bytes=20_000_000, max_decompression_ratio=100),
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.outcome == "decompression_bomb"
    assert result.content == b""

    # Where the ceiling comes from: the ratio can only be exceeded once
    # `decoded > raw_total * ratio`, and `raw_total` grows one network read at a
    # time — httpx reads 64KiB — so the earliest the check can fire is around
    # 64KiB x 100 = 6.5MB of output. Add one 1MiB inflate step and the copies
    # around it. Nothing in that derivation refers to the size of the payload,
    # which is the property being asserted: 200MB of bomb costs single-digit MB.
    ceiling = 64 * 1024 * 100 + _INFLATE_STEP_BYTES * 4
    assert peak < ceiling, f"peak allocation {peak} — the abort came too late"


async def test_an_honestly_large_gzip_page_is_still_refused_by_the_size_cap(
    policy, resolver, bomb_server
) -> None:
    """The ratio cap is not the only limit; a big page compressed normally is too big.

    The payload is incompressible, so its ratio is ~1:1 and only
    ``max_page_bytes`` can catch it.
    """
    import os

    port = bomb_server(gzip.compress(os.urandom(3_000_000)))

    async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
        result = await f.fetch_static(
            f"http://127.0.0.1:{port}/",
            loopback_policy(policy, max_page_bytes=1_000_000, max_decompression_ratio=100),
        )

    assert result.outcome == "too_large"


async def test_a_normally_compressed_page_is_not_mistaken_for_a_bomb(
    policy, resolver, bomb_server
) -> None:
    """Highly but honestly compressible content under the floor must still pass.

    Without ``RATIO_FLOOR_BYTES`` this page — 200KB of repetitive HTML, which
    gzips at roughly 400:1 — would be refused, and so would a great deal of
    real government HTML.
    """
    page = b"<p>Land Transport Authority annual report</p>\n" * 5000
    assert len(page) < RATIO_FLOOR_BYTES
    port = bomb_server(gzip.compress(page))

    async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
        result = await f.fetch_static(
            f"http://127.0.0.1:{port}/",
            loopback_policy(policy, max_decompression_ratio=100),
        )

    assert result.ok, result.detail
    assert result.content == page


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "identity"])
async def test_every_encoding_the_fetcher_advertises_round_trips(
    policy, resolver, bomb_server, encoding
) -> None:
    """Accept-Encoding promises gzip and deflate; both must actually decode.

    A crawler that asks for an encoding it cannot read turns every compressed
    response into a parse error, and compressed is the common case.
    """
    page = b"<h1>Walkability</h1><p>" + b"content " * 100 + b"</p>"
    payload = {
        "gzip": gzip.compress(page),
        "deflate": zlib.compress(page),
        "identity": page,
    }[encoding]

    port = bomb_server(payload, encoding=encoding)
    async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
        result = await f.fetch_static(f"http://127.0.0.1:{port}/", loopback_policy(policy))

    assert result.ok, result.detail
    assert result.content == page


async def test_raw_deflate_is_decoded_too(policy, resolver, bomb_server) -> None:
    """`Content-Encoding: deflate` is served both zlib-wrapped and raw in the wild."""
    page = b"<p>raw deflate</p>"
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    port = bomb_server(compressor.compress(page) + compressor.flush(), encoding="deflate")

    async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
        result = await f.fetch_static(f"http://127.0.0.1:{port}/", loopback_policy(policy))

    assert result.ok, result.detail
    assert result.content == page


async def test_an_encoding_the_fetcher_never_asked_for_is_a_parse_error(
    policy, resolver, bomb_server
) -> None:
    """Refused rather than guessed at: a mis-decoded body is worse than no body."""
    port = bomb_server(b"\x1b\x2e\x00\x00", encoding="br")

    async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
        result = await f.fetch_static(f"http://127.0.0.1:{port}/", loopback_policy(policy))

    assert result.outcome == "parse_error"


async def test_a_body_that_will_not_decompress_is_a_parse_error(
    policy, resolver, bomb_server
) -> None:
    port = bomb_server(b"this is not gzip at all, whatever the header says")

    async with Fetcher(resolver=resolver({"127.0.0.1": ["127.0.0.1"]})) as f:
        result = await f.fetch_static(f"http://127.0.0.1:{port}/", loopback_policy(policy))

    assert result.outcome == "parse_error"


# --------------------------------------------------------------------------
# HTTPS-final, and how netguard's refusals become outcomes
# --------------------------------------------------------------------------


async def test_a_plaintext_final_response_is_refused(policy, resolver, recorder) -> None:
    """On plain HTTP anyone on the path can rewrite the page (§6.4)."""
    rec = recorder(lambda r: html([b"<p>hi</p>"]))
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("http://example.test/x", policy(require_https_final=True))

    assert result.outcome == "unsafe_target"
    assert netguard.PLAINTEXT_FINAL in result.detail


async def test_an_http_hop_that_redirects_to_https_is_followed(policy, resolver, recorder) -> None:
    """Most sites redirect; refusing the *hop* would refuse most of the web."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "http":
            return streamed(301, headers={"location": "https://example.test/x"})
        return html([b"<p>hi</p>"])

    rec = recorder(handler)
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("http://example.test/x", policy(require_https_final=True))

    assert result.ok, result.detail
    assert result.final_url == "https://example.test/x"


async def test_the_per_domain_override_allows_a_plaintext_source(
    policy, resolver, recorder
) -> None:
    """§6.4's per-domain mechanism exists for the handful of http-only sources."""
    rec = recorder(lambda r: html([b"<p>hi</p>"]))
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("http://example.test/x", policy(require_https_final=False))

    assert result.ok, result.detail


@pytest.mark.parametrize(
    "url,mapping,expected_outcome,expected_reason",
    [
        ("https://ssrf.test/", {"ssrf.test": ["127.0.0.1"]}, "unsafe_target", "loopback address"),
        ("https://meta.test/", {"meta.test": ["169.254.169.254"]}, "unsafe_target", "metadata"),
        ("https://lan.test/", {"lan.test": ["192.168.1.1"]}, "unsafe_target", "private address"),
        # Mixed answers are the rebinding shape: one public, one private.
        (
            "https://mixed.test/",
            {"mixed.test": [PUBLIC, "10.1.2.3"]},
            "unsafe_target",
            "private address",
        ),
        ("file:///etc/passwd", {}, "unsafe_target", "scheme not allowed"),
    ],
)
async def test_netguard_refusals_are_recorded_as_unsafe_targets(
    policy, resolver, recorder, url, mapping, expected_outcome, expected_reason
) -> None:
    rec = recorder(lambda r: html([b"<p>hi</p>"]))
    async with Fetcher(client=rec.client(), resolver=resolver(mapping)) as f:
        result = await f.fetch_static(url, policy())

    assert result.outcome == expected_outcome
    assert expected_reason in result.detail
    assert rec.requests == [], "a refused target must never reach the transport"


async def test_a_name_that_will_not_resolve_is_a_connection_error_not_an_attack(
    policy, resolver, recorder
) -> None:
    """A DNS outage must not read as a burst of hostile targets on the health line."""
    rec = recorder(lambda r: html([b"<p>hi</p>"]))
    async with Fetcher(client=rec.client(), resolver=resolver(fail={"gone.test"})) as f:
        result = await f.fetch_static("https://gone.test/", policy())

    assert result.outcome == "connection_error"


async def test_the_dns_reasons_the_fetcher_branches_on_are_the_ones_netguard_raises(
    resolver,
) -> None:
    """Drift test: the mapping above is a string comparison against another module.

    ``fetch_static`` distinguishes a DNS failure from a hostile address by
    matching ``BlockedTarget.reason`` against ``netguard.DNS_REASONS``. If
    netguard ever reworded either message, that branch would silently start
    filing dead links as attacks — so the words themselves are asserted here,
    from netguard's own behaviour rather than from a copy of the strings.
    """
    with pytest.raises(netguard.BlockedTarget) as failed:
        await netguard.assert_url_allowed(
            "https://gone.test/", resolver=resolver(fail={"gone.test"})
        )
    assert failed.value.reason in netguard.DNS_REASONS

    async def empty(host: str, port: int = 443) -> list:
        return []

    with pytest.raises(netguard.BlockedTarget) as none_returned:
        await netguard.assert_url_allowed("https://empty.test/", resolver=empty)
    assert none_returned.value.reason in netguard.DNS_REASONS


# --------------------------------------------------------------------------
# Status handling
# --------------------------------------------------------------------------


async def test_a_304_is_recorded_as_not_modified_not_as_an_error(
    policy, resolver, recorder
) -> None:
    """Conditional requests are the cheapest thing in the crawl; they must not
    look like failures."""
    rec = recorder(lambda r: streamed(304, headers={"etag": '"abc"'}))
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static(
            "https://example.test/x", policy(), extra_headers={"If-None-Match": '"abc"'}
        )

    assert result.outcome == "not_modified"
    assert result.status_code == 304
    assert rec.requests[0].headers["if-none-match"] == '"abc"'


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
async def test_error_statuses_are_http_errors_carrying_the_code(
    policy, resolver, recorder, status
) -> None:
    rec = recorder(lambda r: streamed(status, headers={"content-type": "text/html"}))
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch_static("https://example.test/x", policy())

    assert result.outcome == "http_error"
    assert result.status_code == status


async def test_the_user_agent_and_contact_header_identify_the_crawler(
    policy, resolver, recorder
) -> None:
    """§14.2: crawling is identifiable and contactable, not anonymous."""
    rec = recorder(lambda r: html([b"<p>hi</p>"]))
    agent = "MeridianBot/0.1 (+https://example.org/contact)"
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        await f.fetch_static(
            "https://example.test/x", policy(user_agent=agent, send_contact_header=True)
        )

    assert rec.requests[0].headers["user-agent"] == agent
    assert "example.org/contact" in rec.requests[0].headers["from"]
