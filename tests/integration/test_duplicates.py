"""Deciding a possible duplicate (task `B-202`, spec §5.5), against a real Postgres.

Resolution queues the uncertain middle band for a person. Before `B-202` nothing could decide
one: the notifications piled up, most of the bell. What matters here is that a merge goes
through the reversible path, that undo splits it exactly, that a decision is recorded where
the bell reads it, and that what must not happen is refused rather than half-done.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from api.main import create_app
from meridian_core import duplicates
from meridian_core.db import dispose_engines
from meridian_core.models import Entity, Notification

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    yield s
    await s.rollback()


async def node(sess, name: str, node_type: str = "concept") -> Entity:
    row = Entity(
        canonical_name=f"{name} {uuid.uuid4().hex[:6]}",
        node_type=node_type,
        aliases=[name],
        supporting_chunk_ids=[],
    )
    sess.add(row)
    await sess.flush()
    return row


async def pair(sess, created: Entity, candidate: Entity) -> Notification:
    note = Notification(
        notification_type="merge_adjudication",
        title=f"Possible duplicate: {created.canonical_name}",
        payload={
            "mention": "a name",
            "created": created.entity_id,
            "candidate": candidate.entity_id,
        },
        surface="admin",
    )
    sess.add(note)
    await sess.flush()
    return note


async def test_an_undecided_pair_is_listed_with_both_nodes(sess) -> None:
    created, candidate = await node(sess, "x"), await node(sess, "x")
    note = await pair(sess, created, candidate)
    page = await duplicates.open_pairs(sess, limit=50)
    ours = next(p for p in page.pairs if p.notification_id == note.notification_id)
    assert ours.created.entity_id == created.entity_id
    assert ours.candidate.entity_id == candidate.entity_id
    assert page.total >= 1


async def test_merge_redirects_and_undo_splits_it_exactly(sess) -> None:
    created, candidate = await node(sess, "y"), await node(sess, "y")
    note = await pair(sess, created, candidate)

    decided = await duplicates.decide(sess, note.notification_id, "merge", decided_by="test")
    assert decided.decision == "merged" and decided.merge_id is not None
    await sess.refresh(created)
    assert created.redirects_to == candidate.entity_id
    await sess.refresh(note)
    assert note.payload["decision"] == "merged" and note.read_at is not None
    page = await duplicates.open_pairs(sess, limit=50)
    assert note.notification_id not in [p.notification_id for p in page.pairs]

    await duplicates.undo(sess, note.notification_id, decided_by="test")
    await sess.refresh(created)
    assert created.redirects_to is None
    await sess.refresh(note)
    assert "decision" not in note.payload and note.read_at is None


async def test_keep_apart_changes_no_node(sess) -> None:
    created, candidate = await node(sess, "z"), await node(sess, "z")
    note = await pair(sess, created, candidate)
    decided = await duplicates.decide(sess, note.notification_id, "keep", decided_by="test")
    assert decided.decision == "kept apart"
    await sess.refresh(created)
    assert created.redirects_to is None


async def test_what_must_not_happen_is_refused_and_nothing_changes(sess) -> None:
    created, candidate = await node(sess, "w", "concept"), await node(sess, "w", "organisation")
    note = await pair(sess, created, candidate)
    # Across node types: resolution refuses, and the pair stays open.
    with pytest.raises(duplicates.DuplicateRefused, match="different kinds"):
        await duplicates.decide(sess, note.notification_id, "merge", decided_by="test")
    await sess.refresh(note)
    assert "decision" not in (note.payload or {})

    await duplicates.decide(sess, note.notification_id, "keep", decided_by="test")
    with pytest.raises(duplicates.DuplicateRefused, match="Already decided"):
        await duplicates.decide(sess, note.notification_id, "keep", decided_by="test")
    with pytest.raises(duplicates.DuplicateRefused, match="No possible duplicate"):
        await duplicates.decide(sess, 10**12, "keep", decided_by="test")


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("MERIDIAN_ADMIN_ALLOW_ANONYMOUS", "true")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://api.test"
    ) as c:
        yield c
    await dispose_engines()


async def test_the_routes_refuse_in_words(client) -> None:
    listed = await client.get("/api/admin/duplicates")
    assert listed.status_code == 200 and set(listed.json()) == {"pairs", "total"}
    unknown = await client.post("/api/admin/duplicates/999999999999", json={"decision": "merge"})
    assert unknown.status_code == 409 and "No possible duplicate" in unknown.json()["detail"]
    bogus = await client.post("/api/admin/duplicates/1", json={"decision": "maybe"})
    assert bogus.status_code == 422
    assert (await client.get("/api/explore/duplicates")).status_code == 404
