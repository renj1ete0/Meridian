"""Queued search seeds (task B-51)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.models import GazetteerTerm, QueueTask, TopicConfig
from worker.seedsearch import MAX_PENDING, QUERY_PRIORITY, run_once

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def factory(sess):
    @asynccontextmanager
    async def make():
        sess.commit = sess.flush
        yield sess

    return make


@pytest.fixture
async def topic(sess) -> str:
    """A topic of this test's own; every other topic is paused for the test's
    transaction, so the counts are about this one."""
    name = f"seedtopic-{uuid.uuid4().hex[:6]}"
    for row in await sess.scalars(select(TopicConfig)):
        row.status = "paused"
    sess.add(
        TopicConfig(
            topic=name,
            weight=0.1,
            floor=0.0,
            ceiling=1.0,
            status="active",
            description="how easy and pleasant a place is to walk around in",
        )
    )
    await sess.flush()
    return name


async def mine(sess, topic: str) -> list[QueueTask]:
    return list(await sess.scalars(select(QueueTask).where(QueueTask.topic == topic)))


async def test_queries_are_queued_as_diversity_seeds_above_every_link(sess, topic) -> None:
    await run_once(write=True, seed=1, session_factory=factory(sess))

    rows = await mine(sess, topic)
    assert rows
    assert {r.task_type for r in rows} == {"query"}
    assert {r.seed_source for r in rows} == {"diversity"}
    assert {r.priority for r in rows} == {QUERY_PRIORITY}


async def test_a_report_queues_nothing(sess, topic) -> None:
    planned = (await run_once(write=False, seed=1, session_factory=factory(sess))).queries

    assert any(q.topic == topic for q in planned)
    assert await mine(sess, topic) == []


async def test_a_second_run_never_repeats_the_first(sess, topic) -> None:
    await run_once(write=True, seed=1, session_factory=factory(sess))
    await run_once(write=True, seed=1, session_factory=factory(sess))

    texts = [r.url_or_query for r in await mine(sess, topic)]
    assert len(texts) == len(set(texts))


async def test_a_topic_with_a_backlog_of_queries_gets_none(sess, topic) -> None:
    for i in range(MAX_PENDING):
        sess.add(
            QueueTask(
                url_or_query=f"old query {i} {topic}",
                topic=topic,
                task_type="query",
                seed_source="diversity",
            )
        )
    await sess.flush()

    planned = (await run_once(write=True, seed=1, session_factory=factory(sess))).queries

    assert not any(q.topic == topic for q in planned)


async def test_only_approved_vocabulary_of_the_topic_is_used(sess, topic) -> None:
    approved = f"approvedphrase{uuid.uuid4().hex[:6]}"
    proposed = f"proposedphrase{uuid.uuid4().hex[:6]}"
    sess.add_all(
        [
            GazetteerTerm(
                canonical=approved, entity_type="concept", topic_labels=[topic], approved=True
            ),
            GazetteerTerm(
                canonical=proposed, entity_type="concept", topic_labels=[topic], approved=False
            ),
        ]
    )
    await sess.flush()

    planned = (
        await run_once(write=False, per_topic=200, seed=1, session_factory=factory(sess))
    ).queries

    texts = " ".join(q.text for q in planned if q.topic == topic)
    assert approved in texts
    assert proposed not in texts


async def test_a_paused_topic_gets_no_queries(sess, topic) -> None:
    row = await sess.get(TopicConfig, topic)
    row.status = "paused"
    await sess.flush()

    planned = (await run_once(write=False, seed=1, session_factory=factory(sess))).queries

    assert not any(q.topic == topic for q in planned)


async def test_a_topic_with_no_description_and_little_vocabulary_is_left_out(sess, topic) -> None:
    row = await sess.get(TopicConfig, topic)
    row.description = None
    await sess.flush()

    run = await run_once(write=True, seed=1, session_factory=factory(sess))

    assert topic in run.vague
    assert await mine(sess, topic) == []


async def test_an_agency_is_not_query_vocabulary(sess, topic) -> None:
    agency = f"Someplace Transit Authority {uuid.uuid4().hex[:4]}"
    sess.add(
        GazetteerTerm(canonical=agency, entity_type="agency", topic_labels=[topic], approved=True)
    )
    await sess.flush()

    planned = (
        await run_once(write=False, per_topic=200, seed=1, session_factory=factory(sess))
    ).queries

    assert not any(agency in q.text for q in planned)
