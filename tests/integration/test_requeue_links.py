"""Followed links wait behind their page's verdict (task B-92).

Against Postgres, because the rule is a query over two tables: a pending link
moves only when the page that carried it was read and found about none of the
topics. Each other case — a page about a topic, a page never read, a link
already claimed, a search result, a lookup — is one this must leave alone.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from meridian_core.models import QueueTask
from meridian_core.sources import upsert_source
from worker import requeue_links

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def page(sess, labels):
    source, _ = await upsert_source(
        sess, f"https://p{uuid.uuid4().hex[:10]}.test/", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    source.topic_labels = labels
    await sess.flush()
    return source


async def link(sess, parent, *, status="pending", seed="frontier", kind="url", priority=40):
    task = QueueTask(
        url_or_query=f"https://c{uuid.uuid4().hex[:10]}.test/x",
        task_type=kind,
        seed_source=seed,
        status=status,
        priority=priority,
        parent_source_id=parent.source_id,
    )
    sess.add(task)
    await sess.flush()
    return task


async def priority_of(sess, task) -> int:
    return await sess.scalar(select(QueueTask.priority).where(QueueTask.task_id == task.task_id))


async def test_a_link_from_a_page_about_nothing_waits_at_the_floor(sess) -> None:
    moved = await link(sess, await page(sess, []))
    kept = await link(sess, await page(sess, ["walkability"]))
    unread = await link(sess, await page(sess, None))

    before = await requeue_links.count_demotable(sess)
    assert before >= 1
    await requeue_links.demote(sess)

    assert await priority_of(sess, moved) == requeue_links.DEMOTED_PRIORITY
    assert await priority_of(sess, kept) == 40, "a link from an on-topic page moved"
    assert await priority_of(sess, unread) == 40, "a page nobody has read was judged"


@pytest.mark.parametrize(
    ("status", "seed", "kind"),
    [
        ("done", "frontier", "url"),  # answered; re-ranking it does nothing
        ("pending", "search", "url"),  # somebody asked for it
        ("pending", "frontier", "doi"),  # a lookup, ranked by requeue_dois
    ],
)
async def test_what_is_not_a_pending_followed_page_is_left_alone(sess, status, seed, kind) -> None:
    task = await link(sess, await page(sess, []), status=status, seed=seed, kind=kind)
    await requeue_links.demote(sess)
    assert await priority_of(sess, task) == 40


async def test_it_only_ever_moves_down_and_a_second_pass_moves_nothing(sess) -> None:
    parent = await page(sess, [])
    low = await link(sess, parent, priority=-5)  # already below the floor: stays
    high = await link(sess, parent, priority=50)
    await requeue_links.demote(sess)
    assert await priority_of(sess, low) == -5
    assert await priority_of(sess, high) == requeue_links.DEMOTED_PRIORITY
    assert await requeue_links.count_demotable(sess) == 0
    assert await requeue_links.demote(sess) == 0
