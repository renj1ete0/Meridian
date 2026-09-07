"""The loop against a real database (task P1-15, spec §6.1, §13.4).

`test_worker_loop.py` covers the loop's mechanics over a fake store. What that
cannot show is the thing P1-15 is actually for: a row goes into `queue`, and
without anybody watching it comes out the other side as a status, a dropped
lease, and a `fetch_attempts` row that says what happened. Every one of those is
a database effect, and a double would assert them against fields it invented.

The transport is still `httpx.MockTransport` — the socket is `test_fetch_*`'s
subject, not this one's.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from http_doubles import RecordingTransport, streamed
from sqlalchemy import delete, select

from meridian_core.models import FetchAttempt, FetchPolicy, QueueTask, Source
from worker.crawl import Crawler
from worker.fetch import Fetcher
from worker.main import Worker, WorkerSettings
from worker.ratelimit import DomainLimiter
from worker.robots import ALLOW_ALL, RobotsRules, parse

pytestmark = pytest.mark.usefixtures("require_db")

PUBLIC = "93.184.216.34"


@pytest.fixture
def run_domain() -> str:
    """A domain unique to one test, so rows never collide with the seeded ones."""
    return f"t{uuid.uuid4().hex[:12]}.test"


@pytest.fixture
def resolve(resolver, run_domain):
    """DNS for the test domain, answering with a real-looking public address."""
    return resolver({run_domain: [PUBLIC]})


@pytest.fixture
def run_topic() -> str:
    """A topic unique to one test, and the only one the worker under test claims.

    Not optional. This dev database holds the real seeded frontier, and a worker
    with no topic filter correctly claims those rows instead — which reads as
    "the loop did not fetch my task" and is actually the loop working. The same
    trap `test_queueing.py` documents, reached from the other side.
    """
    return f"test_worker_run_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def cleanup(session_for, run_domain, run_topic):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(FetchAttempt).where(FetchAttempt.domain == run_domain))
    await sess.execute(delete(FetchPolicy).where(FetchPolicy.domain == run_domain))
    await sess.execute(delete(Source).where(Source.url.like(f"https://{run_domain}%")))
    await sess.execute(delete(QueueTask).where(QueueTask.topic == run_topic))
    await sess.commit()


@asynccontextmanager
async def _session(sess):
    """Hand the worker the same transaction the test is inspecting.

    Both `Crawler._record` and the loop's settle step commit — a queue status
    that did not survive the process that set it would be a task claimed twice.
    Here that commit is downgraded to a flush so the rows are visible inside the
    test's own transaction and vanish with its rollback.
    """
    sess.commit = sess.flush
    yield sess


def build_worker(
    sess,
    handler,
    domain,
    topic,
    *,
    robots_rules: RobotsRules | None = ALLOW_ALL,
    resolver=None,
    **overrides,
):
    """A Worker over a real session, a recording transport and a primed robots cache.

    The resolver is stubbed to a real-looking public address. Without it the
    `.test` domains below go to the system resolver, fail, and every outcome
    here becomes `connection_error` — and TEST-NET ranges cannot stand in,
    because `ipaddress` classifies them non-global and `netguard` refuses them.
    """
    rec = RecordingTransport(handler)
    fetcher = Fetcher(client=rec.client(), resolver=resolver)
    crawler = Crawler(lambda: _session(sess), fetcher=fetcher, limiter=DomainLimiter())
    if robots_rules is not None:
        crawler.robots.prime(f"https://{domain}/", robots_rules)
    settings = WorkerSettings(
        worker_id=overrides.pop("worker_id", f"test-{uuid.uuid4().hex[:8]}"),
        concurrency=1,
        idle_sleep_s=0.01,
        housekeeping_interval_s=0,
        topics=(topic,),
        **overrides,
    )
    worker = Worker(crawler, settings=settings, session_factory=lambda: _session(sess))
    return worker, rec


def ok_html(request: httpx.Request) -> httpx.Response:
    return streamed(200, headers={"content-type": "text/html"}, chunks=[b"<p>hello</p>"])


def status(code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return streamed(code, headers={"content-type": "text/html"}, chunks=[b""])

    return handler


async def enqueue(sess, domain: str, topic: str, path: str = "/a", **fields) -> QueueTask:
    task = QueueTask(url_or_query=f"https://{domain}{path}", topic=topic, **fields)
    sess.add(task)
    await sess.flush()
    return task


async def attempts_for(sess, task_id: int) -> list[FetchAttempt]:
    rows = await sess.execute(
        select(FetchAttempt)
        .where(FetchAttempt.task_id == task_id)
        .order_by(FetchAttempt.attempt_id)
    )
    return list(rows.scalars())


# --------------------------------------------------------------------------
# A URL goes in, a status and an attempt row come out
# --------------------------------------------------------------------------


async def test_a_queued_url_is_fetched_and_advanced(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """The whole point of P1-15, asserted in the only place it is observable."""
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic)
    worker, rec = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)

    stats = await worker.run()

    assert stats.claimed == 1 and stats.fetched == 1
    await sess.refresh(task)
    assert task.status == "fetched"
    assert task.fetched_at is not None
    assert task.claimed_at is None and task.claimed_by is None, "a settled task keeps no lease"
    assert len(rec.requests) == 1


async def test_the_attempt_is_recorded_with_the_task_it_belongs_to(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """`fetch_attempts.task_id` is what makes the log joinable to the queue."""
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic)
    worker, _ = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)

    await worker.run()

    rows = await attempts_for(sess, task.task_id)
    assert len(rows) == 1
    assert rows[0].outcome == "success"
    assert rows[0].domain == run_domain
    assert rows[0].attempt_number == 1


async def test_a_retry_is_logged_as_the_attempt_it_actually_is(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """The trap P1-15 names: `attempts` is finished attempts, so this is +1.

    A task that has already failed twice must record attempt 3, not attempt 1.
    Written against the database because the number's whole purpose is being
    queryable later — "has this URL been failing all week, or did it just
    arrive" is a question about rows, not about a call argument.
    """
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic, attempts=2)
    worker, _ = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)

    await worker.run()

    rows = await attempts_for(sess, task.task_id)
    assert [r.attempt_number for r in rows] == [3]


# --------------------------------------------------------------------------
# The dispositions, on real rows
# --------------------------------------------------------------------------


async def test_a_transient_failure_leaves_the_task_pending_with_a_backoff(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic)
    worker, _ = build_worker(
        sess, status(503), run_domain, run_topic, resolver=resolve, max_tasks=1
    )

    stats = await worker.run()

    await sess.refresh(task)
    assert task.status == "pending"
    assert task.attempts == 1
    assert task.next_attempt_at is not None
    assert task.error.startswith("http_error")
    assert stats.retried == 1
    rows = await attempts_for(sess, task.task_id)
    assert rows[0].outcome == "http_error" and rows[0].status_code == 503


async def test_a_404_is_abandoned_rather_than_retried(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """The domain answered correctly about a URL that is not coming back."""
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic)
    worker, _ = build_worker(
        sess, status(404), run_domain, run_topic, resolver=resolve, max_tasks=1
    )

    stats = await worker.run()

    await sess.refresh(task)
    assert task.status == "failed"
    assert task.attempts == 1  # not three
    assert task.next_attempt_at is None
    assert stats.abandoned == 1


async def test_a_robots_denial_costs_one_attempt_and_no_requests(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """Retrying would ask the cached rules the same question twice more."""
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic, path="/private")
    rules = parse("User-agent: *\nDisallow: /private\n", "MeridianBot/0.1")
    worker, rec = build_worker(
        sess, ok_html, run_domain, run_topic, resolver=resolve, robots_rules=rules, max_tasks=1
    )

    stats = await worker.run()

    await sess.refresh(task)
    assert task.status == "failed"
    assert task.attempts == 1
    assert stats.abandoned == 1
    assert rec.requests == [], "no request should have gone out at all"
    rows = await attempts_for(sess, task.task_id)
    assert rows[0].outcome == "robots_denied"


async def test_an_unchanged_page_finishes_the_task(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """A 304 means the body is already in the corpus; there is nothing to extract.

    Advancing to `fetched` would hand the next stage a task with no bytes behind
    it. `done` is what a freshness check that found nothing new actually is.
    """
    sess = await session_for("rw")
    url = f"https://{run_domain}/a"
    sess.add(Source(url=url, etag='"v1"', title="cached"))
    await sess.flush()
    task = await enqueue(sess, run_domain, run_topic)

    def not_modified(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("If-None-Match") == '"v1"'
        return streamed(304, headers={}, chunks=[b""])

    worker, _ = build_worker(
        sess, not_modified, run_domain, run_topic, resolver=resolve, max_tasks=1
    )

    stats = await worker.run()

    await sess.refresh(task)
    assert task.status == "done"
    assert stats.unchanged == 1
    rows = await attempts_for(sess, task.task_id)
    assert rows[0].outcome == "not_modified"


async def test_a_blocked_domain_is_refused_before_any_request(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """The cheapest refusal: a policy row lookup, no network, no retry."""
    sess = await session_for("rw")
    sess.add(FetchPolicy(domain=run_domain, status="blocked"))
    await sess.flush()
    task = await enqueue(sess, run_domain, run_topic)
    worker, rec = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)

    await worker.run()

    await sess.refresh(task)
    assert task.status == "failed"
    assert rec.requests == []
    rows = await attempts_for(sess, task.task_id)
    assert rows[0].outcome == "blocked"


# --------------------------------------------------------------------------
# What the loop refuses to pick up
# --------------------------------------------------------------------------


async def test_a_doi_task_is_left_for_the_handler_that_will_take_it(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """P1-14 owns `doi`; claiming it here would fail a task that is not broken."""
    sess = await session_for("rw")
    doi = QueueTask(url_or_query="10.1234/x", task_type="doi", topic=run_topic, priority=100)
    sess.add(doi)
    await sess.flush()

    worker, rec = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)
    worker.stop()  # one pass over an empty-for-us queue, then out

    stats = await worker.run()

    await sess.refresh(doi)
    assert stats.claimed == 0
    assert doi.status == "pending" and doi.claimed_by is None
    assert rec.requests == []


async def test_a_task_still_backing_off_is_not_claimed(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """A dead domain must cost one attempt per backoff window, not one per loop."""
    import datetime as dt

    sess = await session_for("rw")
    task = await enqueue(
        sess,
        run_domain,
        run_topic,
        attempts=1,
        next_attempt_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    worker, rec = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)
    worker.stop()

    stats = await worker.run()

    await sess.refresh(task)
    assert stats.claimed == 0
    assert task.status == "pending"
    assert rec.requests == []


# --------------------------------------------------------------------------
# Draining, shutdown, housekeeping
# --------------------------------------------------------------------------


async def test_the_loop_drains_a_queue_and_stops_at_its_budget(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    sess = await session_for("rw")
    tasks = [await enqueue(sess, run_domain, run_topic, path=f"/p{i}") for i in range(4)]
    worker, rec = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=3)

    stats = await worker.run()

    assert stats.fetched == 3
    assert len(rec.requests) == 3
    for task in tasks:
        await sess.refresh(task)
    assert sorted(t.status for t in tasks) == ["fetched", "fetched", "fetched", "pending"]


async def test_shutdown_releases_the_lease_on_an_unfinished_task(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """A restart must not wait out a 15-minute lease to retry its own work.

    The task here is claimed by a worker that then stops without settling it —
    the shape of a `SIGTERM` arriving between the claim and the fetch.
    """
    from meridian_core.queueing import release_worker_claims

    sess = await session_for("rw")
    worker_id = f"test-{uuid.uuid4().hex[:8]}"
    task = await enqueue(sess, run_domain, run_topic)
    task.claimed_by = worker_id
    import datetime as dt

    task.claimed_at = dt.datetime.now(dt.UTC)
    await sess.flush()

    released = await release_worker_claims(sess, worker_id)

    assert released == 1
    await sess.refresh(task)
    assert task.claimed_by is None and task.claimed_at is None
    assert task.status == "pending", "handing work back is not the same as failing it"
    assert task.attempts == 0, "a clean shutdown must not spend an attempt"


async def test_housekeeping_runs_against_the_real_tables(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """The prune and the health line, exercised rather than mocked.

    `prune_attempts` batches and `queue_depth` groups; both are SQL that a
    double would not run. This asserts they execute and agree with the rows the
    test just wrote, not that they were called.
    """
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic)
    worker, _ = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)
    await worker.run()

    from meridian_core.attempts import fetch_health
    from meridian_core.queueing import queue_depth

    await worker.housekeep()  # must not raise against the real schema

    health = await fetch_health(sess, domain=run_domain)
    depth = await queue_depth(sess)
    assert health.attempts == 1 and health.success_rate == 1.0
    assert depth.get("fetched", 0) >= 1
    await sess.refresh(task)
    assert task.status == "fetched"


async def test_a_recent_attempt_survives_the_prune(
    session_for, resolve, run_domain, run_topic, cleanup
) -> None:
    """Retention bounds the table; it must not empty it.

    A prune that deleted everything would pass "housekeeping ran" and destroy
    the health line, so the retention window is what gets asserted.
    """
    sess = await session_for("rw")
    task = await enqueue(sess, run_domain, run_topic)
    worker, _ = build_worker(sess, ok_html, run_domain, run_topic, resolver=resolve, max_tasks=1)
    await worker.run()

    await worker.housekeep()

    rows = await attempts_for(sess, task.task_id)
    assert len(rows) == 1, "today's attempt is inside the retention window"
