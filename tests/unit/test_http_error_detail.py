"""What a 4xx gets recorded as (`P1-19`, §12.5).

`fetch_attempts` exists so one failure can be told from another. "HTTP 403" is
four different problems wearing the same three digits — a bot challenge, a
geo-block, a forbidden path, an expired credential — and each wants a different
response. These tests hold the detail line to saying which.

The case that motivated this was real: repeated attempts against one seed domain,
every one recorded as `HTTP 403`, and nothing in the database saying that the
cause was a bot challenge which will never succeed however often it is retried.
"""

from __future__ import annotations

from worker.fetch import describe_http_error


def test_a_bare_status_is_still_reported() -> None:
    assert describe_http_error(404) == "HTTP 404"
    assert describe_http_error(403, {}) == "HTTP 403"


def test_a_cloudflare_challenge_is_named() -> None:
    """The header means exactly one thing, so the detail says it in words.

    An operator scanning the attempt log should not have to know what
    `cf-mitigated` is to understand why this URL will never be fetched.
    """
    detail = describe_http_error(403, {"cf-mitigated": "challenge", "server": "cloudflare"})
    assert "cloudflare bot challenge" in detail
    assert detail.startswith("HTTP 403")


def test_the_challenge_is_not_reported_twice() -> None:
    """`server: cloudflare` adds nothing once the challenge has been named."""
    detail = describe_http_error(403, {"cf-mitigated": "challenge", "server": "cloudflare"})
    assert detail.lower().count("cloudflare") == 1


def test_a_cloudflare_server_without_a_challenge_is_still_reported() -> None:
    """A 403 from Cloudflare that is *not* a challenge is a different problem —
    an origin rule or a country block — and the header is the only clue."""
    detail = describe_http_error(403, {"server": "cloudflare"})
    assert "server=cloudflare" in detail
    assert "bot challenge" not in detail


def test_retry_after_is_carried_through() -> None:
    """For a 429 this is the single most actionable value in the response."""
    assert "retry-after=120" in describe_http_error(429, {"Retry-After": "120"})


def test_header_names_are_matched_case_insensitively() -> None:
    """Servers send `CF-Mitigated`, `cf-mitigated` and `Cf-Mitigated`."""
    for name in ("CF-Mitigated", "cf-mitigated", "Cf-MITIGATED"):
        assert "cloudflare bot challenge" in describe_http_error(403, {name: "challenge"})


def test_a_non_challenge_mitigation_value_is_not_misreported() -> None:
    detail = describe_http_error(403, {"cf-mitigated": "block"})
    assert "bot challenge" not in detail
    assert "cf-mitigated=block" in detail


def test_irrelevant_headers_are_not_included() -> None:
    """The line goes in a database column and gets read by a human."""
    detail = describe_http_error(403, {"content-type": "text/html", "set-cookie": "a=b"})
    assert detail == "HTTP 403"


def test_a_long_header_value_is_truncated() -> None:
    """A hostile origin controls these, and the column should not carry a
    kilobyte of its choosing per attempt."""
    detail = describe_http_error(403, {"x-error": "z" * 500})
    assert len(detail) < 200


def test_diagnosis_never_raises_on_odd_input() -> None:
    for headers in ({}, {"cf-mitigated": ""}, {"server": ""}, {"retry-after": "  "}):
        assert describe_http_error(403, headers).startswith("HTTP 403")
