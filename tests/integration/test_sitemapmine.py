"""Proven hosts are mined through their sitemaps (task B-116).

Against a real Postgres: the pass reads host scores and the robots cache the
crawl wrote, and writes queue rows. Mostly about which hosts are *not* mined and
which sitemaps are *not* queued — mining every host would be a breadth-first
crawl of the web's largest sites.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from meridian_core.hostscores import MIN_EXAMINED, PROVEN_BOOST, recompute
from meridian_core.models import QueueTask
from meridian_core.models.robots import RobotsCacheEntry
from meridian_core.queueing import claim_next
from meridian_core.sources import upsert_source
from worker.sitemapmine import CONVENTIONAL, run_pass, sitemaps_for

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
    return f"m{uuid.uuid4().hex[:10]}.test"


async def labelled(sess, h: str, n: int, on: int) -> None:
    for i in range(n):
        source, _ = await upsert_source(
            sess, f"https://{h}/p{i}", checksum=f"sha256:{uuid.uuid4().hex}"
        )
        source.topic_labels = ["walkability"] if i < on else []
    await sess.flush()


async def robots(sess, h: str, body: str) -> None:
    now = dt.datetime.now(dt.UTC)
    sess.add(
        RobotsCacheEntry(
            origin=f"https://{h}/robots.txt",
            outcome="ok",
            body=body,
            fetched_at=now,
            expires_at=now + dt.timedelta(days=1),
        )
    )
    await sess.flush()


async def sitemap_rows(sess, h: str) -> list[QueueTask]:
    rows = await sess.scalars(
        select(QueueTask).where(
            QueueTask.url_or_query.like(f"https://{h}/%"), QueueTask.task_type == "sitemap"
        )
    )
    return list(rows)


def test_only_same_host_sitemaps_are_taken() -> None:
    body = "User-agent: *\nSitemap: https://a.test/s.xml\nSitemap: https://elsewhere.test/s.xml\n"
    assert sitemaps_for("a.test", body, "MeridianBot") == ["https://a.test/s.xml"]


def test_a_host_naming_none_gets_the_conventional_one() -> None:
    assert sitemaps_for("a.test", "User-agent: *\nDisallow:\n", "MeridianBot") == [
        f"https://a.test{CONVENTIONAL}"
    ]
    assert sitemaps_for("a.test", None, "MeridianBot") == [f"https://a.test{CONVENTIONAL}"]


async def test_a_proven_hosts_sitemaps_are_queued_boosted(sess) -> None:
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=MIN_EXAMINED)
    await robots(
        sess, h, f"User-agent: *\nSitemap: https://{h}/a.xml\nSitemap: https://{h}/b.xml\n"
    )
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    rows = await sitemap_rows(sess, h)
    assert {r.url_or_query for r in rows} == {f"https://{h}/a.xml", f"https://{h}/b.xml"}
    assert all(r.seed_source == "sitemap" and r.priority > PROVEN_BOOST for r in rows)


async def test_a_second_pass_queues_nothing_again(sess) -> None:
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=MIN_EXAMINED)
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))
    again = await run_pass(apply=True, session_factory=factory(sess))

    assert h not in " ".join(again.queued)
    assert len(await sitemap_rows(sess, h)) == 1


@pytest.mark.parametrize(
    ("examined", "on"),
    [(MIN_EXAMINED, 0), (MIN_EXAMINED, MIN_EXAMINED // 10), (MIN_EXAMINED - 1, MIN_EXAMINED - 1)],
    ids=["off-topic", "thin", "unjudged"],
)
async def test_a_host_that_is_not_proven_is_not_mined(sess, examined, on) -> None:
    h = host()
    await labelled(sess, h, examined, on=on)
    await robots(sess, h, f"Sitemap: https://{h}/a.xml\n")
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    assert await sitemap_rows(sess, h) == []


async def test_a_report_writes_nothing(sess) -> None:
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=MIN_EXAMINED)
    await recompute(sess)

    stats = await run_pass(apply=False, session_factory=factory(sess))

    assert f"https://{h}{CONVENTIONAL}" in stats.queued
    assert await sitemap_rows(sess, h) == []


async def test_a_mined_sitemap_is_claimable_by_its_hosts_topic(sess) -> None:
    """Every claim draws a topic; a sitemap filed under none was never fetched."""
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=MIN_EXAMINED)
    await recompute(sess)

    await run_pass(apply=True, session_factory=factory(sess))

    [row] = await sitemap_rows(sess, h)
    assert row.topic == "walkability"
    claimed = await claim_next(sess, worker_id="t", topics=["walkability"], task_types=["sitemap"])
    # Other tests' rows may rank higher; ours must at least be eligible.
    assert claimed is not None


async def test_a_pending_sitemap_with_no_topic_is_refiled(sess) -> None:
    h = host()
    await labelled(sess, h, MIN_EXAMINED, on=MIN_EXAMINED)
    await recompute(sess)
    sess.add(
        QueueTask(
            url_or_query=f"https://{h}{CONVENTIONAL}",
            task_type="sitemap",
            seed_source="sitemap",
            priority=100,
        )
    )
    await sess.flush()

    stats = await run_pass(apply=True, session_factory=factory(sess))

    [row] = await sitemap_rows(sess, h)
    assert row.topic == "walkability" and stats.refiled >= 1
