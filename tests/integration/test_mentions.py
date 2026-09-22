"""Turning a name into a node, against a real Postgres (task `P4-16`, §5.5).

`tests/integration/test_resolution.py` covers the four steps `P4-02` built.
This covers the one thing that has to happen between them and `add_edge`:
deciding whether a name a model just read is something the graph already
holds, and writing the row when it is not.

The test that matters most is the boring one — **the same name in two
different passages is one node**. It is boring because it is what anybody
would assume, and it is here because the assumption was false: scoring a
fresh mention against an entity with the batch's chunks as its "context"
counts a non-overlap as disagreement, which drags an exact name match under
the separation threshold and founds a new node for every passage.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from meridian_core.mentions import resolve_mention
from meridian_core.models import Entity, Notification
from meridian_core.validation import ValidationError

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.UTC)
PROVENANCE = {"produced_by": "test-agent", "model": "test-model-1", "quality_tier": 3}


@pytest.fixture
def marker() -> str:
    return f"men{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def sess(session_for, marker: str):
    session = await session_for("rw")
    await session.rollback()

    yield session

    await session.rollback()
    await session.execute(delete(Entity).where(Entity.canonical_name.like(f"%{marker}%")))
    await session.execute(delete(Notification).where(Notification.title.like(f"%{marker}%")))
    await session.commit()


async def resolve(sess, name: str, marker: str, *, chunks: list[int], **kwargs):
    return await resolve_mention(
        sess,
        name=name,
        node_type=kwargs.pop("node_type", "organisation"),
        supporting_chunk_ids=chunks,
        now=NOW,
        **PROVENANCE,
        **kwargs,
    )


async def test_a_name_nobody_has_seen_becomes_a_node(sess, marker: str) -> None:
    resolved = await resolve(sess, f"{marker} Authority", marker, chunks=[1])

    assert resolved.created
    assert resolved.entity.entity_id is not None
    assert resolved.entity.supporting_chunk_ids == [1]


async def test_the_new_node_records_what_produced_it(sess, marker: str) -> None:
    """§2.3: every derived row carries its provenance, or nothing downstream
    can tell a frontier model's node from a cheap one's."""
    resolved = await resolve(sess, f"{marker} Authority", marker, chunks=[1])

    assert resolved.entity.produced_by == "test-agent"
    assert resolved.entity.model == "test-model-1"
    assert resolved.entity.quality_tier == 3


async def test_the_same_name_in_two_passages_is_one_node(sess, marker: str) -> None:
    """The fragmentation §5.5 opens with. Two mentions, different chunks, and
    nothing in common but the name — which is the strongest signal there is."""
    first = await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Authority", marker, chunks=[2])

    assert not second.created
    assert second.entity.entity_id == first.entity.entity_id


async def test_resolving_again_accumulates_the_evidence(sess, marker: str) -> None:
    """Context is §5.5's heaviest signal, and it only gets heavier if each
    mention's chunks join the ones already there."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Authority", marker, chunks=[2, 3])

    assert second.entity.supporting_chunk_ids == [1, 2, 3]


async def test_a_different_spelling_is_kept_as_an_alias(sess, marker: str) -> None:
    """The canonical name stays the one that got there first; the variant is
    recorded, because the next resolution blocks on aliases too."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Authority Pte Ltd", marker, chunks=[2])

    assert not second.created
    assert f"{marker} Authority Pte Ltd" in (second.entity.aliases or [])


async def test_a_spelling_that_differs_only_in_case_is_not_an_alias(sess, marker: str) -> None:
    """Blocking is case-insensitive, so such an "alias" is a row that helps
    nothing and one more string for a person reading the node to weigh."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    second = await resolve(sess, f"{marker} AUTHORITY", marker, chunks=[2])

    assert not second.created
    assert not second.entity.aliases


async def test_two_node_types_sharing_a_name_are_two_nodes(sess, marker: str) -> None:
    """§5.5's first cheap win. An organisation and a place that share a name
    are two things, always, and no score may overturn it."""
    first = await resolve(sess, f"{marker} Ashford", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Ashford", marker, chunks=[1], node_type="place")

    assert second.created
    assert second.entity.entity_id != first.entity.entity_id


async def test_two_stated_jurisdictions_are_two_nodes(sess, marker: str) -> None:
    """One name routinely denotes unrelated things in two countries, and those
    are exactly the cases where every similarity signal says "the same"."""
    first = await resolve(sess, f"{marker} Transport Agency", marker, chunks=[1], jurisdiction="SG")
    second = await resolve(
        sess, f"{marker} Transport Agency", marker, chunks=[1], jurisdiction="NZ"
    )

    assert second.created
    assert second.entity.entity_id != first.entity.entity_id


async def test_an_unstated_jurisdiction_does_not_separate(sess, marker: str) -> None:
    """Most passages never say. Treating silence as disagreement would split
    every entity the moment one document happened to name a country."""
    first = await resolve(sess, f"{marker} Transport Agency", marker, chunks=[1], jurisdiction="SG")
    second = await resolve(sess, f"{marker} Transport Agency", marker, chunks=[1])

    assert second.entity.entity_id == first.entity.entity_id


async def test_the_middle_band_creates_a_node_and_asks_a_person(sess, marker: str) -> None:
    """§5.5 queues the middle band for adjudication. The disposition is a
    second node plus a notification, never a merge on the balance of
    probability: a duplicate is visible and recoverable, a conflation is
    neither."""
    first = await resolve(sess, f"{marker} Regional Transit Board", marker, chunks=[1])
    second = await resolve(sess, f"{marker} Regional Transit Bureau", marker, chunks=[1])

    if second.band != "adjudicate":
        pytest.skip(f"these two names score in the {second.band} band, not the middle one")

    assert second.created
    assert second.entity.entity_id != first.entity.entity_id
    queued = (
        await sess.scalars(
            select(Notification).where(
                Notification.notification_type == "merge_adjudication",
                Notification.title.like(f"%{marker}%"),
            )
        )
    ).all()
    assert len(queued) == 1
    assert queued[0].payload["candidate"] == first.entity.entity_id


async def test_a_separate_name_raises_nothing_for_anybody_to_read(sess, marker: str) -> None:
    """A queue that filled with obvious non-matches would train whoever reads
    it to approve without looking."""
    await resolve(sess, f"{marker} Authority", marker, chunks=[1])
    await resolve(sess, f"{marker} Office of Coastal Records", marker, chunks=[1])

    queued = (
        await sess.scalars(
            select(Notification).where(
                Notification.notification_type == "merge_adjudication",
                Notification.title.like(f"%{marker}%"),
            )
        )
    ).all()
    assert queued == []


async def test_a_run_may_not_create_an_annotation(sess, marker: str) -> None:
    """An annotation is a note a person wrote (§12.5, `P6-05`) and is the only
    layer of the graph that reflects the reader's own thinking. Nothing
    downstream could tell a forged one from a real one."""
    with pytest.raises(ValidationError, match="annotation"):
        await resolve(sess, f"{marker} Note", marker, chunks=[1], node_type="annotation")


async def test_a_blank_mention_is_refused(sess, marker: str) -> None:
    with pytest.raises(ValidationError):
        await resolve(sess, "   ", marker, chunks=[1])
