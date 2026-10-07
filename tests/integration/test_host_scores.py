"""Host scores from labels, and the requeue pass (task B-48)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.hostscores import MIN_EXAMINED, PROVEN_BOOST, Standing, load, recompute
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


async def labelled(sess, h: str, n: int, on: int, *, reached_by: str | None = None) -> None:
    """``n`` examined pages, ``on`` of them on a topic; ``reached_by`` records how each came."""
    for i in range(n):
        url = f"https://www.{h}/p{i}"
        source, _ = await upsert_source(sess, url, checksum=f"sha256:{uuid.uuid4().hex}")
        source.topic_labels = ["walkability"] if i < on else []
        if reached_by is not None:
            sess.add(
                QueueTask(
                    url_or_query=url, seed_source=reached_by, status="fetched", topic="walkability"
                )
            )
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


async def test_pages_reached_by_following_are_counted_apart_from_searched_ones(sess) -> None:
    """`B-155`: a host proven on what a search picked says little about its own links."""
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=MIN_EXAMINED, reached_by="search")
    await recompute(sess)
    searched = (await load(sess))[h]
    assert (searched.examined, searched.followed_examined) == (MIN_EXAMINED, 0)
    assert not searched.has_followed_record

    other = host()
    await labelled(sess, other, MIN_EXAMINED, on=5, reached_by="frontier")
    await recompute(sess)
    followed = (await load(sess))[other]
    assert (followed.followed_examined, followed.followed_on_topic) == (MIN_EXAMINED, 5)


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
    await labelled(sess, on, 20, on=20, reached_by="frontier")
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
    # Fully on a topic, so proven (`B-115`): its tier priority plus the boost.
    tier = priority_with_urgency(url, await source_tier_map(sess), HALF_LIFE_DAYS)
    assert priority == tier + PROVEN_BOOST


async def test_requeue_lifts_a_proven_hosts_waiting_links_above_an_unjudged_hosts(sess) -> None:
    """`B-115`, applied to the backlog: queued before the host was proven, fetched first after."""
    proven, unjudged = host(), host()
    await labelled(sess, proven, 20, on=20, reached_by="frontier")
    await queued(sess, proven, 1, priority=1)
    await queued(sess, unjudged, 1, priority=60)
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    rows = dict(
        (
            await sess.execute(
                select(QueueTask.url_or_query, QueueTask.priority).where(
                    QueueTask.url_or_query.like(f"https://{proven}/%")
                    | QueueTask.url_or_query.like(f"https://{unjudged}/%")
                )
            )
        ).all()
    )
    by_host = {u.split("/")[2]: p for u, p in rows.items()}
    assert by_host[proven] > by_host[unjudged]


async def test_requeue_reads_the_whole_score_not_just_the_counts(sess) -> None:
    """It rebuilt each score from its examined and on-topic counts only, so a vouched-for host
    (`B-150`) and a host proven by following (`B-155`) both lost their standing whenever it ran.
    """
    vouched, unjudged = host(), host()
    for h in (vouched, unjudged):
        await queued(sess, h, 1, priority=13)
    await recompute(sess)
    # One on-topic page elsewhere links to it.
    page, _ = await upsert_source(
        sess, f"https://{host()}/x", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    page.topic_labels = ["walkability"]
    await sess.flush()
    from meridian_core.hostscores import record_vouches

    await record_vouches(sess, page.source_id, page.url, [f"https://{vouched}/"])
    await recompute(sess)
    assert (await load(sess))[vouched].is_vouched

    await run_pass(apply=True, session_factory=factory(sess))

    rows = dict(
        (
            await sess.execute(
                select(QueueTask.url_or_query, QueueTask.priority).where(
                    QueueTask.url_or_query.like(f"https://{vouched}/%")
                    | QueueTask.url_or_query.like(f"https://{unjudged}/%")
                )
            )
        ).all()
    )
    by_host = {u.split("/")[2]: p for u, p in rows.items()}
    assert by_host[vouched] > by_host[unjudged]
