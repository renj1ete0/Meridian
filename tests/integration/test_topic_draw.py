"""The crawl claims in proportion to the attention vector (`B-26`, §10).

§10's first line is that attention is a weight vector over topics and seeds are
drawn proportionally. Until `B-26` nothing in the acquisition path read those
weights — `steering.py` was the only module that touched them — and the effect
compounds rather than merely being absent: a discovered link inherits its
parent's topic, so whatever the crawl is already working on produces more of
itself. A real 2h20m run finished with 97% of a 44,000-row frontier on one
topic, and it was the topic weighted lowest of the three.

These tests drive `Worker._claim` against real rows, because the claim is where
the vector has to be read: the alternative — asserting on `draw_topic` alone —
would pass with the worker still claiming by priority and age.
"""

from __future__ import annotations

import uuid
from collections import Counter
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete

from meridian_core.models import QueueTask, TopicConfig
from meridian_core.queueing import enqueue
from worker.main import Worker, WorkerSettings

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def marker() -> str:
    return f"draw{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def frontier(session_for, marker: str, monkeypatch):
    """Two topics with equal frontiers and very unequal weights.

    Equal frontiers on purpose: if the queues differed in size, a claim that
    ignored the weights could still look proportional by accident.

    **The steering loader is patched rather than the rows.** An earlier version
    paused every other active topic so the draw was between these two, and
    restored them in teardown — which is fine until a teardown does not run.
    One did not, and the dev database sat with every topic paused and a later
    test failing somewhere else entirely. A fixture that mutates shared
    configuration is one bad exit away from being the thing that broke the
    suite.
    """
    sess = await session_for("rw")
    await sess.rollback()

    heavy, light = f"{marker}-heavy", f"{marker}-light"
    rows = [
        TopicConfig(topic=heavy, weight=0.9, floor=0.0, ceiling=1.0, status="active"),
        TopicConfig(topic=light, weight=0.1, floor=0.0, ceiling=1.0, status="active"),
    ]
    sess.add_all(rows)

    for topic in (heavy, light):
        for index in range(30):
            await enqueue(
                sess,
                f"https://{marker}-{topic}-{index}.test/page",
                topic=topic,
                seed_source="frontier",
                task_type="url",
                priority=50,
            )
    await sess.commit()

    async def only_ours(_sess):
        return rows

    monkeypatch.setattr("worker.main.steering_topics", only_ours)

    # Captured before anything can replace it. `worker_for` downgrades
    # `sess.commit` to `flush` so the worker's settle step stays inside the
    # test's transaction — which also disarms this teardown, silently: the
    # deletes below run, commit nothing, and the rows outlive the test. That
    # is how twelve topic rows and 360 queue rows were left behind, and the
    # first symptom was an unrelated test failing on floors that summed wrong.
    commit = sess.commit

    yield sess, heavy, light

    sess.commit = commit
    await sess.rollback()
    await sess.execute(delete(QueueTask).where(QueueTask.url_or_query.like(f"https://{marker}%")))
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic.like(f"{marker}%")))
    await sess.commit()


def worker_for(sess, **overrides) -> Worker:
    """A worker with no pinned topics, so the draw is what decides."""
    settings = WorkerSettings(
        worker_id=f"draw-{uuid.uuid4().hex[:8]}",
        concurrency=1,
        topics=overrides.pop("topics", ()),
        **overrides,
    )

    @asynccontextmanager
    async def factory():
        # The worker's settle step commits, and a queue status that did not
        # survive it would be a task claimed twice. Downgraded to a flush so
        # the rows stay inside the test's transaction.
        sess.commit = sess.flush
        yield sess

    return Worker(None, settings=settings, session_factory=factory)


async def test_claims_follow_the_weights(frontier) -> None:
    """Nine to one, against two frontiers of equal size.

    Loose bounds: this is a random draw and the test must not fail on a run of
    luck. Tight enough that claiming by priority and age — which would give
    roughly half each — fails every time.
    """
    sess, heavy, light = frontier
    worker = worker_for(sess)

    claims = Counter()
    for _ in range(40):
        claim = await worker._claim()
        assert claim is not None, "the frontier was not empty; a lane should never idle here"
        task = await sess.get(QueueTask, claim.task_id)
        claims[task.topic] += 1

    assert claims[heavy] > claims[light], (
        f"the heavier topic drew {claims[heavy]} of 40 against {claims[light]}; "
        "claims are not following the attention vector"
    )
    assert claims[heavy] >= 24, f"expected roughly 36 of 40, got {claims[heavy]}"


async def test_an_empty_topic_does_not_stall_a_lane(frontier) -> None:
    """A topic whose frontier is momentarily empty must fall back rather than
    return nothing. Otherwise a corpus with one starved topic crawls at a
    fraction of its concurrency and looks merely slow."""
    sess, heavy, light = frontier
    await sess.execute(delete(QueueTask).where(QueueTask.topic == heavy))
    await sess.flush()
    worker = worker_for(sess)

    claims = [await worker._claim() for _ in range(5)]

    assert all(claim is not None for claim in claims), "a drawn-but-empty topic stalled the lane"


async def test_a_pinned_topic_list_is_not_widened_by_the_draw(frontier) -> None:
    """`MERIDIAN_WORKER_TOPICS` is somebody saying what this worker is for.
    A weight vector must not quietly enlarge that."""
    sess, heavy, light = frontier
    worker = worker_for(sess, topics=(light,))

    for _ in range(6):
        claim = await worker._claim()
        assert claim is not None
        task = await sess.get(QueueTask, claim.task_id)
        assert task.topic == light, "the draw overrode an operator's own topic list"


async def test_an_empty_topic_does_not_donate_its_share_to_the_biggest_pile(
    frontier, session_for, marker
) -> None:
    """The refinement, and the reason it matters.

    Falling straight through to an unfiltered claim gives the share of every
    topic with nothing queued to whichever topic has most queued — which is
    the concentration this feature exists to correct, arriving by the back
    door. On the live stack three of six active topics held no rows, so 45% of
    the weight was being handed to the largest frontier.

    Here `heavy` is weighted nine to one and has nothing claimable, so every
    claim must come from `light` rather than from the unrelated rows the dev
    corpus is full of.
    """
    sess, heavy, light = frontier
    await sess.execute(delete(QueueTask).where(QueueTask.topic == heavy))
    await sess.flush()
    worker = worker_for(sess)

    topics = []
    for _ in range(10):
        claim = await worker._claim()
        assert claim is not None
        task = await sess.get(QueueTask, claim.task_id)
        topics.append(task.topic)

    assert set(topics) == {light}, (
        f"claims went to {sorted(set(topics))}; an empty topic's share was "
        "spent outside the attention pool"
    )
