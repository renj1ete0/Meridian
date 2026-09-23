"""The two routes the designed Admin added (task P6-28), against a real Postgres.

**A preview is the write, rolled back.** The add-topic dialog shows the
re-normalisation before anybody commits (design-system §8, spec §10.2). The
claims worth testing are therefore that the preview's numbers are *exactly* the
numbers the write then produces, that it is refused with the write's own
sentence, and that it leaves nothing behind — no row, no weight moved, no log
entry.

**A bulk verdict is all or none.** A page of harvested terms decided at once
must end in the same state the per-term routes would leave each row in, and an
id that does not exist must refuse the whole request rather than deciding the
rest.

Fixtures restore rather than delete, for the reason `test_admin_topics.py`
gives: the dev database holds a real steered vector, and the app commits on its
own connection.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, func, select

from api.main import create_app
from meridian_core.db import dispose_engines
from meridian_core.models import GazetteerTerm, SteeringLog, TopicConfig
from meridian_core.schemas.admin import GAZETTEER_BULK_MAX

pytestmark = pytest.mark.usefixtures("require_db")

FIELDS = ("weight", "floor", "ceiling", "status", "pinned", "boost_factor", "boost_expires_at")


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
def new_topic() -> str:
    return f"zz-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def restored(session_for):
    """Put every topic row back exactly as it was, and drop this test's log rows."""
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


async def vector(client) -> dict[str, dict]:
    body = (await client.get("/api/admin/topics")).json()
    return {row["topic"]["topic"]: row for row in body["rows"]}


async def an_active_topic(client) -> str:
    active = sorted(
        t for t, r in (await vector(client)).items() if r["topic"]["status"] == "active"
    )
    assert len(active) >= 2, "the dev database needs two active topics to steer"
    return active[0]


async def log_count(session_for) -> int:
    sess = await session_for("rw")
    await sess.rollback()
    return int(await sess.scalar(select(func.count()).select_from(SteeringLog)) or 0)


def weights(body: dict) -> dict[str, float]:
    return {row["topic"]["topic"]: row["topic"]["weight"] for row in body["rows"]}


# --------------------------------------------------------------------------
# Previewing an added topic
# --------------------------------------------------------------------------


async def test_the_add_preview_is_what_the_add_then_does(
    client, open_admin, restored, new_topic
) -> None:
    # The whole reason the preview is the write rolled back: a dialog showing
    # one set of numbers and a commit producing another is worse than no
    # dialog, because it was believed.
    body = {"topic": new_topic, "floor": 0.07, "ceiling": 0.5}

    preview = await client.post("/api/admin/topics/preview", json=body)
    added = await client.post("/api/admin/topics", json=body)

    assert preview.status_code == 200
    assert added.status_code == 201
    assert weights(preview.json()) == pytest.approx(weights(added.json()))
    assert new_topic in weights(preview.json())


async def test_the_add_preview_leaves_nothing_behind(
    client, open_admin, restored, new_topic, session_for
) -> None:
    before = await vector(client)
    logged = await log_count(session_for)

    response = await client.post("/api/admin/topics/preview", json={"topic": new_topic})

    assert response.status_code == 200
    after = await vector(client)
    assert new_topic not in after
    assert {t: r["topic"]["weight"] for t, r in after.items()} == pytest.approx(
        {t: r["topic"]["weight"] for t, r in before.items()}
    )
    assert await log_count(session_for) == logged, "a preview wrote to the steering log"


async def test_the_add_preview_is_refused_with_the_writes_sentence(
    client, open_admin, restored, new_topic
) -> None:
    # The dialog shows this before the button is pressed; it is only useful if
    # it is the same refusal the button would have produced.
    body = {"topic": new_topic, "floor": 0.95, "ceiling": 1.0}

    preview = await client.post("/api/admin/topics/preview", json=body)
    write = await client.post("/api/admin/topics", json=body)

    assert preview.status_code == 422
    assert preview.json()["detail"] == write.json()["detail"]
    assert "floors sum to" in preview.json()["detail"]


async def test_previewing_an_existing_topic_says_to_reactivate_it(
    client, open_admin, restored
) -> None:
    topic = await an_active_topic(client)

    response = await client.post("/api/admin/topics/preview", json={"topic": topic})

    assert response.status_code == 422
    assert "Reactivate" in response.json()["detail"]


# --------------------------------------------------------------------------
# Previewing a steering change
# --------------------------------------------------------------------------


async def test_the_edit_preview_is_what_the_edit_then_does(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)

    preview = await client.post(f"/api/admin/topics/{topic}/preview", json={"weight": 0.3})
    applied = await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.3})

    assert preview.status_code == 200
    assert weights(preview.json()) == pytest.approx(weights(applied.json()))
    assert weights(preview.json())[topic] == pytest.approx(0.3)


async def test_previewing_an_archive_releases_the_weight_and_commits_nothing(
    client, open_admin, restored
) -> None:
    # §10.2: archiving releases the weight and re-normalises the rest. The
    # preview must show that, and the topic must still be active afterwards.
    topic = await an_active_topic(client)

    body = (
        await client.post(f"/api/admin/topics/{topic}/preview", json={"status": "archived"})
    ).json()

    rows = {row["topic"]["topic"]: row for row in body["rows"]}
    assert rows[topic]["share"] == 0
    assert body["sums_to"] == pytest.approx(1.0)
    assert (await vector(client))[topic]["topic"]["status"] == "active"


async def test_the_edit_preview_is_refused_as_the_edit_would_be(
    client, open_admin, restored
) -> None:
    topic = await an_active_topic(client)

    preview = await client.post(f"/api/admin/topics/{topic}/preview", json={"weight": 0.99})
    write = await client.patch(f"/api/admin/topics/{topic}", json={"weight": 0.99})

    assert preview.status_code == 422
    assert preview.json()["detail"] == write.json()["detail"]


async def test_previewing_an_unknown_topic_is_a_404(client, open_admin, restored) -> None:
    response = await client.post("/api/admin/topics/nope-not-here/preview", json={"weight": 0.1})

    assert response.status_code == 404


async def test_the_edit_preview_refuses_unknown_fields(client, open_admin, restored) -> None:
    topic = await an_active_topic(client)

    response = await client.post(f"/api/admin/topics/{topic}/preview", json={"wieght": 0.1})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Deciding the gazetteer in bulk
# --------------------------------------------------------------------------


@pytest.fixture
def marker() -> str:
    return f"Qbk{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def terms(session_for, marker: str):
    sess = await session_for("rw")
    made = [
        GazetteerTerm(
            canonical=f"{marker} Term {i}",
            aliases=[f"{marker[:3].upper()}{i}X"],
            entity_type="concept",
            source="auto_acronym",
            occurrence_count=1,
        )
        for i in range(3)
    ]
    sess.add_all(made)
    await sess.commit()

    yield [term.term_id for term in made]

    await sess.execute(delete(GazetteerTerm).where(GazetteerTerm.canonical.like(f"{marker}%")))
    await sess.commit()


async def states(session_for, ids: list[int]) -> dict[int, tuple[bool, bool]]:
    """Each term's (approved, rejected), read on a fresh connection."""
    sess = await session_for("rw")
    await sess.rollback()
    rows = await sess.scalars(select(GazetteerTerm).where(GazetteerTerm.term_id.in_(ids)))
    return {row.term_id: (row.approved, row.rejected_at is not None) for row in rows}


async def test_a_bulk_approval_approves_every_term_and_reports_each(
    client, open_admin, terms, session_for
) -> None:
    response = await client.post(
        "/api/admin/gazetteer/decide", json={"term_ids": terms, "decision": "approve"}
    )

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [row["term"]["term_id"] for row in rows] == terms
    assert all(row["will_load"] for row in rows)
    assert set((await states(session_for, terms)).values()) == {(True, False)}


async def test_a_bulk_turn_down_keeps_the_rows(client, open_admin, terms, session_for) -> None:
    # A tombstone, not a delete, for the per-term route's reason: the harvest
    # re-reads the same documents and would re-file a deleted term.
    response = await client.post(
        "/api/admin/gazetteer/decide", json={"term_ids": terms, "decision": "reject"}
    )

    assert response.status_code == 200
    assert set((await states(session_for, terms)).values()) == {(False, True)}


async def test_putting_back_in_bulk_leaves_terms_undecided(
    client, open_admin, terms, session_for
) -> None:
    await client.post("/api/admin/gazetteer/decide", json={"term_ids": terms, "decision": "reject"})

    await client.post(
        "/api/admin/gazetteer/decide", json={"term_ids": terms, "decision": "restore"}
    )

    assert set((await states(session_for, terms)).values()) == {(False, False)}


async def test_an_unknown_id_refuses_the_whole_decision(
    client, open_admin, terms, session_for
) -> None:
    # All or none. Deciding the rest would leave a curator to work out which
    # of the page took.
    missing = max(terms) + 10_000_000

    response = await client.post(
        "/api/admin/gazetteer/decide",
        json={"term_ids": [*terms, missing], "decision": "approve"},
    )

    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]
    assert set((await states(session_for, terms)).values()) == {(False, False)}


async def test_a_bulk_collision_is_reported_on_the_rows(
    client, open_admin, session_for, marker
) -> None:
    # Approving many at once is how two terms come to claim the same wording,
    # so the verdict per row is the point of returning rows.
    sess = await session_for("rw")
    pair = [
        GazetteerTerm(
            canonical=f"{marker} Left {i}",
            aliases=[f"{marker[:3].upper()}ZZ"],
            entity_type="concept",
            source="auto_acronym",
            occurrence_count=1,
        )
        for i in range(2)
    ]
    sess.add_all(pair)
    await sess.commit()
    ids = [term.term_id for term in pair]
    try:
        response = await client.post(
            "/api/admin/gazetteer/decide", json={"term_ids": ids, "decision": "approve"}
        )
        rows = response.json()["rows"]
        assert {row["withheld_reason"] for row in rows} == {"collision"}
        assert rows[0]["collides_with"] == [ids[1]]
    finally:
        await sess.rollback()
        await sess.execute(delete(GazetteerTerm).where(GazetteerTerm.term_id.in_(ids)))
        await sess.commit()


@pytest.mark.parametrize(
    "body",
    [
        {"term_ids": [], "decision": "approve"},
        {"term_ids": [1], "decision": "delete"},
        {"term_ids": [1], "decision": "approve", "extra": True},
        {"term_ids": list(range(1, GAZETTEER_BULK_MAX + 2)), "decision": "approve"},
    ],
    ids=["empty", "unknown-verdict", "unknown-field", "more-than-a-page"],
)
async def test_a_malformed_bulk_decision_is_refused(client, open_admin, body) -> None:
    response = await client.post("/api/admin/gazetteer/decide", json=body)

    assert response.status_code == 422


def test_a_bulk_page_is_the_queues_page() -> None:
    # Drift: the queue serves at most `MAX_LIMIT` rows a page, and the bulk
    # route exists to decide one page. If the two part, either a page cannot be
    # decided at once or a bulk request can reach past what anybody was shown.
    from api.routes.admin import MAX_LIMIT

    assert GAZETTEER_BULK_MAX == MAX_LIMIT
