"""Host scores from labels, and the requeue pass (task B-48)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.hostscores import MIN_EXAMINED, Standing, load, recompute
from meridian_core.models import QueueTask
from meridian_core.sources import upsert_source
from worker.requeue import HELD_PRIORITY, run_pass

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


def host() -> str:
    return f"h{uuid.uuid4().hex[:10]}.test"


async def labelled(sess, h: str, n: int, on: int) -> None:
    for i in range(n):
        source, _ = await upsert_source(
            sess, f"https://www.{h}/p{i}", checksum=f"sha256:{uuid.uuid4().hex}"
        )
        source.topic_labels = ["walkability"] if i < on else []
    await sess.flush()


async def queued(sess, h: str, n: int, *, seed_source: str = "frontier", priority: int = 60):
    for i in range(n):
        sess.add(
            QueueTask(
                url_or_query=f"https://{h}/q{i}-{uuid.uuid4().hex[:6]}",
                seed_source=seed_source,
                priority=priority,
                topic="walkability",
            )
        )
    await sess.flush()


async def test_scores_count_examined_and_on_topic_by_host_with_www_folded(sess) -> None:
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=3)
    await queued(sess, h, 4)

    await recompute(sess)
    score = (await load(sess))[h]

    assert (score.examined, score.on_topic, score.pending) == (MIN_EXAMINED, 3, 4)
    assert score.standing is Standing.ON_TOPIC


async def test_an_unlabelled_source_does_not_count_as_examined(sess) -> None:
    h = host()
    source, _ = await upsert_source(sess, f"https://{h}/x", checksum="sha256:u")
    assert source.topic_labels is None
    await recompute(sess)

    assert h not in await load(sess)


async def test_requeue_holds_an_offtopic_hosts_links_and_keeps_an_on_topic_hosts(sess) -> None:
    off, on = host(), host()
    await labelled(sess, off, MIN_EXAMINED, on=0)
    await labelled(sess, on, MIN_EXAMINED, on=MIN_EXAMINED)
    await queued(sess, off, 3)
    await queued(sess, on, 3)
    await recompute(sess)

    stats = await run_pass(apply=True, session_factory=factory(sess))

    rows = (
        await sess.execute(
            select(QueueTask.url_or_query, QueueTask.priority).where(
                QueueTask.url_or_query.like(f"https://{off}/%")
                | QueueTask.url_or_query.like(f"https://{on}/%")
            )
        )
    ).all()
    assert {p for u, p in rows if off in u} == {HELD_PRIORITY}
    # Kept, at the priority its tier is worth now — not held.
    kept = {p for u, p in rows if on in u}
    assert len(kept) == 1 and HELD_PRIORITY not in kept
    assert stats.held >= 3


async def test_requeue_leaves_search_and_user_rows_alone(sess) -> None:
    off = host()
    await labelled(sess, off, MIN_EXAMINED, on=0)
    await queued(sess, off, 2, seed_source="search")
    await queued(sess, off, 1, seed_source="user", priority=100)
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    priorities = set(
        await sess.scalars(
            select(QueueTask.priority).where(QueueTask.url_or_query.like(f"https://{off}/%"))
        )
    )
    assert priorities == {60, 100}


async def test_a_report_changes_no_priority(sess) -> None:
    off = host()
    await labelled(sess, off, MIN_EXAMINED, on=0)
    await queued(sess, off, 2)
    await recompute(sess)

    await run_pass(apply=False, session_factory=factory(sess))

    priorities = set(
        await sess.scalars(
            select(QueueTask.priority).where(QueueTask.url_or_query.like(f"https://{off}/%"))
        )
    )
    assert priorities == {60}


async def test_requeue_deletes_nothing(sess) -> None:
    off = host()
    await labelled(sess, off, MIN_EXAMINED, on=0)
    await queued(sess, off, 5)
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    count = len(
        list(
            await sess.scalars(
                select(QueueTask.task_id).where(QueueTask.url_or_query.like(f"https://{off}/%"))
            )
        )
    )
    assert count == 5


async def test_requeue_brings_a_kept_link_to_its_current_tier_priority(sess) -> None:
    """`B-50` changed what a tier is worth; a link queued before must follow."""
    from meridian_core.ageing import HALF_LIFE_DAYS
    from meridian_core.policy import source_tier_map
    from meridian_core.tiering import priority_with_urgency

    on = host()
    await labelled(sess, on, 20, on=20)
    await queued(sess, on, 1, priority=97)
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    url, priority = (
        await sess.execute(
            select(QueueTask.url_or_query, QueueTask.priority).where(
                QueueTask.url_or_query.like(f"https://{on}/%")
            )
        )
    ).one()
    assert priority == priority_with_urgency(url, await source_tier_map(sess), HALF_LIFE_DAYS)
