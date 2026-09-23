"""The crawl-health dashboard's numbers against a real Postgres (task P6-25, §12.5, §13.4).

**Every test picks its own instant.** The dev database holds a real crawl and
another session may be writing to it, so a count over "the last 24 hours" is
whatever was last crawled. `crawl_health` takes ``now``, and every window, the
last attempt and the verdict's queue rows are bounded by it — so a test set far
in the future sees only the rows it placed just before that instant, and one
set far in the past sees nothing at all. That is also what makes the stalled
verdict testable here: it needs a last attempt that is *old*, and a real crawl
always has a newer one.

Rows are written in a transaction that is rolled back, so nothing needs
deleting and the concurrent session never sees them.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest

from api.main import create_app
from meridian_core.crawlhealth import HOURS, STALL_AFTER, TOP_DOMAINS, crawl_health
from meridian_core.db import dispose_engines
from meridian_core.models import FetchAttempt, QueueTask
from meridian_core.models.queue import FETCH_OUTCOME, TASK_STATUS

pytestmark = pytest.mark.usefixtures("require_db")

#: Past every real row, so the windows before it hold only what a test puts there.
FUTURE = dt.datetime(2099, 6, 1, 12, 0, tzinfo=dt.UTC)

#: Before every real row, so nothing — attempt or queue row — existed yet.
PAST = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def domain() -> str:
    return f"health-{uuid.uuid4().hex[:10]}.test"


@pytest.fixture
async def sess(session_for):
    return await session_for("rw")


def attempt(domain: str, at: dt.datetime, outcome: str = "success") -> FetchAttempt:
    return FetchAttempt(domain=domain, url=f"https://{domain}/x", attempted_at=at, outcome=outcome)


def task(domain: str, **over) -> QueueTask:
    return QueueTask(url_or_query=f"https://{domain}/{uuid.uuid4().hex[:6]}", **over)


# --------------------------------------------------------------------------
# The history
# --------------------------------------------------------------------------


async def test_attempts_land_in_the_hour_they_happened(sess, domain: str) -> None:
    """Rolling hours ending at ``now``, oldest first, empty hours kept.

    Bucket boundaries are where an off-by-one lives: an attempt exactly one
    hour old belongs to the second-newest bucket, and one 24 hours old is
    outside the window altogether rather than in the oldest bar.
    """
    sess.add_all(
        [
            attempt(domain, FUTURE - dt.timedelta(minutes=5)),
            attempt(domain, FUTURE - dt.timedelta(minutes=10), "timeout"),
            attempt(domain, FUTURE - dt.timedelta(minutes=10), "not_modified"),
            attempt(domain, FUTURE - dt.timedelta(hours=1)),
            attempt(domain, FUTURE - dt.timedelta(hours=23, minutes=30), "http_error"),
            attempt(domain, FUTURE - dt.timedelta(hours=24)),
            attempt(domain, FUTURE + dt.timedelta(minutes=1)),
        ]
    )
    await sess.flush()

    health = await crawl_health(sess, now=FUTURE)

    assert len(health.hours) == HOURS
    starts = [b.start for b in health.hours]
    assert starts == sorted(starts), "buckets must run oldest first"
    assert health.hours[-1].start == FUTURE - dt.timedelta(hours=1)

    newest, second, oldest = health.hours[-1], health.hours[-2], health.hours[0]
    # A 304 is a success: the chart and `alerts` must draw the same line.
    assert (newest.succeeded, newest.failed) == (2, 1)
    assert (second.succeeded, second.failed) == (1, 0)
    assert (oldest.succeeded, oldest.failed) == (0, 1)
    # The 24h-old row and the one after `now` are outside the window.
    assert sum(b.succeeded + b.failed for b in health.hours) == 5
    assert all(b.succeeded == b.failed == 0 for b in health.hours[1:-2])


async def test_the_outcome_mix_counts_every_outcome_the_schema_allows(sess, domain: str) -> None:
    """Every value of `FETCH_OUTCOME`, zeros included — read off the model, so
    an outcome added to the schema appears here without anyone remembering to."""
    for outcome, n in (("success", 3), ("robots_denied", 2), ("timeout", 1)):
        sess.add_all(
            attempt(domain, FUTURE - dt.timedelta(minutes=i + 1), outcome) for i in range(n)
        )
    await sess.flush()

    health = await crawl_health(sess, now=FUTURE)
    counts = {row.outcome: row.count for row in health.outcomes}

    assert set(counts) == set(FETCH_OUTCOME.enums)
    assert len(health.outcomes) == len(FETCH_OUTCOME.enums)
    assert counts["success"] == 3
    assert counts["robots_denied"] == 2
    assert counts["timeout"] == 1
    assert sum(counts.values()) == 6
    assert [row.count for row in health.outcomes] == sorted(counts.values(), reverse=True)


async def test_top_domains_are_the_last_hour_busiest_first(sess, domain: str) -> None:
    quiet = f"quiet-{domain}"
    sess.add_all(
        [
            *(attempt(domain, FUTURE - dt.timedelta(minutes=i + 1)) for i in range(3)),
            attempt(domain, FUTURE - dt.timedelta(minutes=30), "http_error"),
            attempt(quiet, FUTURE - dt.timedelta(minutes=2)),
            # Two hours old: in the day's chart, not in the last hour's list.
            *(attempt(quiet, FUTURE - dt.timedelta(hours=2)) for _ in range(9)),
        ]
    )
    await sess.flush()

    health = await crawl_health(sess, now=FUTURE)

    assert [(d.domain, d.attempts, d.succeeded) for d in health.top_domains] == [
        (domain, 4, 3),
        (quiet, 1, 1),
    ]


async def test_top_domains_are_capped(sess, domain: str) -> None:
    sess.add_all(
        attempt(f"{n}-{domain}", FUTURE - dt.timedelta(minutes=1)) for n in range(TOP_DOMAINS + 3)
    )
    await sess.flush()

    health = await crawl_health(sess, now=FUTURE)

    assert len(health.top_domains) == TOP_DOMAINS


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------


async def test_a_recent_attempt_is_crawling(sess, domain: str) -> None:
    sess.add(attempt(domain, FUTURE - dt.timedelta(minutes=2)))
    await sess.flush()

    live = (await crawl_health(sess, now=FUTURE)).liveness

    assert live.state == "crawling"
    assert live.last_attempt_at == FUTURE - dt.timedelta(minutes=2)
    assert live.quiet_seconds == 120


async def test_silence_with_ready_work_is_a_stall(sess, domain: str) -> None:
    """The failure the screen exists for: every process up, nothing fetching."""
    sess.add(attempt(domain, FUTURE - STALL_AFTER - dt.timedelta(minutes=1)))
    sess.add(task(domain))
    await sess.flush()

    live = (await crawl_health(sess, now=FUTURE)).liveness

    assert live.state == "stalled"
    assert live.ready >= 1
    assert live.quiet_seconds == int((STALL_AFTER + dt.timedelta(minutes=1)).total_seconds())


async def test_silence_just_inside_the_threshold_is_not_yet_a_stall(sess, domain: str) -> None:
    """The boundary, from the side that must not alarm."""
    sess.add(attempt(domain, FUTURE - STALL_AFTER + dt.timedelta(seconds=30)))
    sess.add(task(domain))
    await sess.flush()

    assert (await crawl_health(sess, now=FUTURE)).liveness.state == "crawling"


async def test_nothing_ever_fetched_and_nothing_queued_is_idle(sess) -> None:
    """The empty case must be a state, not a crash: no attempt, no queue, no
    buckets with anything in them."""
    health = await crawl_health(sess, now=PAST)

    assert health.liveness.state == "idle"
    assert health.liveness.last_attempt_at is None
    assert health.liveness.quiet_seconds is None
    assert (health.liveness.ready, health.liveness.pending) == (0, 0)
    assert len(health.hours) == HOURS
    assert all(b.succeeded == b.failed == 0 for b in health.hours)
    assert all(row.count == 0 for row in health.outcomes)
    assert health.top_domains == []


async def test_ready_work_that_was_never_fetched_is_a_stall(sess, domain: str) -> None:
    """No attempt on record is not "fine" when there is work: it is a worker
    that never started."""
    sess.add(task(domain, created_at=PAST - dt.timedelta(days=1)))
    await sess.flush()

    live = (await crawl_health(sess, now=PAST)).liveness

    assert live.state == "stalled"
    assert live.last_attempt_at is None
    assert (live.ready, live.pending) == (1, 1)


async def test_pending_work_inside_its_backoff_is_waiting_not_stalled(sess, domain: str) -> None:
    """A queue that is only backing off is doing its job. Calling it a stall
    sends somebody to restart a worker that has nothing it may claim."""
    sess.add(
        task(
            domain,
            created_at=PAST - dt.timedelta(days=1),
            next_attempt_at=PAST + dt.timedelta(hours=1),
        )
    )
    await sess.flush()

    live = (await crawl_health(sess, now=PAST)).liveness

    assert live.state == "waiting"
    assert (live.ready, live.pending) == (0, 1)


async def test_rows_that_are_not_pending_do_not_hold_off_idle(sess, domain: str) -> None:
    """A finished or failed row is not work. A drained queue full of `done`
    rows must read as idle, which is the whole point of the idle state."""
    for status in ("done", "failed"):
        sess.add(task(domain, status=status, created_at=PAST - dt.timedelta(days=1)))
    await sess.flush()

    health = await crawl_health(sess, now=PAST)

    assert health.liveness.state == "idle"


# --------------------------------------------------------------------------
# The queue and the backlog
# --------------------------------------------------------------------------


async def test_the_queue_names_every_status(sess, domain: str) -> None:
    """Every value of `TASK_STATUS`, zeros included: a status missing from the
    screen reads as a status nobody is in."""
    sess.add(task(domain, status="rejected_duplicate"))
    await sess.flush()

    queue = (await crawl_health(sess)).queue

    assert set(queue) == set(TASK_STATUS.enums)
    # At least, not exactly: the dev database is shared and live.
    assert queue["rejected_duplicate"] >= 1


async def test_the_embedding_backlog_matches_what_the_embedder_will_take(sess) -> None:
    """Same definition as `chunks.embedding_backlog`, which is what the
    embedder drains and what `alerts` reports on — three places, one number."""
    from meridian_core.chunks import embedding_backlog

    assert (await crawl_health(sess)).embedding_backlog == await embedding_backlog(sess)


# --------------------------------------------------------------------------
# Through the API, on the read-only role
# --------------------------------------------------------------------------


async def test_the_route_serves_the_full_shape_on_the_read_only_role() -> None:
    """Through the app, which opens its own `meridian_ro` connection. A grant
    that silently denied one of the three tables would be a 500 here and
    nowhere else."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
            response = await c.get("/api/explore/crawl-health")
    finally:
        await dispose_engines()

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["hours"]) == HOURS
    assert {row["outcome"] for row in body["outcomes"]} == set(FETCH_OUTCOME.enums)
    assert set(body["queue"]) == set(TASK_STATUS.enums)
    assert body["liveness"]["state"] in ("crawling", "stalled", "waiting", "idle")
    assert body["stall_after_seconds"] == int(STALL_AFTER.total_seconds())
