"""How the corpus grew (task `B-140`, ADRs 0005, 0009, 0010).

Pinned to January 2001, which no other data in a test database reaches, so every count here
is of rows these tests write.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid

import pytest
from sqlalchemy import select, update

from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.growth import growth, split_labels, window_start
from meridian_core.models import AreaBuildHistory, Chunk, ChunkTopics, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

ZONE = "Asia/Singapore"
NOW = dt.datetime(2001, 1, 10, 4, 0, tzinfo=dt.UTC)  # 12:00 on the 10th in Singapore


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


@pytest.fixture
def topics() -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    return f"growth-a-{tag}", f"growth-b-{tag}"


async def page(
    sess,
    at: dt.datetime,
    labels: list[str],
    *,
    host: str | None = None,
    junk: bool = False,
    passages: int = 0,
) -> Source:
    host = host or f"g{uuid.uuid4().hex[:10]}.test"
    source, _ = await upsert_source(
        sess, f"https://{host}/{uuid.uuid4().hex}", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await sess.execute(
        update(Source)
        .where(Source.source_id == source.source_id)
        .values(
            created_at=at,
            topic_labels=labels,
            retention_tier="junk" if junk else "background",
        )
    )
    if passages:
        await replace_chunks(
            sess,
            source.source_id,
            [
                ChunkWrite(text=f"Passage {i} {uuid.uuid4().hex}.", chunk_index=i)
                for i in range(passages)
            ],
        )
        ids = list(
            await sess.scalars(select(Chunk.chunk_id).where(Chunk.source_id == source.source_id))
        )
        await sess.execute(update(Chunk).where(Chunk.chunk_id.in_(ids)).values(created_at=at))
        for chunk_id in ids:
            sess.add(
                ChunkTopics(
                    chunk_id=chunk_id, topic_labels=labels, topic_scores={}, topic_basis="t"
                )
            )
    await sess.flush()
    return source


def utc(day: int, hour: int) -> dt.datetime:
    return dt.datetime(2001, 1, day, hour, tzinfo=dt.UTC)


# --------------------------------------------------------------------------
# Pure rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "shown", "bucket"),
    [
        (["a"], None, "a"),
        (["a", "b"], None, "multi"),
        (["a", "b"], {"a"}, "a"),  # a filter to one topic counts the page for it
        (["c"], {"a"}, None),
        ([], None, None),
    ],
)
def test_a_page_falls_in_one_bucket(labels, shown, bucket) -> None:
    assert split_labels(labels, shown) == bucket


def test_the_window_starts_at_local_midnight() -> None:
    first, start = window_start(NOW, ZONE, 7)
    assert first == dt.date(2001, 1, 4)
    assert start == dt.datetime(2001, 1, 3, 16, tzinfo=dt.UTC)
    assert window_start(NOW, ZONE, None) == (None, None)


# --------------------------------------------------------------------------
# Against the database
# --------------------------------------------------------------------------


async def test_days_are_calendar_days_in_the_display_zone(sess, topics) -> None:
    a, _ = topics
    await page(sess, utc(8, 18), [a])  # 02:00 on the 9th in Singapore, the 8th in UTC
    here = await growth(sess, zone=ZONE, days=7, topics=[a], now=NOW)
    there = await growth(sess, zone="UTC", days=7, topics=[a], now=NOW)

    def counted(result) -> dict[dt.date, int]:
        return {d.day: d.by_topic.get(a, 0) for d in result.daily if d.by_topic}

    assert counted(here) == {dt.date(2001, 1, 9): 1}
    assert counted(there) == {dt.date(2001, 1, 8): 1}


async def test_a_page_about_two_shown_topics_counts_once(sess, topics) -> None:
    a, b = topics
    await page(sess, utc(5, 4), [a, b])
    both = await growth(sess, zone=ZONE, days=7, topics=[a, b], now=NOW)
    one = await growth(sess, zone=ZONE, days=7, topics=[a], now=NOW)
    day = dt.date(2001, 1, 5)
    assert next(d for d in both.daily if d.day == day).multi == 1
    assert next(d for d in one.daily if d.day == day).by_topic == {a: 1}


async def test_junk_and_copies_are_not_growth(sess, topics) -> None:
    a, _ = topics
    original = await page(sess, utc(6, 4), [a])
    await page(sess, utc(6, 5), [a], junk=True)
    copy = await page(sess, utc(6, 6), [a])
    await sess.execute(
        update(Source)
        .where(Source.source_id == copy.source_id)
        .values(duplicate_of=original.source_id, duplicate_reason="exact")
    )
    result = await growth(sess, zone=ZONE, days=7, topics=[a], now=NOW)
    assert sum(d.by_topic.get(a, 0) for d in result.daily) == 1
    assert result.sources.total == 1


async def test_every_day_of_the_window_is_there_and_quiet_days_say_so(sess, topics) -> None:
    a, _ = topics
    await page(sess, utc(7, 4), [a])
    result = await growth(sess, zone=ZONE, days=7, topics=[a], now=NOW)
    assert [d.day for d in result.daily] == [dt.date(2001, 1, n) for n in range(4, 11)]
    crawled = {d.day for d in result.daily if d.crawled}
    assert crawled == {dt.date(2001, 1, 7)}, "a day with no fetch is a gap, not a zero"


async def test_nothing_after_now_is_counted(sess, topics) -> None:
    a, _ = topics
    await page(sess, utc(9, 4), [a])
    await page(sess, utc(20, 4), [a])  # after NOW
    result = await growth(sess, zone=ZONE, days=7, topics=[a], now=NOW)
    assert result.sources.total == 1


async def test_a_site_is_new_on_the_day_its_first_page_arrived(sess, topics) -> None:
    a, _ = topics
    host = f"site{uuid.uuid4().hex[:8]}.test"
    await page(sess, utc(5, 4), [a], host=host)
    await page(sess, utc(8, 4), [a], host=host)
    result = await growth(sess, zone=ZONE, days=7, topics=[a], now=NOW)
    by_day = {d.day: d.new_sites for d in result.daily}
    assert by_day[dt.date(2001, 1, 5)] >= 1
    assert by_day[dt.date(2001, 1, 8)] == 0


async def test_passages_are_counted_per_topic_and_in_the_window(sess, topics) -> None:
    a, b = topics
    await page(sess, utc(1, 4), [a], passages=3)  # before the 7-day window
    await page(sess, utc(8, 4), [a, b], passages=2)
    result = await growth(sess, zone=ZONE, days=7, topics=[a, b], now=NOW)
    rows = {t.topic: t for t in result.all_topics}
    assert rows[a].passages.total == 5 and rows[a].passages.in_window == 2
    assert rows[b].passages.total == 2
    assert result.passages.total == 5, "a passage about both counts once in the headline"


async def test_all_time_counts_everything_as_in_the_window(sess, topics) -> None:
    a, _ = topics
    await page(sess, utc(1, 4), [a], passages=1)
    result = await growth(sess, zone=ZONE, days=None, topics=[a], now=NOW)
    assert result.sources.in_window == result.sources.total == 1
    assert result.daily[0].day == dt.date(2001, 1, 1)


async def test_the_map_history_is_read_inside_the_window(sess) -> None:
    for i, when in enumerate([utc(1, 4), utc(6, 4), utc(9, 4)]):
        sess.add(
            AreaBuildHistory(
                build_id=-(1000 + i),
                computed_at=when,
                passages=100 * (i + 1),
                regions=3,
                areas=10 + i,
                sub_areas=40,
                weak_areas=i,
            )
        )
    await sess.flush()
    result = await growth(sess, zone=ZONE, days=7, now=NOW)
    assert [m.areas for m in result.map_history] == [11, 12]
    assert result.map_now.areas == 12


def test_the_history_backfill_uses_the_weak_rule() -> None:
    """The migration's literal must be the read path's rule, or old builds count differently."""
    from meridian_core.areaview import WEAK_BELOW_SOURCES

    migration = next(
        pathlib.Path(__file__)
        .resolve()
        .parents[2]
        .glob("migrations/versions/*map_build_history.py")
    )
    assert f"a.sources < {WEAK_BELOW_SOURCES}" in migration.read_text()
