"""Watched news sitemaps and feeds are read again on their interval (task B-249).

Against a real Postgres, in a transaction rolled back at the end: the pass reads
`watches` and writes `queue` rows, and the table refuses what the code refuses.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from meridian_core.models import QueueTask, Watch
from meridian_core.models.queue import SEED_SOURCE
from worker.watch import WATCH_PRIORITY, WatchRefused, add_watch, is_due, run_pass

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)


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


def address() -> str:
    return f"https://w{uuid.uuid4().hex[:10]}.test/news-sitemap.xml"


async def a_topic(sess) -> str:
    topic = await sess.scalar(text("SELECT topic FROM topic_config LIMIT 1"))
    assert topic, "no topic is seeded; run `make seed`"
    return topic


async def reads(sess, url: str) -> list[QueueTask]:
    return list(
        await sess.scalars(
            select(QueueTask).where(QueueTask.url_or_query == url, QueueTask.task_type == "sitemap")
        )
    )


async def test_a_due_watch_is_queued_as_a_watch_read(sess) -> None:
    url = address()
    sess.add(Watch(url=url, topic=await a_topic(sess)))
    await sess.flush()

    stats = await run_pass(apply=True, now=NOW, session_factory=factory(sess))

    assert url in stats.queued
    (row,) = await reads(sess, url)
    assert (row.seed_source, row.priority) == ("watch", WATCH_PRIORITY)
    watch = await sess.scalar(select(Watch).where(Watch.url == url))
    assert watch.last_queued_at == NOW


async def test_a_watch_is_not_read_again_before_its_interval(sess) -> None:
    url = address()
    sess.add(Watch(url=url, topic=await a_topic(sess), interval_seconds=900))
    await sess.flush()
    await run_pass(apply=True, now=NOW, session_factory=factory(sess))
    await sess.execute(
        QueueTask.__table__.update().where(QueueTask.url_or_query == url).values(status="done")
    )

    await run_pass(apply=True, now=NOW + dt.timedelta(minutes=14), session_factory=factory(sess))
    assert len(await reads(sess, url)) == 1
    await run_pass(apply=True, now=NOW + dt.timedelta(minutes=15), session_factory=factory(sess))
    assert len(await reads(sess, url)) == 2


async def test_a_read_still_waiting_is_not_stacked(sess) -> None:
    """A crawl that is behind must not pile up reads of the same list."""
    url = address()
    sess.add(Watch(url=url, topic=await a_topic(sess), interval_seconds=300))
    await sess.flush()
    await run_pass(apply=True, now=NOW, session_factory=factory(sess))

    stats = await run_pass(
        apply=True, now=NOW + dt.timedelta(hours=1), session_factory=factory(sess)
    )
    assert stats.in_flight >= 1
    assert len(await reads(sess, url)) == 1


async def test_a_disabled_watch_is_not_read(sess) -> None:
    url = address()
    sess.add(Watch(url=url, topic=await a_topic(sess), enabled=False))
    await sess.flush()
    stats = await run_pass(apply=True, now=NOW, session_factory=factory(sess))
    assert url not in stats.queued and await reads(sess, url) == []


async def test_without_apply_nothing_is_written(sess) -> None:
    url = address()
    sess.add(Watch(url=url, topic=await a_topic(sess)))
    await sess.flush()
    stats = await run_pass(apply=False, now=NOW, session_factory=factory(sess))
    assert url in stats.queued and await reads(sess, url) == []


# --------------------------------------------------------------------------
# What is refused, by the code and by the table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"url": "ftp://x.test/feed"}, "http"),
        ({"url": "not a url"}, "http"),
        ({"interval_seconds": 60}, "interval"),
        ({"topic": "no-such-topic-anywhere"}, "no topic"),
        ({"topic": None, "headline_filter": False}, "headline"),
    ],
)
async def test_add_refuses_by_name(sess, kwargs, message) -> None:
    fields = {"url": address(), "topic": await a_topic(sess), **kwargs}
    url = fields.pop("url")
    with pytest.raises(WatchRefused, match=message):
        await add_watch(sess, url, **fields)


async def test_add_refuses_an_address_already_watched(sess) -> None:
    url, topic = address(), await a_topic(sess)
    await add_watch(sess, url, topic=topic)
    with pytest.raises(WatchRefused, match="already"):
        await add_watch(sess, url, topic=topic)


@pytest.mark.parametrize(
    ("kind", "topic", "headline_filter", "interval"),
    [
        ("sitemap", None, True, 299),
        ("sitemap", None, False, 900),
        ("page", None, True, 900),
    ],
)
async def test_the_table_refuses_what_the_code_refuses(
    sess, kind, topic, headline_filter, interval
) -> None:
    """A row written around `add_watch` and the ORM (SQL, a future Admin form) is held
    to the same rules, by the table's own constraints."""
    with pytest.raises(IntegrityError):
        await sess.execute(
            text(
                "INSERT INTO watches (url, kind, topic, headline_filter, interval_seconds) "
                "VALUES (:url, :kind, :topic, :headline_filter, :interval)"
            ),
            {
                "url": address(),
                "kind": kind,
                "topic": topic,
                "headline_filter": headline_filter,
                "interval": interval,
            },
        )


async def test_watch_is_a_seed_source_in_every_column_that_holds_one(sess) -> None:
    """Drift: the model's value set against each CHECK in the database."""
    for table in ("queue", "fetch_policy"):
        definition = await sess.scalar(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": f"ck_{table}_seed_source"},
        )
        for value in SEED_SOURCE.enums:
            assert f"'{value}'" in definition, (table, value)


def test_due_reads_the_interval() -> None:
    watch = Watch(url="https://x.test/s", enabled=True, interval_seconds=600, last_queued_at=NOW)
    assert not is_due(watch, NOW + dt.timedelta(seconds=599))
    assert is_due(watch, NOW + dt.timedelta(seconds=600))
    watch.last_queued_at = None
    assert is_due(watch, NOW)
    watch.enabled = False
    assert not is_due(watch, NOW)


async def test_the_table_takes_a_listing_watch_and_add_watch_refuses_an_unknown_kind(sess):
    """`B-255`: the third kind is in the CHECK, and a kind outside it is refused by name
    before the insert rather than as a constraint error."""
    url = address().replace("news-sitemap.xml", "latest")
    watch = await add_watch(sess, url, kind="listing", headline_filter=True)
    assert watch.kind == "listing"

    with pytest.raises(WatchRefused, match="no watch kind 'page'"):
        await add_watch(sess, address(), kind="page", headline_filter=True)
