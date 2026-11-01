"""The topics on the landing (task B-238), against a real Postgres.

Rolled back, and called directly: the route is a one-line wrapper, checked for its
permission by `tests/unit/test_route_permissions.py` and for its shape by the web's
field-list drift test. Topic names are unique to the run, so the dev corpus never
leaks into a count.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import update

from meridian_core.models import Area, AreaBuild, Source, TopicConfig
from meridian_core.models.source import EMBEDDING_DIM
from meridian_core.sources import upsert_source
from meridian_core.topiccards import FIELD_LEVEL, FIELDS_SHOWN, topic_cards

pytestmark = pytest.mark.usefixtures("require_db")

TODAY = dt.date(2026, 10, 1)


@pytest.fixture
async def sess(session_for):
    return await session_for("rw")


async def a_source(sess, topic: str, n: int, **values) -> Source:
    source, _ = await upsert_source(
        sess,
        f"https://{topic}.example.test/{n}",
        checksum=f"sha256:{uuid.uuid4().hex}",
        source_tier="government",
        title=f"Northfield report {n}",
    )
    await sess.execute(
        update(Source)
        .where(Source.source_id == source.source_id)
        .values(topic_labels=[topic], **values)
    )
    return source


async def test_a_card_counts_what_a_reader_can_find_and_names_the_newest(sess) -> None:
    topic = f"cards-{uuid.uuid4().hex[:8]}"
    sess.add(TopicConfig(topic=topic, description="Made-up kerbside studies.", weight=0.5))
    await sess.flush()
    kept = await a_source(sess, topic, 1, publication_date=dt.date(2025, 3, 1))
    newest = await a_source(sess, topic, 2, publication_date=dt.date(2026, 9, 1))
    await a_source(sess, topic, 3)  # undated: counted, never the newest
    # Refused: a junk page, a copy, and a date after today (a parsing error, not news).
    await a_source(sess, topic, 4, retention_tier="junk", publication_date=dt.date(2026, 9, 20))
    await a_source(
        sess,
        topic,
        5,
        duplicate_of=kept.source_id,
        duplicate_reason="same text",
        publication_date=dt.date(2026, 9, 25),
    )
    await a_source(sess, topic, 6, publication_date=dt.date(2031, 1, 1))
    await sess.flush()

    card = next(c for c in (await topic_cards(sess, today=TODAY)).topics if c.topic == topic)

    assert card.description == "Made-up kerbside studies."
    assert card.sources == 4  # 1, 2, 3 and the future-dated 6; not junk, not the copy
    assert card.newest is not None
    assert card.newest.source_id == newest.source_id
    assert card.newest.published == dt.date(2026, 9, 1)
    assert card.newest.title == "Northfield report 2"


async def test_an_archived_topic_is_left_out_and_an_empty_one_says_so(sess) -> None:
    gone = f"cards-{uuid.uuid4().hex[:8]}"
    empty = f"cards-{uuid.uuid4().hex[:8]}"
    sess.add(TopicConfig(topic=gone, weight=0.5, status="archived"))
    sess.add(TopicConfig(topic=empty, weight=0.5))
    await sess.flush()

    cards = {c.topic: c for c in (await topic_cards(sess, today=TODAY)).topics}

    assert gone not in cards
    assert cards[empty].sources == 0
    assert cards[empty].newest is None  # absent, not a made-up date
    assert cards[empty].description is None


async def test_the_fields_are_the_newest_maps_most_on_topic_ones(sess) -> None:
    topic = f"cards-{uuid.uuid4().hex[:8]}"
    sess.add(TopicConfig(topic=topic, weight=0.5))
    old = AreaBuild(passages=10, params={})
    sess.add(old)
    await sess.flush()
    build = AreaBuild(passages=10, params={})
    sess.add(build)
    await sess.flush()

    def area(build_id: int, level: int, field: str, share: int | None, parent=None) -> Area:
        return Area(
            build_id=build_id,
            level=level,
            parent_id=parent,
            terms=["kerb height"],
            field=field,
            passages=50,
            sources=5,
            tier_mix={},
            topic_mix={topic: share} if share is not None else {"other": 9},
            centroid=[0.0] * (EMBEDDING_DIM - 1) + [1.0],
            x=0.0,
            y=0.0,
        )

    region = area(build.build_id, 1, "Region", 99)
    sess.add(region)
    await sess.flush()
    shares = [5, 40, 12, 30, 0]
    for i, share in enumerate(shares):
        sess.add(area(build.build_id, FIELD_LEVEL, f"Field {i}", share, region.area_id))
    sess.add(area(build.build_id, FIELD_LEVEL, "Elsewhere", None, region.area_id))
    sess.add(area(old.build_id, FIELD_LEVEL, "From an old build", 1000))
    await sess.flush()

    card = next(c for c in (await topic_cards(sess, today=TODAY)).topics if c.topic == topic)

    assert [f.name for f in card.fields] == ["Field 1", "Field 3", "Field 2"][:FIELDS_SHOWN]
    assert [f.passages for f in card.fields] == [40, 30, 12][:FIELDS_SHOWN]
    # The region holds them, which is where the Map opens to draw them.
    assert {f.parent_id for f in card.fields} == {region.area_id}
