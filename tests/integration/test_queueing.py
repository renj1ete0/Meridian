"""Integration tests for claiming and releasing queue tasks — queueing.py,
spec §5.1, §6.1, §13.4.

Run against a real Postgres because the property that matters most,
``FOR UPDATE SKIP LOCKED`` correctness, cannot be observed any other way: a
mock or SQLite would happily let two "transactions" claim the same row, since
neither implements row-level locking.

Tests are scoped to distinct ``topic`` values (``claim_next``'s own filter)
rather than to specific priorities, so they run correctly next to the real
seeded frontier rows that already sit in this dev database's ``queue`` table.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import uuid

import pytest
from sqlalchemy import delete, func, select

from meridian_core.models import QueueTask
from meridian_core.queueing import (
    DEFAULT_LEASE_SECONDS,
    abandon,
    advance,
    claim_next,
    fail,
    queue_depth,
    reclaim_expired,
    release,
    release_worker_claims,
)

pytestmark = pytest.mark.usefixtures("require_db")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --------------------------------------------------------------------------
# No task is claimed twice — needs real concurrency
# --------------------------------------------------------------------------


async def test_concurrent_claims_never_double_claim_a_task(session_for) -> None:
    """Two (or more) claimers racing for the same rows must never both come
    away with the same task — the exact property ``FOR UPDATE SKIP LOCKED``
    exists for (module docstring).

    A *sequential* version of this test — claim, await, claim again — would
    pass even with the locking removed entirely, because by the time the
    second claim runs the first has already committed its change. The only
    way to actually exercise the lock is to have multiple live transactions
    contend for the same rows at once, so this launches several claims
    concurrently via ``asyncio.gather`` against separate sessions (separate
    Postgres connections, separate transactions) racing for a pool smaller
    than the number of claimers.

    Without ``with_for_update(skip_locked=True)`` (a plain, unlocked
    ``SELECT``), overlapping transactions can all read the same still-pending
    row before any of them commits its claim, and each one's local
    ``QueueTask`` object would then report itself as the successful claimant
    of the same ``task_id`` — i.e. exactly the double claim this test would
    catch by finding a duplicate in ``claimed_ids``.
    """
    # Unique per run. The rows below must be committed for other connections to
    # see them, so a run killed mid-flight leaves them behind and a fixed topic
    # would poison the next run — which is exactly what happened once.
    race_topic = f"test_queueing_race_{uuid.uuid4().hex[:8]}"

    setup = await session_for("rw")
    tasks = [
        QueueTask(
            url_or_query=f"https://queueing-race-test.example/{uuid.uuid4().hex[:8]}/{i}",
            topic=race_topic,
            status="pending",
        )
        for i in range(3)
    ]
    setup.add_all(tasks)
    await setup.flush()
    task_ids = {t.task_id for t in tasks}
    # Committed so the racing sessions below — separate connections — can see
    # these rows at all; a session_for session with nothing but a flush is
    # invisible outside its own uncommitted transaction.
    await setup.commit()

    racer_count = 5  # more claimers than tasks, so some must legitimately get None
    racers = [await session_for("rw") for _ in range(racer_count)]
    try:
        results = await asyncio.gather(
            *(
                claim_next(sess, worker_id=f"racer-{i}", topics=[race_topic])
                for i, sess in enumerate(racers)
            )
        )
        claimed_ids = [r.task_id for r in results if r is not None]
        none_count = sum(1 for r in results if r is None)

        assert len(claimed_ids) == len(task_ids), "every task should end up claimed exactly once"
        assert len(set(claimed_ids)) == len(claimed_ids), (
            f"the same task was claimed by more than one racer: {claimed_ids}"
        )
        assert set(claimed_ids) == task_ids
        assert none_count == racer_count - len(task_ids)
    finally:
        # Release locks before deleting, or the cleanup below would block on
        # whatever locks the still-open racer transactions are holding.
        for sess in racers:
            await sess.rollback()
        cleanup = await session_for("rw")
        await cleanup.execute(delete(QueueTask).where(QueueTask.task_id.in_(task_ids)))
        await cleanup.commit()


# --------------------------------------------------------------------------
# Priority ordering
# --------------------------------------------------------------------------


async def test_higher_priority_is_claimed_before_older_lower_priority(session_for) -> None:
    """§5.2 tier-upranking: a higher-priority task must be fetched first even
    when a lower-priority one has been waiting far longer."""
    sess = await session_for("rw")
    now = _now()
    old_low = QueueTask(
        url_or_query="https://queueing-test.example/old-low",
        topic="test_queueing_priority",
        priority=0,
        created_at=now - dt.timedelta(hours=2),
    )
    new_high = QueueTask(
        url_or_query="https://queueing-test.example/new-high",
        topic="test_queueing_priority",
        priority=10,
        created_at=now - dt.timedelta(minutes=1),
    )
    sess.add_all([old_low, new_high])
    await sess.flush()

    claimed = await claim_next(sess, worker_id="w1", topics=["test_queueing_priority"])
    assert claimed is not None and claimed.task_id == new_high.task_id, "priority must outrank age"


async def test_equal_priority_oldest_wins(session_for) -> None:
    """No starvation: among equal priority, the oldest task must win, or a
    steady stream of same-priority arrivals could starve one out forever."""
    sess = await session_for("rw")
    now = _now()
    older = QueueTask(
        url_or_query="https://queueing-test.example/older",
        topic="test_queueing_age",
        priority=5,
        created_at=now - dt.timedelta(hours=1),
    )
    newer = QueueTask(
        url_or_query="https://queueing-test.example/newer",
        topic="test_queueing_age",
        priority=5,
        created_at=now - dt.timedelta(minutes=1),
    )
    sess.add_all([newer, older])  # insertion order deliberately not age order
    await sess.flush()

    claimed = await claim_next(sess, worker_id="w1", topics=["test_queueing_age"])
    assert claimed is not None and claimed.task_id == older.task_id


# --------------------------------------------------------------------------
# Backoff excludes a task until it passes
# --------------------------------------------------------------------------


async def test_backoff_excludes_a_task_until_it_passes(session_for) -> None:
    """A task with ``next_attempt_at`` in the future must not be claimable —
    the entire mechanism that stops a dead domain from spinning the queue."""
    sess = await session_for("rw")
    now = _now()
    not_due = QueueTask(
        url_or_query="https://queueing-test.example/not-due",
        topic="test_queueing_backoff",
        next_attempt_at=now + dt.timedelta(hours=1),
    )
    due = QueueTask(
        url_or_query="https://queueing-test.example/due",
        topic="test_queueing_backoff",
        next_attempt_at=now - dt.timedelta(seconds=1),
    )
    sess.add_all([not_due, due])
    await sess.flush()

    first = await claim_next(sess, worker_id="w1", topics=["test_queueing_backoff"])
    assert first is not None and first.task_id == due.task_id, "the due task must be claimable"

    second = await claim_next(sess, worker_id="w1", topics=["test_queueing_backoff"])
    assert second is None, "a task whose backoff has not passed must not be claimable"


# --------------------------------------------------------------------------
# Lease expiry — the crashed-worker path
# --------------------------------------------------------------------------


async def test_expired_lease_is_reclaimable_by_another_worker(session_for) -> None:
    sess = await session_for("rw")
    now = _now()
    stale = QueueTask(
        url_or_query="https://queueing-test.example/stale-lease",
        topic="test_queueing_lease",
        claimed_at=now - dt.timedelta(seconds=DEFAULT_LEASE_SECONDS + 60),
        claimed_by="dead-worker",
    )
    sess.add(stale)
    await sess.flush()

    claimed = await claim_next(sess, worker_id="new-worker", topics=["test_queueing_lease"])
    assert claimed is not None and claimed.task_id == stale.task_id
    assert claimed.claimed_by == "new-worker"


async def test_recently_claimed_lease_is_not_reclaimable(session_for) -> None:
    sess = await session_for("rw")
    now = _now()
    fresh = QueueTask(
        url_or_query="https://queueing-test.example/fresh-lease",
        topic="test_queueing_lease_fresh",
        claimed_at=now - dt.timedelta(seconds=30),
        claimed_by="alive-worker",
    )
    sess.add(fresh)
    await sess.flush()

    claimed = await claim_next(sess, worker_id="new-worker", topics=["test_queueing_lease_fresh"])
    assert claimed is None, "a lease still inside its window must not be stolen"


# --------------------------------------------------------------------------
# fail()
# --------------------------------------------------------------------------


async def test_fail_retries_within_budget_and_preserves_the_error(session_for) -> None:
    sess = await session_for("rw")
    task = QueueTask(url_or_query="https://queueing-test.example/fail-retry", status="pending")
    sess.add(task)
    await sess.flush()

    will_retry = await fail(sess, task, "connection reset", max_retries=2, rng=random.Random(0))

    assert will_retry is True
    assert task.attempts == 1
    assert task.error == "connection reset", "the only record of why a URL never made it in"
    assert task.status == "pending", "fail() does not itself change status while retries remain"
    assert task.next_attempt_at is not None and task.next_attempt_at > _now()
    assert task.claimed_at is None
    assert task.claimed_by is None


async def test_fail_past_max_retries_marks_failed_and_clears_next_attempt(session_for) -> None:
    sess = await session_for("rw")
    task = QueueTask(
        url_or_query="https://queueing-test.example/fail-exhausted", status="pending", attempts=2
    )
    sess.add(task)
    await sess.flush()

    will_retry = await fail(sess, task, "still 500ing", max_retries=2)

    assert will_retry is False
    assert task.status == "failed"
    assert task.next_attempt_at is None
    assert task.error == "still 500ing"


async def test_fail_truncates_a_very_long_error_to_2000_chars(session_for) -> None:
    sess = await session_for("rw")
    task = QueueTask(url_or_query="https://queueing-test.example/fail-long-error", status="pending")
    sess.add(task)
    await sess.flush()

    await fail(sess, task, "x" * 5000, max_retries=2)

    assert len(task.error) == 2000


# --------------------------------------------------------------------------
# release() and advance()
# --------------------------------------------------------------------------


async def test_release_drops_the_lease_without_consuming_an_attempt(session_for) -> None:
    sess = await session_for("rw")
    task = QueueTask(
        url_or_query="https://queueing-test.example/release", topic="test_queueing_release"
    )
    sess.add(task)
    await sess.flush()

    claimed = await claim_next(sess, worker_id="w1", topics=["test_queueing_release"])
    assert claimed is not None

    await release(sess, claimed)

    assert claimed.claimed_at is None
    assert claimed.claimed_by is None
    assert claimed.attempts == 0, "a clean shutdown must not cost the task an attempt"
    assert claimed.status == "pending"

    # And it must genuinely be back in the pool for someone else.
    reclaimed = await claim_next(sess, worker_id="w2", topics=["test_queueing_release"])
    assert reclaimed is not None and reclaimed.task_id == task.task_id


async def test_advance_to_fetched_stamps_fetched_at_and_drops_the_lease(session_for) -> None:
    sess = await session_for("rw")
    task = QueueTask(
        url_or_query="https://queueing-test.example/advance-fetched",
        claimed_at=_now(),
        claimed_by="w1",
    )
    sess.add(task)
    await sess.flush()

    await advance(sess, task, "fetched")

    assert task.status == "fetched"
    assert task.fetched_at is not None
    assert task.claimed_at is None
    assert task.claimed_by is None


async def test_advance_to_a_non_fetched_status_does_not_stamp_fetched_at(session_for) -> None:
    sess = await session_for("rw")
    task = QueueTask(url_or_query="https://queueing-test.example/advance-other")
    sess.add(task)
    await sess.flush()

    await advance(sess, task, "rejected_duplicate")

    assert task.status == "rejected_duplicate"
    assert task.fetched_at is None


# --------------------------------------------------------------------------
# reclaim_expired()
# --------------------------------------------------------------------------


async def test_reclaim_expired_clears_only_expired_leases_and_returns_the_count(
    session_for,
) -> None:
    sess = await session_for("rw")
    now = _now()

    # A baseline, rather than assuming this dev database has zero pre-existing
    # expired leases, so the assertion below is exact either way.
    baseline = await sess.scalar(
        select(func.count())
        .select_from(QueueTask)
        .where(
            QueueTask.claimed_at.is_not(None),
            QueueTask.claimed_at < now - dt.timedelta(seconds=DEFAULT_LEASE_SECONDS),
        )
    )

    stale = QueueTask(
        url_or_query="https://queueing-test.example/reclaim-stale",
        claimed_at=now - dt.timedelta(seconds=DEFAULT_LEASE_SECONDS + 60),
        claimed_by="dead-worker",
    )
    fresh = QueueTask(
        url_or_query="https://queueing-test.example/reclaim-fresh",
        claimed_at=now - dt.timedelta(seconds=30),
        claimed_by="alive-worker",
    )
    sess.add_all([stale, fresh])
    await sess.flush()

    count = await reclaim_expired(sess, lease_seconds=DEFAULT_LEASE_SECONDS)

    assert count == baseline + 1

    await sess.refresh(stale)
    await sess.refresh(fresh)
    assert stale.claimed_at is None and stale.claimed_by is None
    assert fresh.claimed_at is not None and fresh.claimed_by == "alive-worker", (
        "reclaim_expired must leave a live lease alone"
    )


# --------------------------------------------------------------------------
# Task-type filtering — the loop claims only what it can handle (P1-15)
# --------------------------------------------------------------------------


async def test_a_claim_can_be_narrowed_to_the_task_types_a_caller_handles(session_for) -> None:
    """The worker fetches URLs and reads sitemaps; `query` and `doi` do not.

    Without the filter the loop's only options for a `doi` row are to fail a
    task that is not broken or to claim it, release it and claim it again
    forever. Asserted here rather than in a unit test because the filter is a
    SQL predicate — an in-memory double would pass with it removed.
    """
    topic = f"test_queueing_types_{uuid.uuid4().hex[:8]}"
    sess = await session_for("rw")
    sess.add_all(
        [
            QueueTask(
                url_or_query="10.1234/some-doi",
                task_type="doi",
                topic=topic,
                priority=10,  # highest, so it would be claimed first if eligible
            ),
            QueueTask(
                url_or_query="https://queueing-test.example/typed",
                task_type="url",
                topic=topic,
                priority=1,
            ),
        ]
    )
    await sess.flush()

    claimed = await claim_next(sess, worker_id="w", topics=[topic], task_types=["url"])

    assert claimed is not None
    assert claimed.task_type == "url"

    # And the doi row is still pending, waiting for the handler that will take it.
    nothing_left = await claim_next(sess, worker_id="w", topics=[topic], task_types=["url"])
    assert nothing_left is None


async def test_an_unfiltered_claim_still_takes_any_type(session_for) -> None:
    """The filter is opt-in: existing callers must not silently start skipping."""
    topic = f"test_queueing_types_{uuid.uuid4().hex[:8]}"
    sess = await session_for("rw")
    sess.add(QueueTask(url_or_query="10.1234/lonely-doi", task_type="doi", topic=topic))
    await sess.flush()

    claimed = await claim_next(sess, worker_id="w", topics=[topic])

    assert claimed is not None and claimed.task_type == "doi"


# --------------------------------------------------------------------------
# Abandoning, releasing, and counting (P1-15)
# --------------------------------------------------------------------------


async def test_abandon_fails_a_task_immediately_and_counts_the_attempt(session_for) -> None:
    """A refusal spends no retries but is still an attempt that happened.

    `fail` would leave this pending with a backoff, and robots.txt would be
    asked the same question two more times over the next hour to be given the
    same answer.
    """
    sess = await session_for("rw")
    task = QueueTask(
        url_or_query="https://queueing-test.example/abandoned",
        topic="test_queueing_abandon",
        claimed_at=_now(),
        claimed_by="worker-1",
    )
    sess.add(task)
    await sess.flush()

    await abandon(sess, task, "robots_denied: disallowed by robots.txt")

    assert task.status == "failed"
    assert task.attempts == 1
    assert task.next_attempt_at is None
    assert task.claimed_at is None and task.claimed_by is None
    assert task.error == "robots_denied: disallowed by robots.txt"


async def test_abandon_truncates_an_enormous_error_rather_than_failing_the_write(
    session_for,
) -> None:
    """`queue.error` is TEXT, but the same 2000-char bound as `fail` applies.

    A refusal detail carrying a whole HTML error page would otherwise make the
    record of *why* a URL failed unreadable.
    """
    sess = await session_for("rw")
    task = QueueTask(url_or_query="https://queueing-test.example/long-error")
    sess.add(task)
    await sess.flush()

    await abandon(sess, task, "x" * 5000)

    assert len(task.error) == 2000


async def test_release_worker_claims_drops_only_this_workers_pending_leases(session_for) -> None:
    """Shutdown hands work back; it must not reopen tasks that finished.

    The `status == pending` half is the part worth asserting: without it a
    restart would clear the lease on a task that had already been fetched,
    which is harmless today and exactly the sort of thing that stops being
    harmless once a later stage claims on the same column.
    """
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    other_id = f"worker-{uuid.uuid4().hex[:8]}"
    sess = await session_for("rw")
    now = _now()

    mine = QueueTask(
        url_or_query="https://queueing-test.example/release-mine",
        claimed_at=now,
        claimed_by=worker_id,
        status="pending",
    )
    mine_done = QueueTask(
        url_or_query="https://queueing-test.example/release-mine-done",
        claimed_at=now,
        claimed_by=worker_id,
        status="fetched",
    )
    theirs = QueueTask(
        url_or_query="https://queueing-test.example/release-theirs",
        claimed_at=now,
        claimed_by=other_id,
        status="pending",
    )
    sess.add_all([mine, mine_done, theirs])
    await sess.flush()

    released = await release_worker_claims(sess, worker_id)

    assert released == 1
    for row in (mine, mine_done, theirs):
        await sess.refresh(row)
    assert mine.claimed_by is None and mine.claimed_at is None
    assert mine_done.claimed_by == worker_id, "a finished task must not be reopened"
    assert theirs.claimed_by == other_id, "another worker's lease is not ours to drop"


async def test_queue_depth_counts_by_status(session_for) -> None:
    """The queue half of §12.5's health line.

    Counted per status rather than as one number: 4,000 pending and 4,000
    failed are the same depth and opposite situations, and a health line that
    cannot tell them apart is the silent failure it exists to prevent.
    """
    sess = await session_for("rw")
    before = await queue_depth(sess)

    sess.add_all(
        [
            QueueTask(url_or_query="https://queueing-test.example/depth-1", status="pending"),
            QueueTask(url_or_query="https://queueing-test.example/depth-2", status="pending"),
            QueueTask(url_or_query="https://queueing-test.example/depth-3", status="failed"),
        ]
    )
    await sess.flush()

    after = await queue_depth(sess)

    assert after["pending"] == before.get("pending", 0) + 2
    assert after["failed"] == before.get("failed", 0) + 1
