"""The polite fetch, end to end (task P1-04, spec §6.4, §14.2).

Against a real Postgres because the whole point of this module is that the
decisions come out of the database: a `fetch_policy` row makes a domain blocked,
a `sources` row makes a request conditional. A mocked session would let the test
assert that the code reads fields it invented.

The transport is still `httpx.MockTransport` — what is being tested here is the
sequencing (policy, then robots, then the delay, then the request), not the
socket, which `test_fetch_*` already covers.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from http_doubles import RecordingTransport, streamed
from sqlalchemy import delete, select

from meridian_core.attempts import fetch_health
from meridian_core.models import FetchAttempt, FetchPolicy, Source
from worker.crawl import Crawler, conditional_headers, validators
from worker.fetch import Fetcher
from worker.ratelimit import DomainLimiter
from worker.robots import ALLOW_ALL, RobotsRules, parse

pytestmark = pytest.mark.usefixtures("require_db")

PUBLIC = "93.184.216.34"


@pytest.fixture
def crawl_domain() -> str:
    """A domain unique to one test, so rows never collide with the seeded ones."""
    return f"t{uuid.uuid4().hex[:12]}.test"


@pytest.fixture
async def cleanup(session_for, crawl_domain):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(FetchAttempt).where(FetchAttempt.domain == crawl_domain))
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain == crawl_domain))
    await sess.execute(delete(Source).where(Source.url.like(f"https://{crawl_domain}%")))
    await sess.commit()


@asynccontextmanager
async def _session(sess):
    """Hand the Crawler the same transaction the test is inspecting.

    ``Crawler._record`` commits — the attempt row and the policy consequence
    have to survive the process that wrote them. Here that commit is downgraded
    to a flush so the rows are visible to the test inside its own transaction
    and vanish with the rollback, rather than landing in the dev database.
    """
    sess.commit = sess.flush
    yield sess


def build(sess, handler, domain, *, robots_rules: RobotsRules | None = ALLOW_ALL, resolver=None):
    """A Crawler wired to a recording transport and a primed robots cache.

    The cache is primed for ``domain`` specifically so most tests below make one
    request rather than two. The cases that are about robots.txt itself pass
    ``robots_rules=None`` and let it be fetched.
    """
    rec = RecordingTransport(handler)
    fetcher = Fetcher(client=rec.client(), resolver=resolver)
    limiter = DomainLimiter()
    crawler = Crawler(lambda: _session(sess), fetcher=fetcher, limiter=limiter)
    if robots_rules is not None:
        crawler.robots.prime(f"https://{domain}/", robots_rules)
    return crawler, rec


def ok_html(request: httpx.Request) -> httpx.Response:
    return streamed(200, headers={"content-type": "text/html"}, chunks=[b"<p>hello</p>"])


# --------------------------------------------------------------------------
# Policy gate — the cheapest refusal, before any network
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["blocked", "paused"])
async def test_a_domain_the_policy_bars_is_never_requested(
    session_for, resolver, crawl_domain, cleanup, status
) -> None:
    """§6.4: blocked-domain marking stops one dead site eating the crawl budget."""
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=crawl_domain, settings={}, status=status))
    await sess.flush()

    crawler, rec = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    result = await crawler.fetch(f"https://{crawl_domain}/page")

    assert result.outcome == "blocked"
    assert status in result.detail
    assert rec.requests == [], "a blocked domain must cost no network at all"


async def test_an_unknown_domain_falls_through_to_the_global_policy(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    sess = await session_for("rw")
    crawler, rec = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))

    result = await crawler.fetch(f"https://{crawl_domain}/page")

    assert result.ok, result.detail
    assert len(rec.requests) == 1


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------


async def test_robots_is_fetched_before_the_page_and_can_refuse_it(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return streamed(
                200,
                headers={"content-type": "text/plain"},
                chunks=[b"User-agent: *\nDisallow: /private/\n"],
            )
        return ok_html(request)

    sess = await session_for("rw")
    crawler, rec = build(
        sess, handler, crawl_domain, robots_rules=None, resolver=resolver({crawl_domain: [PUBLIC]})
    )

    refused = await crawler.fetch(f"https://{crawl_domain}/private/x")
    assert refused.outcome == "robots_denied"
    assert [r.url.path for r in rec.requests] == ["/robots.txt"]

    allowed = await crawler.fetch(f"https://{crawl_domain}/public/x")
    assert allowed.ok, allowed.detail
    assert [r.url.path for r in rec.requests] == ["/robots.txt", "/public/x"], (
        "robots.txt must be cached, not re-fetched per URL"
    )


async def test_robots_can_be_turned_off_per_domain(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """The policy knob exists; when it is off, no robots request is made at all."""
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=crawl_domain, settings={"respect_robots": False}, status="active"))
    await sess.flush()

    crawler, rec = build(
        sess, ok_html, crawl_domain, robots_rules=None, resolver=resolver({crawl_domain: [PUBLIC]})
    )
    result = await crawler.fetch(f"https://{crawl_domain}/anything")

    assert result.ok
    assert [r.url.path for r in rec.requests] == ["/anything"]


async def test_a_crawl_delay_in_robots_slows_the_domain_down(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """A site asking for more space gets it; asking for less is not a licence."""
    sess = await session_for("rw")
    sess.add(
        FetchPolicy(
            domain=crawl_domain,
            settings={"delay_per_domain_ms": 0, "delay_jitter_ms": 0},
            status="active",
        )
    )
    await sess.flush()

    crawler, rec = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    crawler.robots.prime(
        f"https://{crawl_domain}/", parse("User-agent: *\nCrawl-delay: 0.15\n", "MeridianBot/0.1")
    )

    started = time.monotonic()
    await crawler.fetch(f"https://{crawl_domain}/a")
    await crawler.fetch(f"https://{crawl_domain}/b")
    elapsed = time.monotonic() - started

    # The policy asks for no delay at all, so any gap here came from robots.txt.
    assert elapsed >= 0.150 - 0.005, f"two fetches took {elapsed:.3f}s — Crawl-delay was ignored"
    assert len(rec.requests) == 2


# --------------------------------------------------------------------------
# Conditional requests
# --------------------------------------------------------------------------


def _source(url: str, **kwargs) -> Source:
    return Source(url=url, source_tier="government", retention_tier="primary", **kwargs)


@pytest.mark.parametrize(
    "stored,expected",
    [
        ({"etag": '"abc"'}, {"If-None-Match": '"abc"'}),
        (
            {"last_modified": "Sun, 30 Aug 2026 04:11:49 GMT"},
            {"If-Modified-Since": "Sun, 30 Aug 2026 04:11:49 GMT"},
        ),
        # RFC 9110 §13.1.2: the origin ignores If-Modified-Since when an ETag
        # is present, so sending both is bytes that cannot change the answer.
        (
            {"etag": '"abc"', "last_modified": "Sun, 30 Aug 2026 04:11:49 GMT"},
            {"If-None-Match": '"abc"'},
        ),
        ({}, {}),
    ],
)
def test_conditional_headers_prefer_the_etag(stored, expected) -> None:
    assert conditional_headers(_source("https://x/", **stored)) == expected


def test_no_conditional_headers_without_a_previous_fetch() -> None:
    assert conditional_headers(None) == {}


def test_the_policy_can_switch_conditional_requests_off() -> None:
    assert conditional_headers(_source("https://x/", etag='"abc"'), enabled=False) == {}


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"ETag": '"abc"'}, {"etag": '"abc"', "last_modified": None}),
        (
            {"last-modified": "Mon, 01 Jan 2026 00:00:00 GMT"},
            {"etag": None, "last_modified": "Mon, 01 Jan 2026 00:00:00 GMT"},
        ),
        ({"content-type": "text/html"}, {"etag": None, "last_modified": None}),
    ],
)
def test_validators_are_read_back_case_insensitively(headers, expected) -> None:
    """Header case is arbitrary on the wire and varies by server."""
    assert validators(headers) == expected


async def test_a_stored_etag_is_sent_and_a_304_costs_no_body(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """The point of the whole feature: an unchanged page is nearly free."""
    url = f"https://{crawl_domain}/report"
    sess = await session_for("rw")
    sess.add(_source(url, etag='"v1"', accessed_at=dt.datetime.now(dt.UTC)))
    await sess.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == '"v1"':
            return streamed(304, headers={"etag": '"v1"'})
        return ok_html(request)

    crawler, rec = build(sess, handler, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    result = await crawler.fetch(url)

    assert result.outcome == "not_modified"
    assert result.content == b""
    assert rec.requests[0].headers["if-none-match"] == '"v1"'


async def test_a_url_never_fetched_before_sends_no_validators(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    sess = await session_for("rw")
    crawler, rec = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))

    await crawler.fetch(f"https://{crawl_domain}/brand-new")

    assert "if-none-match" not in rec.requests[0].headers
    assert "if-modified-since" not in rec.requests[0].headers


async def test_the_validators_of_a_response_can_be_stored_for_next_time(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """The round trip: what a response carries is what the next request sends."""
    url = f"https://{crawl_domain}/round-trip"
    sess = await session_for("rw")

    def handler(request: httpx.Request) -> httpx.Response:
        return streamed(
            200,
            headers={
                "content-type": "text/html",
                "etag": '"v2"',
                "last-modified": "Mon, 01 Jan 2026 00:00:00 GMT",
            },
            chunks=[b"<p>x</p>"],
        )

    crawler, _ = build(sess, handler, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    result = await crawler.fetch(url)

    stored = _source(url, **validators(result.headers))
    sess.add(stored)
    await sess.flush()

    assert conditional_headers(stored) == {"If-None-Match": '"v2"'}


# --------------------------------------------------------------------------
# Rate limiting is actually applied
# --------------------------------------------------------------------------


async def test_the_domain_delay_is_applied_to_real_fetches(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    sess = await session_for("rw")
    sess.add(
        FetchPolicy(
            domain=crawl_domain,
            settings={"delay_per_domain_ms": 60, "delay_jitter_ms": 0},
            status="active",
        )
    )
    await sess.flush()

    crawler, rec = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))

    started = time.monotonic()
    await crawler.fetch(f"https://{crawl_domain}/a")
    await crawler.fetch(f"https://{crawl_domain}/b")
    elapsed = time.monotonic() - started

    assert elapsed >= 0.060 - 0.005, f"two fetches took {elapsed:.3f}s — the delay was skipped"
    assert len(rec.requests) == 2


async def test_robots_itself_goes_through_the_rate_limiter(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """Honouring a domain's delay for pages but not for robots.txt misses the point."""
    sess = await session_for("rw")
    limiter = DomainLimiter()
    rec = RecordingTransport(
        lambda r: streamed(200, headers={"content-type": "text/plain"}, chunks=[b"User-agent: *\n"])
    )
    crawler = Crawler(
        lambda: _session(sess),
        fetcher=Fetcher(client=rec.client(), resolver=resolver({crawl_domain: [PUBLIC]})),
        limiter=limiter,
    )

    await crawler.fetch(f"https://{crawl_domain}/page")

    assert limiter.tracked_domains == 1, "robots.txt bypassed the limiter entirely"


# --------------------------------------------------------------------------
# Every attempt is recorded, and every outcome has its consequence (P1-19, P1-05)
# --------------------------------------------------------------------------


async def _attempts(sess, domain: str) -> list[FetchAttempt]:
    rows = await sess.scalars(
        select(FetchAttempt).where(FetchAttempt.domain == domain).order_by(FetchAttempt.attempt_id)
    )
    return list(rows)


async def _policy_row(sess, domain: str) -> FetchPolicy | None:
    return await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == domain))


async def test_a_successful_fetch_writes_one_attempt_row(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """§12.5: without this the Pi can crawl 404s for a week unnoticed."""
    sess = await session_for("rw")
    crawler, _ = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))

    result = await crawler.fetch(f"https://{crawl_domain}/page", task_id=None, attempt_number=3)

    (row,) = await _attempts(sess, crawl_domain)
    assert row.outcome == result.outcome == "success"
    assert row.status_code == 200
    assert row.url == f"https://{crawl_domain}/page"
    assert row.bytes_fetched == len(result.content) > 0
    assert row.attempt_number == 3


@pytest.mark.parametrize(
    "path,expected",
    [("/private/x", "robots_denied"), ("/public/x", "success")],
)
async def test_a_refusal_is_recorded_as_faithfully_as_a_success(
    session_for, resolver, crawl_domain, cleanup, path, expected
) -> None:
    """A refusal that leaves no row is a crawler that looks idle while working."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return streamed(
                200,
                headers={"content-type": "text/plain"},
                chunks=[b"User-agent: *\nDisallow: /private/\n"],
            )
        return ok_html(request)

    sess = await session_for("rw")
    crawler, _ = build(
        sess, handler, crawl_domain, robots_rules=None, resolver=resolver({crawl_domain: [PUBLIC]})
    )

    await crawler.fetch(f"https://{crawl_domain}{path}")

    (row,) = await _attempts(sess, crawl_domain)
    assert row.outcome == expected


async def test_a_blocked_domain_is_recorded_without_being_requested(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """The row is how a queue quietly spinning on a blocked domain becomes visible."""
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=crawl_domain, settings={}, status="blocked"))
    await sess.flush()

    crawler, rec = build(sess, ok_html, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    await crawler.fetch(f"https://{crawl_domain}/page")

    (row,) = await _attempts(sess, crawl_domain)
    assert row.outcome == "blocked"
    assert rec.requests == []
    # A refusal to fetch an already-blocked domain is not fresh evidence, and
    # counting it would have the block deepen itself.
    assert (await _policy_row(sess, crawl_domain)).consecutive_failures == 0


async def test_repeated_failures_block_the_domain_and_clear_its_limiter_state(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """§6.4: one dead site must not consume crawl budget for weeks unnoticed."""
    sess = await session_for("rw")
    sess.add(
        FetchPolicy(
            domain=crawl_domain,
            settings={"blocked_after_failures": 3, "delay_per_domain_ms": 0, "delay_jitter_ms": 0},
            status="active",
        )
    )
    await sess.flush()

    def dead(request: httpx.Request) -> httpx.Response:
        return streamed(503, headers={"content-type": "text/html"})

    crawler, rec = build(sess, dead, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))

    for _ in range(3):
        await crawler.fetch(f"https://{crawl_domain}/page")

    row = await _policy_row(sess, crawl_domain)
    assert row.status == "blocked"
    assert row.consecutive_failures == 3
    assert "consecutive failures" in (row.note or "")
    assert crawler.limiter.tracked_domains == 0, "a domain never to be fetched again kept its slot"

    # And the block takes effect on the next request rather than at some later
    # reload: the fourth fetch never reaches the network.
    before = len(rec.requests)
    fourth = await crawler.fetch(f"https://{crawl_domain}/page")
    assert fourth.outcome == "blocked"
    assert len(rec.requests) == before


async def test_a_404_does_not_count_against_the_domain(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """A missing page is the domain answering correctly. Blocking a live site
    over five dead links would take the good part of the crawl down with them."""
    sess = await session_for("rw")
    sess.add(
        FetchPolicy(
            domain=crawl_domain,
            settings={"blocked_after_failures": 2, "delay_per_domain_ms": 0, "delay_jitter_ms": 0},
            status="active",
        )
    )
    await sess.flush()

    def missing(request: httpx.Request) -> httpx.Response:
        return streamed(404, headers={"content-type": "text/html"})

    crawler, _ = build(sess, missing, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    for _ in range(3):
        await crawler.fetch(f"https://{crawl_domain}/gone")

    row = await _policy_row(sess, crawl_domain)
    assert row.status == "active"
    assert row.consecutive_failures == 0
    assert [r.outcome for r in await _attempts(sess, crawl_domain)] == ["http_error"] * 3


async def test_one_good_fetch_clears_the_failures_before_it(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """Only *consecutive* failures block, so an intermittent domain survives."""
    sess = await session_for("rw")
    sess.add(
        FetchPolicy(
            domain=crawl_domain,
            settings={"blocked_after_failures": 3, "delay_per_domain_ms": 0, "delay_jitter_ms": 0},
            status="active",
        )
    )
    await sess.flush()

    responses = [503, 503, 200]

    def flaky(request: httpx.Request) -> httpx.Response:
        status = responses.pop(0)
        if status == 200:
            return ok_html(request)
        return streamed(status, headers={"content-type": "text/html"})

    crawler, _ = build(sess, flaky, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    for _ in range(3):
        await crawler.fetch(f"https://{crawl_domain}/page")

    row = await _policy_row(sess, crawl_domain)
    assert row.status == "active"
    assert row.consecutive_failures == 0


async def test_the_health_line_reads_what_the_crawler_wrote(
    session_for, resolver, crawl_domain, cleanup
) -> None:
    """The end of the loop P1-19 exists to close: fetches in, a rate out."""
    sess = await session_for("rw")
    responses = [200, 500, 200, 200]

    def mixed(request: httpx.Request) -> httpx.Response:
        status = responses.pop(0)
        if status == 200:
            return ok_html(request)
        return streamed(status, headers={"content-type": "text/html"})

    crawler, _ = build(sess, mixed, crawl_domain, resolver=resolver({crawl_domain: [PUBLIC]}))
    for i in range(4):
        await crawler.fetch(f"https://{crawl_domain}/p{i}")

    health = await fetch_health(sess, hours=1, domain=crawl_domain)
    assert health.attempts == 4
    assert health.successes == 3
    assert health.success_rate == pytest.approx(0.75)
    assert health.by_outcome == {"success": 3, "http_error": 1}
