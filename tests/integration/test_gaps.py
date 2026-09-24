"""Gaps against a real Postgres (task P6-36).

Two claims matter. The list's numbers are the database's (a thin topic is thin
because its labelled sources are few, counted here independently). And every
action goes through existing machinery, leaves a `steering_log` row, and is
refused for a held-out question-set item (eval/README.md) — by the server, not
only by the UI.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import yaml
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core import gaps
from meridian_core.db import dispose_engines
from meridian_core.models import Chunk, ChunkTopics, QueueTask, Source, SteeringLog, TopicConfig

pytestmark = pytest.mark.usefixtures("require_db")

BOOST = ("boost_factor", "boost_expires_at")


def marker() -> str:
    return f"zz-gap-{uuid.uuid4().hex[:8]}"


async def add_topic(sess, topic: str, status: str = "active") -> None:
    sess.add(TopicConfig(topic=topic, weight=0.0, floor=0.0, ceiling=1.0, status=status))
    await sess.flush()


async def add_sources(
    sess, topic: str, n: int, *, tier: str = "institutional", published: dt.date | None = None
) -> None:
    for _ in range(n):
        source = Source(
            url=f"https://{uuid.uuid4().hex[:10]}.test/{topic}",
            source_tier=tier,
            retention_tier="primary",
            checksum=f"sha256:{uuid.uuid4().hex}",
            text_available=True,
            topic_labels=[topic],
            publication_date=published,
        )
        sess.add(source)
        await sess.flush()
        sess.add(Chunk(source_id=source.source_id, text=f"{topic} passage", chunk_index=0))
    await sess.flush()


def by_id(found: list[gaps.Gap]) -> dict[str, gaps.Gap]:
    return {g.id: g for g in found}


# -- topic coverage -----------------------------------------------------------


async def test_a_topic_with_few_labelled_sources_is_thin_with_the_database_counts(session_for):
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic)
    await add_sources(sess, topic, 2, tier="government")

    gap = by_id(await gaps.topic_coverage(sess))[f"topic-thin:{topic}"]
    assert gap.evidence["sources"] == 2
    assert gap.evidence["passages"] == 2
    assert gap.evidence["strong_sources"] == 2
    assert {a.kind for a in gap.actions} == {"seed_query", "boost_topic"}


async def test_passages_about_a_topic_in_other_documents_are_counted(session_for):
    """`P2-24`: a passage labelled with the topic inside a document labelled
    with something else counts as a passage and names its document; one inside
    a document already labelled with the topic is not counted twice; a
    superseded chunk and an off-topic passage do not count at all."""
    sess = await session_for("rw")
    topic, other = marker(), marker()
    await add_topic(sess, topic)
    await add_topic(sess, other)
    await add_sources(sess, topic, 1)
    await add_sources(sess, other, 2)

    own = await sess.scalar(
        select(Chunk.chunk_id).join(Source).where(Source.topic_labels == [topic])
    )
    elsewhere = list(
        await sess.scalars(
            select(Chunk.chunk_id).join(Source).where(Source.topic_labels == [other])
        )
    )
    sess.add(ChunkTopics(chunk_id=own, topic_labels=[topic], topic_scores={}, topic_basis="t"))
    sess.add(
        ChunkTopics(chunk_id=elsewhere[0], topic_labels=[topic], topic_scores={}, topic_basis="t")
    )
    sess.add(ChunkTopics(chunk_id=elsewhere[1], topic_labels=[], topic_scores={}, topic_basis="t"))
    # A superseded chunk in the second document, labelled with the topic.
    old_source = await sess.scalar(select(Chunk.source_id).where(Chunk.chunk_id == elsewhere[1]))
    gone = Chunk(
        source_id=old_source,
        text="old",
        chunk_index=7,
        superseded_at=dt.datetime.now(dt.UTC),
    )
    sess.add(gone)
    await sess.flush()
    sess.add(
        ChunkTopics(chunk_id=gone.chunk_id, topic_labels=[topic], topic_scores={}, topic_basis="t")
    )
    await sess.flush()

    found = by_id(await gaps.topic_coverage(sess))
    gap = found[f"topic-thin:{topic}"]
    assert gap.evidence["sources"] == 1
    assert gap.evidence["passages"] == 2
    assert gap.evidence["passage_sources"] == 1
    assert "1 other document" in gap.reason
    # The other topic's documents gained nothing from a passage about this one.
    assert found[f"topic-thin:{other}"].evidence["passage_sources"] == 0
    assert found[f"topic-thin:{other}"].evidence["passages"] == 2


async def test_a_topic_with_no_sources_at_all_is_the_most_severe(session_for):
    sess = await session_for("rw")
    empty, some = marker(), marker()
    await add_topic(sess, empty)
    await add_topic(sess, some)
    await add_sources(sess, some, 5)
    found = by_id(await gaps.topic_coverage(sess))
    assert found[f"topic-thin:{empty}"].severity > found[f"topic-thin:{some}"].severity
    assert found[f"topic-thin:{empty}"].evidence["sources"] == 0


async def test_enough_sources_but_few_strong_ones_is_weak_not_thin(session_for):
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic)
    await add_sources(sess, topic, gaps.THIN_SOURCES)
    await add_sources(sess, topic, 1, tier="peer_reviewed")
    found = by_id(await gaps.topic_coverage(sess))
    assert f"topic-thin:{topic}" not in found
    assert found[f"topic-weak:{topic}"].evidence["strong_sources"] == 1


async def test_an_old_newest_source_is_stale(session_for):
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic)
    await add_sources(sess, topic, 3, published=dt.date(2012, 1, 1))
    assert f"topic-stale:{topic}" in by_id(await gaps.topic_coverage(sess))


async def test_archived_topics_are_not_gaps(session_for):
    """Archiving was a decision; listing the topic as a gap would argue with it."""
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic, status="archived")
    assert not [g for g in await gaps.topic_coverage(sess) if g.subject == topic]


# -- search yield ---------------------------------------------------------------


async def test_searches_that_queued_nothing_are_a_gap_and_one_result_clears_it(session_for):
    sess = await session_for("rw")
    topic = marker()
    await add_topic(sess, topic)
    for i in range(gaps.SEARCH_YIELD_MIN):
        sess.add(
            QueueTask(url_or_query=f"{topic} q{i}", task_type="query", status="done", topic=topic)
        )
    await sess.flush()
    assert f"search-yield:{topic}" in by_id(await gaps.search_yield(sess))

    sess.add(QueueTask(url_or_query=f"https://{topic}.test/r", seed_source="search", topic=topic))
    await sess.flush()
    assert f"search-yield:{topic}" not in by_id(await gaps.search_yield(sess))


# -- the API ---------------------------------------------------------------------


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def committed_topic(session_for):
    """A committed active topic the app can see; removed with its queue and log rows."""
    sess = await session_for("rw")
    await sess.rollback()
    topic = marker()
    await add_topic(sess, topic)
    await sess.commit()
    yield topic
    await sess.rollback()
    await sess.execute(delete(QueueTask).where(QueueTask.topic == topic))
    await sess.execute(delete(SteeringLog).where(SteeringLog.topic == topic))
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic == topic))
    await sess.commit()


async def test_the_list_reads_and_names_pending_sources(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MERIDIAN_EVAL_RUNS_DIR", str(tmp_path))
    body = (await client.get("/api/explore/gaps")).json()
    states = {s["name"]: s["status"] for s in body["sources"]}
    assert states["topic-coverage"] == "ok"
    assert states["question-set"] == "unavailable"  # no run file in tmp_path
    assert states["areas"] == "pending" and states["routes"] == "pending"
    severities = [g["severity"] for g in body["gaps"]]
    assert severities == sorted(severities, reverse=True)


async def test_the_list_carries_low_question_items_from_the_newest_run(
    client, monkeypatch, tmp_path
):
    (tmp_path / "2026-01-01.yaml").write_text(
        yaml.safe_dump(
            {
                "set_version": 1,
                "draft": True,
                "items": [
                    {
                        "id": "Q99",
                        "kind": "lookup",
                        "question": "a question",
                        "topics": [],
                        "hits": [],
                        "proposed": {"grade": 2},
                        "operator": {"grade": 0, "missing": "nothing on it"},
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("MERIDIAN_EVAL_RUNS_DIR", str(tmp_path))
    body = (await client.get("/api/explore/gaps")).json()
    (gap,) = [g for g in body["gaps"] if g["id"] == "question:Q99"]
    assert gap["evidence"]["basis"] == "operator"
    assert [a["kind"] for a in gap["actions"]] == ["open_search"]


async def test_seeding_queues_a_query_and_logs_it(client, open_admin, committed_topic, session_for):
    response = await client.post(
        "/api/admin/gaps/seed",
        json={
            "topic": committed_topic,
            "query": "  shade   study ",
            "gap_id": f"topic-thin:{committed_topic}",
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    sess = await session_for("rw")
    await sess.rollback()
    task = await sess.get(QueueTask, task_id)
    assert (task.url_or_query, task.task_type, task.seed_source, task.topic) == (
        "shade study",
        "query",
        "user",
        committed_topic,
    )
    logged = (
        await sess.scalars(select(SteeringLog).where(SteeringLog.topic == committed_topic))
    ).all()
    assert [(r.field, r.new_value) for r in logged] == [(gaps.SEED_FIELD, "shade study")]
    assert str(task_id) in logged[0].reason

    again = await client.post(
        "/api/admin/gaps/seed",
        json={"topic": committed_topic, "query": "shade study", "gap_id": "x"},
    )
    assert again.status_code == 409


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"query": "anything at all", "gap_id": "question:Q07"}, 422),
        ({"query": "https://example.test/", "gap_id": "topic-thin:x"}, 422),
        ({"query": "  a ", "gap_id": "topic-thin:x"}, 422),
        ({"query": "fine words", "gap_id": "topic-thin:x", "extra": 1}, 422),
    ],
)
async def test_seeding_refuses_and_leaves_nothing(
    client, open_admin, committed_topic, session_for, payload, status
):
    response = await client.post("/api/admin/gaps/seed", json={"topic": committed_topic, **payload})
    assert response.status_code == status, response.text
    sess = await session_for("rw")
    await sess.rollback()
    assert not (
        await sess.scalars(select(QueueTask).where(QueueTask.topic == committed_topic))
    ).all()
    assert not (
        await sess.scalars(select(SteeringLog).where(SteeringLog.topic == committed_topic))
    ).all()


async def test_seeding_an_unknown_topic_is_404(client, open_admin):
    response = await client.post(
        "/api/admin/gaps/seed", json={"topic": marker(), "query": "words here", "gap_id": "g"}
    )
    assert response.status_code == 404


async def test_boosting_sets_an_expiring_boost_through_steering(
    client, open_admin, committed_topic, session_for
):
    response = await client.post(
        "/api/admin/gaps/boost",
        json={
            "topic": committed_topic,
            "factor": 2,
            "days": 7,
            "gap_id": f"topic-thin:{committed_topic}",
        },
    )
    assert response.status_code == 200, response.text
    sess = await session_for("rw")
    await sess.rollback()
    row = await sess.get(TopicConfig, committed_topic)
    assert row.boost_factor == 2
    assert row.boost_expires_at > dt.datetime.now(dt.UTC) + dt.timedelta(days=6)
    fields = {
        r.field
        for r in await sess.scalars(select(SteeringLog).where(SteeringLog.topic == committed_topic))
    }
    assert set(BOOST) <= fields


async def test_boosting_refuses_a_question_item_and_an_inactive_topic(
    client, open_admin, committed_topic, session_for
):
    held = await client.post(
        "/api/admin/gaps/boost",
        json={"topic": committed_topic, "factor": 2, "days": 7, "gap_id": "question:Q01"},
    )
    assert held.status_code == 422
    too_big = await client.post(
        "/api/admin/gaps/boost",
        json={"topic": committed_topic, "factor": 50, "days": 7, "gap_id": "g"},
    )
    assert too_big.status_code == 422
    sess = await session_for("rw")
    await sess.rollback()
    assert (await sess.get(TopicConfig, committed_topic)).boost_factor is None


async def test_actions_are_closed_when_admin_is(client, monkeypatch, committed_topic):
    monkeypatch.delenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    response = await client.post(
        "/api/admin/gaps/seed", json={"topic": committed_topic, "query": "words", "gap_id": "g"}
    )
    assert response.status_code == 503


async def test_the_list_route_accepts_no_write(client):
    assert (await client.post("/api/explore/gaps", json={})).status_code == 405
