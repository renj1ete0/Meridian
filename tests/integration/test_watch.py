"""Watched questions against Postgres (task P6-43): what is new for a saved view.

The count must agree with how search matches words, start from when the view
was last opened, respect its topic filter, and leave out what search leaves out
— junk and duplicates. Each rule has a case where it is what excludes a source.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import update

from meridian_core import watch
from meridian_core.chunks import ChunkWrite, replace_chunks
from meridian_core.models import SavedView, Source
from meridian_core.sources import upsert_source

pytestmark = pytest.mark.usefixtures("require_db")

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


@pytest.fixture
def word() -> str:
    """A word nothing else in the corpus contains."""
    return f"zq{uuid.uuid4().hex[:10]}"


async def source(
    sess,
    text: str,
    *,
    at: dt.datetime,
    topics=None,
    junk=False,
    places=None,
    tier=None,
    published=None,
) -> Source:
    row, _ = await upsert_source(
        sess, f"https://w{uuid.uuid4().hex[:10]}.test/p", checksum=f"sha256:{uuid.uuid4().hex}"
    )
    await replace_chunks(sess, row.source_id, [ChunkWrite(text=text, chunk_index=0)])
    await sess.execute(
        update(Source)
        .where(Source.source_id == row.source_id)
        .values(
            created_at=at,
            topic_labels=topics,
            retention_tier="junk" if junk else row.retention_tier,
            places=places,
            source_tier=tier or row.source_tier,
            publication_date=published,
        )
    )
    await sess.flush()
    return row


def view(query=None, topics=None, opened=None, filters=None, focus=None) -> SavedView:
    """A view stored as the interface stores it: `SearchFilters`' names (`B-73`, `B-194`)."""
    stored = dict(filters or {})
    if topics:
        stored["topics"] = topics
    return SavedView(
        name=f"v{uuid.uuid4().hex[:8]}",
        query=query,
        filters=stored,
        focus_entity_id=focus,
        last_opened_at=opened,
        created_at=T0,
    )


async def test_counts_what_matches_since_the_view_was_last_opened(sess, word) -> None:
    opened = T0 + dt.timedelta(days=5)
    await source(sess, f"old passage about {word}", at=opened - dt.timedelta(days=1))
    await source(sess, f"new passage about {word}", at=opened + dt.timedelta(days=1))
    await source(sess, "new passage about something else", at=opened + dt.timedelta(days=1))

    assert await watch.new_for_view(sess, view(query=word, opened=opened)) == 1


async def test_a_view_never_opened_counts_from_when_it_was_saved(sess, word) -> None:
    await source(sess, f"about {word}", at=T0 - dt.timedelta(days=1))
    await source(sess, f"about {word}", at=T0 + dt.timedelta(days=1))
    assert await watch.new_for_view(sess, view(query=word)) == 1


async def test_junk_and_duplicates_are_not_new_evidence(sess, word) -> None:
    after = T0 + dt.timedelta(days=1)
    kept = await source(sess, f"about {word}", at=after)
    await source(sess, f"about {word}", at=after, junk=True)
    copy = await source(sess, f"about {word}", at=after)
    await sess.execute(
        update(Source)
        .where(Source.source_id == copy.source_id)
        .values(duplicate_of=kept.source_id, duplicate_reason="test copy")
    )
    assert await watch.new_for_view(sess, view(query=word)) == 1


async def test_the_topic_filter_narrows_as_the_view_does(sess, word) -> None:
    topic = f"t{uuid.uuid4().hex[:6]}"
    after = T0 + dt.timedelta(days=1)
    await source(sess, f"about {word}", at=after, topics=[topic])
    await source(sess, f"about {word}", at=after, topics=["other"])
    await source(sess, f"about {word}", at=after, topics=None)

    assert await watch.new_for_view(sess, view(query=word, topics=[topic])) == 1
    # A topic-only view counts the topic's new sources, words or not.
    assert await watch.new_for_view(sess, view(topics=[topic])) >= 1


async def test_a_view_that_asks_nothing_countable_is_not_counted(sess) -> None:
    """None, not 0: "nothing new" would be a claim about a question never asked."""
    assert await watch.new_for_view(sess, view(query="  ")) is None


async def test_the_count_stops_at_the_cap(sess, word, monkeypatch) -> None:
    monkeypatch.setattr(watch, "COUNT_CAP", 2)
    for _ in range(5):
        await source(sess, f"about {word}", at=T0 + dt.timedelta(days=1))
    assert await watch.new_for_view(sess, view(query=word)) == 3


async def test_every_stored_filter_narrows_as_find_would(sess, word) -> None:
    """`B-194`: the count read `topic`, which no view had stored since `B-73`, and never
    applied places, source types or years, so "3 new" on a narrowed view counted the corpus."""
    after = T0 + dt.timedelta(days=1)
    await source(
        sess,
        f"about {word}",
        at=after,
        places=["JP"],
        tier="government",
        published=dt.date(2021, 5, 1),
    )
    await source(
        sess,
        f"about {word}",
        at=after,
        places=["FR"],
        tier="government",
        published=dt.date(2021, 5, 1),
    )
    await source(
        sess, f"about {word}", at=after, places=["JP"], tier="press", published=dt.date(2021, 5, 1)
    )
    await source(
        sess,
        f"about {word}",
        at=after,
        places=["JP"],
        tier="government",
        published=dt.date(2010, 5, 1),
    )

    assert await watch.new_for_view(sess, view(query=word)) == 4
    narrowed = view(
        query=word,
        filters={
            "places": ["JP"],
            "source_tiers": ["government"],
            "published_after": "2020-01-01",
            "published_before": "2022-12-31",
        },
    )
    assert await watch.new_for_view(sess, narrowed) == 1


async def test_a_view_saved_before_b73_still_counts_its_topic(sess, word) -> None:
    topic = f"t{uuid.uuid4().hex[:6]}"
    after = T0 + dt.timedelta(days=1)
    await source(sess, f"about {word}", at=after, topics=[topic])
    await source(sess, f"about {word}", at=after, topics=["other"])
    legacy = view(query=word, filters={"topic": [topic]})
    assert await watch.new_for_view(sess, legacy) == 1


async def test_a_node_view_or_an_unusable_filter_counts_nothing(sess, word) -> None:
    """None, never a count wider than the view: a node view asks about a neighbourhood,
    and a filter the search cannot apply would be dropped."""
    await source(sess, f"about {word}", at=T0 + dt.timedelta(days=1))
    assert await watch.new_for_view(sess, view(query=word, focus=1)) is None
    assert await watch.new_for_view(sess, view(query=word, filters={"not_a_filter": 1})) is None
    assert (
        await watch.new_for_view(sess, view(query=word, filters={"published_after": "soon"}))
        is None
    )
