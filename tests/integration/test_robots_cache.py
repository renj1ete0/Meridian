"""The robots cache across a restart (task P1-29, spec §6.1, §2.3.1.3).

Against a real Postgres, because the claim is that a *new* `RobotsCache` — a
different process, as far as this is concerned — answers without fetching. An
in-memory double would make that trivially true by being the same object.

Two properties carry the file.

**A restart must not re-ask.** That is the whole task: the requests go out
through the same per-domain limiter the pages queue behind, so one wasted
request per origin is paid out of crawl throughput at exactly the moment a
worker is coming back.

**A restart must not turn an outage into consent.** `missing` and `unreachable`
both store no body and mean opposite things (§2.3.1.3): a 404 permits the whole
origin, an unreachable server refuses it until the file can be read. A
round-trip that collapsed them would silently grant permission the site never
gave — and only for the origins that were down when the worker restarted.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core import robotscache
from meridian_core.models import RobotsCacheEntry
from meridian_core.policy import ResolvedPolicy
from worker.fetch import FetchResult
from worker.robots import ROBOTS_ERROR_TTL_S, ROBOTS_TTL_S, RobotsCache

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)

DISALLOWS = "User-agent: *\nDisallow: /private/\n"


@pytest.fixture
def origin() -> str:
    return f"https://robots-{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def store(session_for, origin: str):
    """A session factory the cache can use, plus cleanup.

    The cache commits on its own transaction — deliberately, since the caller is
    a crawl loop whose transaction spans a page fetch — so the rows are real and
    have to be deleted rather than rolled back.
    """
    sess = await session_for("rw")
    await sess.rollback()

    def factory():
        class Scope:
            async def __aenter__(self):
                return sess

            async def __aexit__(self, *exc):
                return False

        return Scope()

    yield factory

    await sess.rollback()
    await sess.execute(delete(RobotsCacheEntry).where(RobotsCacheEntry.origin.like(f"{origin}%")))
    await sess.commit()


class CountingFetch:
    """Answers once and counts. The count is the assertion in most of this file."""

    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self, url: str, policy: ResolvedPolicy) -> FetchResult:
        self.calls += 1
        return self.result


URL = "https://example.test/robots.txt"


def ok(body: str = DISALLOWS) -> FetchResult:
    return FetchResult(
        requested_url=URL,
        final_url=URL,
        outcome="success",
        status_code=200,
        content=body.encode(),
    )


def failed(outcome: str, status: int | None = None) -> FetchResult:
    return FetchResult(
        requested_url=URL, final_url=URL, outcome=outcome, status_code=status
    )


def policy_for(origin: str) -> ResolvedPolicy:
    return ResolvedPolicy(domain=origin.removeprefix("https://"))


def restarted(fetch, store, *, now=lambda: NOW) -> RobotsCache:
    """A cache with nothing in memory — which is what a restart is, here."""
    return RobotsCache(fetch, store=store, now=now)


# --------------------------------------------------------------------------
# A restart does not re-ask
# --------------------------------------------------------------------------


async def test_a_new_cache_answers_from_the_database(store, origin) -> None:
    first = CountingFetch(ok())
    await restarted(first, store).rules_for(f"{origin}/page", policy_for(origin))

    second = CountingFetch(ok())
    rules = await restarted(second, store).rules_for(f"{origin}/other", policy_for(origin))

    assert first.calls == 1
    assert second.calls == 0, "a restart re-fetched robots.txt"
    assert rules.allows("/private/") is False


async def test_the_rules_survive_the_round_trip(store, origin) -> None:
    # Not just "something was cached": the file is re-parsed on load, so a
    # round-trip that lost the Disallow would leave the origin fully open and
    # nothing would report it.
    await restarted(CountingFetch(ok()), store).rules_for(f"{origin}/x", policy_for(origin))

    rules = await restarted(CountingFetch(ok("")), store).rules_for(
        f"{origin}/private/thing", policy_for(origin)
    )

    assert rules.allows("/private/thing") is False
    assert rules.allows("/public/thing") is True


async def test_the_raw_file_is_what_is_stored(store, origin, session_for) -> None:
    # Re-parsed rather than stored parsed, so a fix to the parser reaches
    # everything already cached rather than only what is fetched after it.
    await restarted(CountingFetch(ok()), store).rules_for(f"{origin}/x", policy_for(origin))

    sess = await session_for("rw")
    await sess.rollback()
    row = (
        await sess.scalars(
            select(RobotsCacheEntry).where(RobotsCacheEntry.origin == f"{origin}/robots.txt")
        )
    ).one()

    assert row.body == DISALLOWS
    assert row.outcome == robotscache.OK


async def test_an_expired_entry_is_re_fetched(store, origin) -> None:
    await restarted(CountingFetch(ok()), store).rules_for(f"{origin}/x", policy_for(origin))

    later = NOW + dt.timedelta(seconds=ROBOTS_TTL_S + 1)
    fetch = CountingFetch(ok())
    await restarted(fetch, store, now=lambda: later).rules_for(f"{origin}/x", policy_for(origin))

    assert fetch.calls == 1


async def test_an_entry_just_inside_its_life_is_not(store, origin) -> None:
    # The boundary, without which the test above would pass against an
    # implementation that expired everything immediately.
    await restarted(CountingFetch(ok()), store).rules_for(f"{origin}/x", policy_for(origin))

    later = NOW + dt.timedelta(seconds=ROBOTS_TTL_S - 60)
    fetch = CountingFetch(ok())
    await restarted(fetch, store, now=lambda: later).rules_for(f"{origin}/x", policy_for(origin))

    assert fetch.calls == 0


# --------------------------------------------------------------------------
# A restart does not turn an outage into consent (§2.3.1.3)
# --------------------------------------------------------------------------


async def test_an_unreachable_origin_is_still_refused_after_a_restart(store, origin) -> None:
    # The dangerous one. If the round trip collapsed `unreachable` into "no
    # body, so nothing is disallowed", every origin that was down at restart
    # would be crawled as though it had granted permission.
    await restarted(CountingFetch(failed("connect_error")), store).rules_for(
        f"{origin}/x", policy_for(origin)
    )

    rules = await restarted(CountingFetch(ok("")), store).rules_for(
        f"{origin}/x", policy_for(origin)
    )

    assert rules.allows("/anything") is False
    assert rules.unreachable is True


async def test_a_missing_robots_file_still_permits_after_a_restart(store, origin) -> None:
    # The other half, and what makes the test above mean something: a 404 is a
    # site saying it has no rules, and treating that as a refusal would take
    # every such domain out of the crawl.
    await restarted(CountingFetch(failed("http_error", 404)), store).rules_for(
        f"{origin}/x", policy_for(origin)
    )

    rules = await restarted(CountingFetch(ok(DISALLOWS)), store).rules_for(
        f"{origin}/x", policy_for(origin)
    )

    assert rules.allows("/anything") is True
    assert rules.unreachable is False


async def test_a_refusal_expires_far_sooner_than_a_file(store, origin, session_for) -> None:
    # Caching "refuse everything" for a day because of one blip takes a domain
    # out of the crawl for a day. The short TTL has to survive the round trip or
    # the persisted copy quietly restores the long one.
    await restarted(CountingFetch(failed("connect_error")), store).rules_for(
        f"{origin}/x", policy_for(origin)
    )

    sess = await session_for("rw")
    await sess.rollback()
    row = (
        await sess.scalars(
            select(RobotsCacheEntry).where(RobotsCacheEntry.origin == f"{origin}/robots.txt")
        )
    ).one()

    assert row.expires_at - row.fetched_at == dt.timedelta(seconds=ROBOTS_ERROR_TTL_S)


# --------------------------------------------------------------------------
# The cache may not become a way to stop the crawl
# --------------------------------------------------------------------------


async def test_a_broken_store_does_not_stop_the_crawl(origin) -> None:
    # An optimisation that can take the crawl down is worse than no
    # optimisation. The failure this guards is a database hiccup becoming "this
    # worker refuses every origin", which is the cache causing the outage it
    # was meant to shorten.
    def broken():
        raise RuntimeError("no database today")

    fetch = CountingFetch(ok())
    rules = await RobotsCache(fetch, store=broken, now=lambda: NOW).rules_for(
        f"{origin}/x", policy_for(origin)
    )

    assert fetch.calls == 1
    assert rules.allows("/public/") is True


async def test_without_a_store_it_is_the_in_process_cache_it_always_was(origin) -> None:
    # Any caller without a database — every unit test, and anything embedding
    # the crawler — gets the previous behaviour rather than an error.
    fetch = CountingFetch(ok())
    cache = RobotsCache(fetch)

    await cache.rules_for(f"{origin}/a", policy_for(origin))
    await cache.rules_for(f"{origin}/b", policy_for(origin))

    assert fetch.calls == 1


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


async def test_many_urls_from_one_origin_fetch_robots_once(store, origin) -> None:
    # What a crawl actually does at the start: a lane claims a batch of URLs
    # from one domain, and without the per-origin lock every one of them misses
    # the empty cache and fetches the same file — a thundering herd aimed at the
    # one file that asked to be treated gently.
    import asyncio

    fetch = CountingFetch(ok())
    cache = restarted(fetch, store)

    await asyncio.gather(
        *(cache.rules_for(f"{origin}/page-{i}", policy_for(origin)) for i in range(8))
    )

    assert fetch.calls == 1
