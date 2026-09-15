"""Steering through `/api/admin/*` against a real Postgres (task P6-12, §10).

The claims worth checking here are all about the *set*, not about one row:
changing one topic moves every other one, archiving removes a topic from the
pool without removing anything it produced, and the log has to explain a weight
somebody did not touch.

**These fixtures restore rather than delete.** The dev database holds a real
steered vector, and the app commits on its own connection — so a test that left
weights where it put them would quietly re-steer somebody's crawl. Every topic
row is snapshotted and written back afterwards.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import SteeringLog, TopicConfig

pytestmark = pytest.mark.usefixtures("require_db")

FIELDS = ("weight", "floor", "ceiling", "status", "pinned", "boost_factor", "boost_expires_at")


@pytest.fixture
def new_topic() -> str:
    return f"zz-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def open_admin(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def restored(session_for):
    """Put every topic row back exactly as it was.

    Not a rollback: the app commits on its own connection, so the only way to
    leave the dev database unchanged is to write the old values back.
    """
    sess = await session_for("rw")
    await sess.rollback()
    before = {
        row.topic: {field: getattr(row, field) for field in FIELDS}
        for row in await sess.scalars(select(TopicConfig))
    }
    started = dt.datetime.now(dt.UTC)

    yield sess

    await sess.rollback()
    for row in await sess.scalars(select(TopicConfig)):
        if row.topic not in before:
            await sess.delete(row)
            continue
        for field, value in before[row.topic].items():
            setattr(row, field, value)
    await sess.execute(delete(SteeringLog).where(SteeringLog.changed_at >= started))
    await sess.commit()


async def topics_of(client) -> dict[str, dict]:
    body = (await client.get("/api/admin/topics")).json()
    return {row["topic"]["topic"]: row for row in body["rows"]}


async def an_active_topic(client) -> str:
    rows = await topics_of(client)
    active = sorted(t for t, row in rows.items() if row["topic"]["status"] == "active")
    assert active, "the dev database has no active topic to steer"
    return active[0]


# --------------------------------------------------------------------------
# The vector
# --------------------------------------------------------------------------


async def test_the_active_shares_sum_to_one(client, open_admin, restored) -> None:
    body = (await client.get("/api/admin/topics")).json()

    assert body["sums_to"] == pytest.approx(1.0)


async def test_a_share_is_reported_beside_the_stored_weight(client, open_admin, restored) -> None:
    # The two differ exactly when a boost or a bound is doing something, and it
    # is the share that decides what gets crawled.
    rows = await topics_of(client)

    assert all({"share", "effective_weight", "boost_active"} <= set(row) for row in rows.values())


# --------------------------------------------------------------------------
# Setting a weight
# --------------------------------------------------------------------------


async def test_a_weight_is_set_to_exactly_what_was_asked(client, open_admin, restored) -> None:
    # Not scaled afterwards along with everything else. A control that shows a
    # different number from the one typed into it reads as broken.
    topic = await an_active_topic(client)

    body = (await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.4})).json()
    row = next(r for r in body["rows"] if r["topic"]["topic"] == topic)

    assert row["share"] == pytest.approx(0.4)
    assert body["sums_to"] == pytest.approx(1.0)


async def test_the_others_absorb_the_difference(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)
    before = await topics_of(client)

    body = (await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.4})).json()
    after = {row["topic"]["topic"]: row for row in body["rows"]}

    moved = [t for t in before if t != topic and after[t]["share"] != before[t]["share"]]
    assert moved, "setting one weight changed nothing else, so the pool no longer sums to 1"


async def test_a_weight_above_the_ceiling_is_refused_not_clamped(
    client, open_admin, restored
) -> None:
    # Silently clamping shows a different number and explains nothing — and the
    # bound is exactly what would need changing.
    topic = await an_active_topic(client)
    await client.patch(f"/api/admin/topics/{topic}", json={"ceiling": 0.5})

    response = await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.9})

    assert response.status_code == 422
    assert "0.500" in response.json()["detail"]


async def test_a_refused_change_leaves_the_vector_alone(client, open_admin, restored) -> None:
    # The request rolls back. A half-applied steering change is worse than a
    # rejected one, because the response says it failed.
    topic = await an_active_topic(client)
    await client.patch(f"/api/admin/topics/{topic}", json={"ceiling": 0.5})
    before = await topics_of(client)

    await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.9})
    after = await topics_of(client)

    assert {t: r["share"] for t, r in after.items()} == pytest.approx(
        {t: r["share"] for t, r in before.items()}
    )


async def test_weighting_a_paused_topic_is_refused(client, open_admin, restored) -> None:
    # It draws no seeds, so a share is meaningless. Accepting the number would
    # store something that does nothing and reads as though it does.
    topic = await an_active_topic(client)
    await client.patch(f"/api/admin/topics/{topic}", json={"status": "paused"})

    response = await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.3})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Pausing and archiving (§10.2)
# --------------------------------------------------------------------------


async def test_archiving_drops_a_topic_from_the_pool(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)

    body = (await client.patch(f"/api/admin/topics/{topic}", json={"status": "archived"})).json()
    row = next(r for r in body["rows"] if r["topic"]["topic"] == topic)

    assert row["share"] == 0.0
    assert body["sums_to"] == pytest.approx(1.0)


async def test_archiving_deletes_nothing(client, open_admin, restored) -> None:
    # §10.2: nodes, edges and tags stay untouched and un-archiving is a status
    # change rather than a rebuild. The row keeping its weight is what makes
    # "returning costs nothing" true.
    topic = await an_active_topic(client)
    before = (await topics_of(client))[topic]["topic"]["weight"]

    await client.patch(f"/api/admin/topics/{topic}", json={"status": "archived"})
    after = (await topics_of(client))[topic]["topic"]

    assert after["weight"] == pytest.approx(before)


async def test_un_archiving_returns_it_to_the_pool(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)
    await client.patch(f"/api/admin/topics/{topic}", json={"status": "archived"})

    body = (await client.patch(f"/api/admin/topics/{topic}", json={"status": "active"})).json()
    row = next(r for r in body["rows"] if r["topic"]["topic"] == topic)

    assert row["share"] > 0
    assert body["sums_to"] == pytest.approx(1.0)


async def test_maintenance_also_leaves_the_pool(client, open_admin, restored) -> None:
    # §10.2 puts maintenance and archived in the same place: it "stops
    # generating new seeds but keeps processing its queue", so it draws nothing.
    topic = await an_active_topic(client)

    body = (await client.patch(f"/api/admin/topics/{topic}", json={"status": "maintenance"})).json()
    row = next(r for r in body["rows"] if r["topic"]["topic"] == topic)

    assert row["share"] == 0.0


# --------------------------------------------------------------------------
# Boosts (§10)
# --------------------------------------------------------------------------


async def test_a_boost_raises_the_share_and_leaves_the_weight(
    client, open_admin, restored
) -> None:
    # The stored weight is the baseline the boost returns to. Writing the boost
    # into it is how "steer back later" quietly becomes permanent.
    topic = await an_active_topic(client)
    before = (await topics_of(client))[topic]
    until = (dt.datetime.now(dt.UTC) + dt.timedelta(days=7)).isoformat()

    body = (
        await client.patch(
            f"/api/admin/topics/{topic}",
            json={"boost_factor": 3.0, "boost_expires_at": until},
        )
    ).json()
    row = next(r for r in body["rows"] if r["topic"]["topic"] == topic)

    assert row["boost_active"] is True
    assert row["share"] > before["share"]
    assert row["topic"]["weight"] == pytest.approx(before["topic"]["weight"])


async def test_a_boost_without_an_expiry_is_refused(client, open_admin, restored) -> None:
    # A permanent multiplier wearing a temporary one's clothes. §10 makes decay
    # the mechanism, so a boost that cannot decay is not the thing being asked
    # for.
    topic = await an_active_topic(client)

    response = await client.patch(f"/api/admin/topics/{topic}", json={"boost_factor": 2.0})

    assert response.status_code == 422


async def test_a_boost_that_has_already_expired_is_refused(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)
    past = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()

    response = await client.patch(
        f"/api/admin/topics/{topic}", json={"boost_factor": 2.0, "boost_expires_at": past}
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Adding a topic (§10.2)
# --------------------------------------------------------------------------


async def test_adding_a_topic_renormalises_the_rest(
    client, open_admin, restored, new_topic
) -> None:
    response = await client.post("/api/admin/topics", json={"topic": new_topic})

    assert response.status_code == 201
    assert response.json()["sums_to"] == pytest.approx(1.0)


async def test_a_new_topic_starts_small(client, open_admin, restored, new_topic) -> None:
    # §10.2 calls adding a topic "a small repeat of cold start". A large share
    # given to a topic with no hand-seeded sources is attention spent on nothing.
    body = (await client.post("/api/admin/topics", json={"topic": new_topic, "floor": 0.05})).json()
    row = next(r for r in body["rows"] if r["topic"]["topic"] == new_topic)

    assert row["share"] == pytest.approx(0.05)


async def test_adding_a_topic_that_exists_is_refused(client, open_admin, restored) -> None:
    # Re-adding would reset bounds and status on a topic that already has a
    # crawl behind it. The message points at the status change that was meant.
    topic = await an_active_topic(client)

    response = await client.post("/api/admin/topics", json={"topic": topic})

    assert response.status_code == 422
    assert "Reactivate" in response.json()["detail"]


async def test_a_floor_that_cannot_be_guaranteed_is_refused(
    client, open_admin, restored, new_topic
) -> None:
    # The moment to say so is while the mistake is being made. The alternative
    # is discovering it weeks later from a topic that stalled.
    response = await client.post(
        "/api/admin/topics", json={"topic": new_topic, "floor": 0.95, "ceiling": 1.0}
    )

    assert response.status_code == 422
    assert "floors sum to" in response.json()["detail"]


# --------------------------------------------------------------------------
# The audit log (§10.1)
# --------------------------------------------------------------------------


async def test_the_change_that_was_asked_for_is_logged(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)

    await client.patch(
        f"/api/admin/topics/{topic}", json={"status": "paused", "reason": "testing the log"}
    )
    entries = (await client.get(f"/api/admin/steering-log?topic={topic}")).json()["entries"]

    assert any(e["field"] == "status" and e["new_value"] == "paused" for e in entries)
    assert all(e["actor"] == "user" for e in entries)
    assert any(e["reason"] == "testing the log" for e in entries)


async def test_a_weight_nobody_touched_is_logged_too(client, open_admin, restored) -> None:
    # The question §10.1 exists to answer is "why is this topic at 0.18", and
    # the answer is almost always a change somebody made to a different topic.
    # Logging only the requested change makes the log unable to answer it.
    topic = await an_active_topic(client)

    await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.4})
    entries = (await client.get("/api/admin/steering-log?limit=200")).json()["entries"]
    others = {e["topic"] for e in entries if e["field"] == "weight" and e["topic"] != topic}

    assert others, "only the steered topic was logged, so the consequences are unexplained"


async def test_an_unlogged_reason_is_still_a_reason(client, open_admin, restored) -> None:
    # Requiring a person to type one before moving a slider produces a column
    # full of the word "update". The server writes what was actually done.
    topic = await an_active_topic(client)

    await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.35})
    entries = (await client.get(f"/api/admin/steering-log?topic={topic}")).json()["entries"]

    assert entries
    assert all(e["reason"] for e in entries)


async def test_the_log_is_newest_first(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)
    await client.patch(f"/api/admin/topics/{topic}", json={"status": "paused"})
    await client.patch(f"/api/admin/topics/{topic}", json={"status": "active"})

    entries = (await client.get(f"/api/admin/steering-log?topic={topic}")).json()["entries"]
    stamps = [e["changed_at"] for e in entries]

    assert stamps == sorted(stamps, reverse=True)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


async def test_an_unknown_topic_is_a_404(client, open_admin, restored) -> None:
    assert (await client.patch("/api/admin/topics/nope", json={"weight": 0.1})).status_code == 404


async def test_an_unknown_field_is_refused(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)

    response = await client.patch(f"/api/admin/topics/{topic}", json={"wieght": 0.1})

    assert response.status_code == 422


async def test_an_unknown_status_is_refused(client, open_admin, restored) -> None:
    # `topic_status` is a CHECK constraint. A value that reached Postgres would
    # fail the update after the request had reported success.
    topic = await an_active_topic(client)

    response = await client.patch(f"/api/admin/topics/{topic}", json={"status": "retired"})

    assert response.status_code == 422
