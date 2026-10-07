"""Links follow the evidence: on-topic pages lift what they link to (task B-150).

Measured on the live corpus before this was built: a link from an on-topic page landed on an
on-topic page about half the time, against roughly one in ten from a page about nothing, and
a host that an on-topic page elsewhere linked to went on to be on a topic several times as
often as one nobody on-topic linked to. Against Postgres, because each rule is a query over
the queue, the sources and the vouches together.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from meridian_core.hostscores import (
    MAX_VOUCHES_PER_PAGE,
    MIN_EXAMINED,
    HostPolicy,
    Score,
    record_vouches,
    vouches_by_host,
)
from meridian_core.models import LinkVouch, QueueTask
from meridian_core.sources import upsert_source
from worker import requeue_links

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


def a_host() -> str:
    return f"h{uuid.uuid4().hex[:10]}.test"


async def page(sess, host: str, labels):
    source, _ = await upsert_source(
        sess,
        f"https://{host}/{uuid.uuid4().hex[:6]}",
        checksum=f"sha256:{uuid.uuid4().hex}",
    )
    source.topic_labels = labels
    await sess.flush()
    return source


async def link(sess, url: str, *, parent=None, priority=13, seed="frontier"):
    task = QueueTask(
        url_or_query=url,
        task_type="url",
        seed_source=seed,
        status="pending",
        priority=priority,
        parent_source_id=parent.source_id if parent is not None else None,
    )
    sess.add(task)
    await sess.flush()
    return task


async def priority_of(sess, task) -> int:
    return await sess.scalar(select(QueueTask.priority).where(QueueTask.task_id == task.task_id))


# --------------------------------------------------------------------------
# Recording


async def test_a_page_vouches_once_for_each_other_host_it_links_to(sess) -> None:
    home, there = a_host(), a_host()
    source = await page(sess, home, None)
    links = [
        f"https://{there}/a",
        f"https://www.{there}/b",  # the same host to everything else
        f"https://{home}/c",  # its own host vouches for nothing
        "mailto:someone@example.org",  # no host
    ]

    assert await record_vouches(sess, source.source_id, source.url, links) == 1
    assert await record_vouches(sess, source.source_id, source.url, links) == 1  # idempotent

    rows = (
        await sess.scalars(select(LinkVouch).where(LinkVouch.source_id == source.source_id))
    ).all()
    assert [r.host for r in rows] == [there]


async def test_a_link_farm_is_not_more_evidence(sess) -> None:
    source = await page(sess, a_host(), None)
    links = [f"https://{a_host()}/" for _ in range(MAX_VOUCHES_PER_PAGE + 50)]
    assert await record_vouches(sess, source.source_id, source.url, links) == MAX_VOUCHES_PER_PAGE


async def test_only_on_topic_pages_vouch_and_one_site_is_one_voice(sess) -> None:
    target, site, other = a_host(), a_host(), a_host()
    for labels in (["walkability"], ["walkability"]):  # two pages, one site
        p = await page(sess, site, labels)
        await record_vouches(sess, p.source_id, p.url, [f"https://{target}/"])
    p = await page(sess, other, ["robotics"])
    await record_vouches(sess, p.source_id, p.url, [f"https://{target}/"])
    for labels in ([], None):  # read and about nothing; never read
        p = await page(sess, a_host(), labels)
        await record_vouches(sess, p.source_id, p.url, [f"https://{target}/"])

    assert (await vouches_by_host(sess))[target] == 2


# --------------------------------------------------------------------------
# Raising


async def test_a_link_from_an_on_topic_page_rises_to_the_proven_band(sess) -> None:
    parent = await page(sess, a_host(), ["walkability"])
    child = await link(sess, f"https://{a_host()}/x", parent=parent)
    already_high = await link(sess, f"https://{a_host()}/y", parent=parent, priority=90)
    from_nothing = await link(sess, f"https://{a_host()}/z", parent=await page(sess, a_host(), []))

    children, _ = await requeue_links.promotable(sess, HostPolicy({}))
    await requeue_links._raise(sess, children, requeue_links.PROMOTED_PRIORITY)

    assert await priority_of(sess, child) == requeue_links.PROMOTED_PRIORITY
    assert await priority_of(sess, already_high) == 90, "a raise never lowers"
    assert await priority_of(sess, from_nothing) == 13


async def test_a_host_judged_off_topic_keeps_its_links_down(sess) -> None:
    """The host's own record outweighs one page that linked to it."""
    off = a_host()
    parent = await page(sess, a_host(), ["walkability"])
    child = await link(sess, f"https://{off}/x", parent=parent)
    policy = HostPolicy({off: Score(MIN_EXAMINED, 0)})

    children, vouched = await requeue_links.promotable(sess, policy)

    assert child.task_id not in children and child.task_id not in vouched


async def test_a_link_into_a_vouched_host_rises_even_from_an_unread_page(sess) -> None:
    host = a_host()
    task = await link(sess, f"https://{host}/x", parent=await page(sess, a_host(), None))
    plain = await link(sess, f"https://{a_host()}/x", parent=await page(sess, a_host(), None))
    policy = HostPolicy({host: Score(vouched=1)})

    _, vouched = await requeue_links.promotable(sess, policy)
    await requeue_links._raise(sess, vouched, requeue_links.VOUCHED_PRIORITY)

    assert await priority_of(sess, task) == requeue_links.VOUCHED_PRIORITY
    assert await priority_of(sess, plain) == 13


async def test_raising_twice_moves_nothing_the_second_time(sess) -> None:
    parent = await page(sess, a_host(), ["walkability"])
    await link(sess, f"https://{a_host()}/x", parent=parent)
    children, _ = await requeue_links.promotable(sess, HostPolicy({}))
    assert await requeue_links._raise(sess, children, requeue_links.PROMOTED_PRIORITY) >= 1
    assert await requeue_links._raise(sess, children, requeue_links.PROMOTED_PRIORITY) == 0


async def test_a_search_result_or_lookup_is_not_re_ranked(sess) -> None:
    parent = await page(sess, a_host(), ["walkability"])
    result = await link(sess, f"https://{a_host()}/x", parent=parent, seed="search")
    children, vouched = await requeue_links.promotable(sess, HostPolicy({}))
    assert result.task_id not in children + vouched
