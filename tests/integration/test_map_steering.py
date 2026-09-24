"""Steering from the map against a real Postgres (task P6-35, spec §10, §10.1).

Every steer goes through machinery that already exists — a topic boost with
an expiry, a search on the queue, a saved view — so each test checks the
three things §10 asks of steering: it changes what it says, it is logged
with a reason in `steering_log`, and it can be undone. And that what cannot
be done is refused in words, with nothing written.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import httpx
import pytest
from area_doubles import a_topic, seed, shrink_levels
from sqlalchemy import delete, func, select

from api.main import create_app
from meridian_core import mapsteer, steering
from meridian_core.areabuild import build_areas
from meridian_core.areaview import AreaNotFound
from meridian_core.db import dispose_engines
from meridian_core.models import (
    Area,
    AreaBuild,
    QueueTask,
    SavedView,
    Source,
    SteeringLog,
    TopicConfig,
)

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 24, 12, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def small_levels(monkeypatch):
    shrink_levels(monkeypatch)


@pytest.fixture
def topic() -> str:
    return a_topic()


async def mapped(sess, topic: str) -> Area:
    """A configured topic, a corpus labelled with it, and one area of it."""
    await steering.add_topic(
        sess, topic, floor=0.0, ceiling=1.0, actor="user", reason="test", now=NOW
    )
    await seed(sess, topic)
    report = await build_areas(sess, topics=[topic])
    return await sess.scalar(
        select(Area).where(Area.build_id == report.build_id, Area.level == 3).limit(1)
    )


async def log_rows(sess, since: int) -> list[SteeringLog]:
    return list(
        await sess.scalars(
            select(SteeringLog).where(SteeringLog.log_id > since).order_by(SteeringLog.log_id)
        )
    )


async def last_log(sess) -> int:
    return int(await sess.scalar(select(func.coalesce(func.max(SteeringLog.log_id), 0))))


async def test_more_boosts_the_areas_topic_and_queues_its_terms(session_for, topic):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    before = await last_log(sess)

    result = await mapsteer.steer_area(sess, area.area_id, "more", actor="user", now=NOW)

    row = await sess.get(TopicConfig, topic)
    assert result.topic == topic
    assert row.boost_factor == mapsteer.MORE_FACTOR
    assert row.boost_expires_at == NOW + dt.timedelta(days=mapsteer.BOOST_DAYS)
    task = await sess.get(QueueTask, result.seed_task_ids[0])
    assert task.url_or_query == " ".join(area.terms[: mapsteer.SEED_TERMS])
    assert (task.task_type, task.seed_source, task.topic) == ("query", "user", topic)

    logged = await log_rows(sess, before)
    assert {r.field for r in logged} == {"boost_factor", "boost_expires_at", "seed"}
    assert all("from the map" in r.reason and str(area.build_id) in r.reason for r in logged)
    assert "queued as a search" in result.message and result.undo


async def test_more_twice_says_what_was_already_there(session_for, topic):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    await mapsteer.steer_area(sess, area.area_id, "more", actor="user", now=NOW)

    again = await mapsteer.steer_area(sess, area.area_id, "more", actor="user", now=NOW)

    assert again.seed_task_ids == []
    assert "already queued" in again.message and "replaces" in again.message


async def test_less_turns_the_topic_down_and_can_be_undone(session_for, topic):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    row = await sess.get(TopicConfig, topic)
    baseline = steering.effective_weight(row, now=NOW)

    result = await mapsteer.steer_area(sess, area.area_id, "less", actor="user", now=NOW)
    assert result.boost_factor == mapsteer.LESS_FACTOR and result.seed_task_ids == []
    assert steering.effective_weight(row, now=NOW) == pytest.approx(baseline * mapsteer.LESS_FACTOR)

    # Undo is the existing "end the boost early", and the weight is untouched.
    await steering.set_boost(
        sess, topic, factor=None, expires_at=None, actor="user", reason="undo", now=NOW
    )
    assert steering.effective_weight(row, now=NOW) == pytest.approx(baseline)


async def test_less_of_an_area_about_no_topic_is_refused_and_writes_nothing(
    session_for, topic, monkeypatch
):
    sess = await session_for("rw")
    area = await mapped(sess, topic)

    async def nobody(_sess, _area):
        return [(None, 60), (topic, 12)]

    monkeypatch.setattr(mapsteer, "area_topics", nobody)
    before = await last_log(sess)

    with pytest.raises(mapsteer.Refused, match="belongs to no configured topic"):
        await mapsteer.steer_area(sess, area.area_id, "less", actor="user", now=NOW)
    assert await log_rows(sess, before) == []
    assert (await sess.get(TopicConfig, topic)).boost_factor is None

    # "More" still queues the area's own terms: asking for it needs no topic.
    more = await mapsteer.steer_area(sess, area.area_id, "more", actor="user", now=NOW)
    assert more.topic is None and more.boost_factor is None and more.seed_task_ids


async def test_watch_saves_a_view_once(session_for, topic):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    before = await last_log(sess)

    result = await mapsteer.steer_area(sess, area.area_id, "watch", actor="user", now=NOW)
    view = await sess.get(SavedView, result.view_id)
    assert view.query == " ".join(area.terms[: mapsteer.SEED_TERMS])
    assert [r.field for r in await log_rows(sess, before)] == ["watch"]

    with pytest.raises(mapsteer.AlreadyDone):
        await mapsteer.steer_area(sess, area.area_id, "watch", actor="user", now=NOW)


async def test_a_suggestion_is_a_logged_search_and_refused_when_it_cannot_be_one(
    session_for, topic
):
    sess = await session_for("rw")
    await steering.add_topic(
        sess, topic, floor=0.0, ceiling=1.0, actor="user", reason="test", now=NOW
    )
    before = await last_log(sess)
    text = f"  heat   resilient shelters {topic} "

    result = await mapsteer.suggest_seed(sess, text, topic=topic, actor="user", now=NOW)
    task = await sess.get(QueueTask, result.seed_task_ids[0])
    assert task.url_or_query == " ".join(text.split())
    assert [r.field for r in await log_rows(sess, before)] == ["seed"]

    with pytest.raises(mapsteer.AlreadyDone):
        await mapsteer.suggest_seed(sess, text, topic=topic, actor="user", now=NOW)
    with pytest.raises(mapsteer.Refused, match="between"):
        await mapsteer.suggest_seed(sess, " a ", topic=None, actor="user", now=NOW)
    with pytest.raises(mapsteer.Refused, match="not a configured topic"):
        await mapsteer.suggest_seed(
            sess, "anything at all", topic=f"{topic}-x", actor="user", now=NOW
        )


async def test_steering_an_area_from_an_earlier_build_is_refused(session_for, topic):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    await build_areas(sess, topics=[topic])
    with pytest.raises(AreaNotFound, match="earlier build"):
        await mapsteer.steer_area(sess, area.area_id, "more", actor="user", now=NOW)


def test_dominance_needs_a_labelled_majority():
    assert mapsteer.dominant([("a", 60), (None, 40)]) == "a"
    assert mapsteer.dominant([(None, 60), ("a", 40)]) is None
    assert mapsteer.dominant([("a", 45), ("b", 45), (None, 10)]) is None
    assert mapsteer.dominant([]) is None
    # A label naming no configured topic is no topic.
    assert mapsteer.dominant([("gone", 90), ("a", 10)], configured={"a"}) is None
    assert mapsteer.dominant([("gone", 10), ("a", 90)], configured={"a"}) == "a"


async def test_an_area_labelled_with_a_topic_no_longer_configured_is_about_no_topic(
    session_for, topic
):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic == topic))
    with pytest.raises(mapsteer.Refused, match="no configured topic"):
        await mapsteer.steer_area(sess, area.area_id, "less", actor="user", now=NOW)
    read = await mapsteer.area_steering(sess, area.area_id)
    assert read.topic is None and read.topics[0].topic == topic


# ---------------------------------------------------------------------------
# Over HTTP: the read on /api/explore, the writes on /api/admin


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed(session_for, topic):
    sess = await session_for("rw")
    area = await mapped(sess, topic)
    await sess.commit()
    yield area
    await sess.execute(delete(AreaBuild).where(AreaBuild.build_id == area.build_id))
    await sess.execute(delete(QueueTask).where(QueueTask.topic == topic))
    await sess.execute(delete(QueueTask).where(QueueTask.url_or_query.like(f"%{topic}%")))
    await sess.execute(delete(SavedView).where(SavedView.name.like("Area: %")))
    await sess.execute(delete(SteeringLog).where(SteeringLog.topic == topic))
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic == topic))
    await sess.execute(delete(Source).where(Source.url.like(f"https://{topic}.test/%")))
    await sess.commit()


async def test_the_menu_reads_what_it_would_move(client, committed, topic):
    body = (await client.get(f"/api/explore/areas/{committed.area_id}/steering")).json()
    assert body["topic"] == topic
    assert body["search"] == " ".join(committed.terms[:3])
    assert (await client.get("/api/explore/areas/999999999999/steering")).status_code == 404


async def test_the_admin_routes_steer_and_refuse(client, committed, topic):
    url = f"/api/admin/map/areas/{committed.area_id}/steer"
    done = await client.post(url, json={"action": "more"})
    assert done.status_code == 200, done.text
    assert done.json()["topic"] == topic and done.json()["seed_task_ids"]

    assert (await client.post(url, json={"action": "delete"})).status_code == 422
    assert (await client.post(url, json={"action": "more", "extra": 1})).status_code == 422
    missing = "/api/admin/map/areas/999999999999/steer"
    assert (await client.post(missing, json={"action": "more"})).status_code == 404

    suggestion = {"text": f"covered walkways {topic}", "topic": topic}
    made = await client.post("/api/admin/map/suggest", json=suggestion)
    assert made.status_code == 201, made.text
    assert (await client.post("/api/admin/map/suggest", json=suggestion)).status_code == 409

    # Reversible through the existing seed route while it is pending.
    task_id = made.json()["seed_task_ids"][0]
    assert (await client.delete(f"/api/admin/seeds/{task_id}")).status_code == 204
