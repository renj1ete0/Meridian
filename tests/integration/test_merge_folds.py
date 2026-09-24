"""A merge folds a repeated claim into the one already held (task `B-41`, §5.5).

`add_edge` treats subject, relation and object as one claim with several
citations. A merge that re-pointed an edge onto a triple the target already
held used to leave two rows for that claim; these tests hold `merge` to the
same rule, and `reverse` to splitting the fold back exactly.

The round-trip tests compare **every column of every row** the merge could
touch, read from the table rather than listed here, so a column added later is
covered without editing them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, func, or_, select

from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Edge,
    Entity,
    MergeLog,
    Observation,
)
from meridian_core.resolution import (
    MergeError,
    fold_repeated_edges,
    merge,
    reverse,
    revive,
    snapshot,
)
from worker.edgedupes import run_pass

pytestmark = pytest.mark.usefixtures("require_db")

MARK = "merge-fold-test"
NOW = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


@pytest.fixture
async def sess(session_for):
    s = await session_for("rw")
    # The repair pass reads the whole table; start it empty inside this
    # test's transaction, which is rolled back afterwards.
    await s.execute(delete(MergeLog))
    await s.execute(delete(Edge))
    await s.flush()
    return s


async def an_entity(sess, name: str) -> Entity:
    row = Entity(
        canonical_name=f"{name} {uuid.uuid4().hex[:6]}",
        node_type="organisation",
        description=MARK,
        aliases=[f"{name} alias"],
        supporting_chunk_ids=[900],
    )
    sess.add(row)
    await sess.flush()
    return row


async def an_edge(sess, from_id: int, to_id: int, *, chunks=(1,), tier=2, **over) -> Edge:
    row = Edge(
        from_node=from_id,
        to_node=to_id,
        relation_type=over.pop("relation_type", "influences"),
        supporting_chunk_ids=list(chunks),
        quality_tier=tier,
        produced_by=over.pop("produced_by", f"agent-t{tier}"),
        model=over.pop("model", f"model-t{tier}"),
        **over,
    )
    sess.add(row)
    await sess.flush()
    return row


async def an_attribute(sess) -> AttributeDefinition:
    row = AttributeDefinition(
        name=f"zz-fold-{uuid.uuid4().hex[:8]}", scope="global", status="active", usage_count=2
    )
    sess.add(row)
    await sess.flush()
    return row


async def a_value(
    sess, entity_id: int, attribute_id: int, *, value, chunks, tier
) -> AttributeValue:
    row = AttributeValue(
        entity_id=entity_id,
        attribute_id=attribute_id,
        value=value,
        supporting_chunk_ids=list(chunks),
        quality_tier=tier,
        produced_by=f"agent-t{tier}",
        model=f"model-t{tier}",
    )
    sess.add(row)
    await sess.flush()
    return row


async def state(sess, *entity_ids: int) -> dict:
    """Every row a merge of these entities could touch, whole."""
    sess.expire_all()
    ids = list(entity_ids)
    edges = (
        (
            await sess.execute(
                select(Edge).where(or_(Edge.from_node.in_(ids), Edge.to_node.in_(ids)))
            )
        )
        .scalars()
        .all()
    )
    # Edges that name any of those as a contradiction are touched too.
    edge_ids = [e.edge_id for e in edges]
    contesting = (
        (await sess.execute(select(Edge).where(Edge.contested_with.overlap(edge_ids))))
        .scalars()
        .all()
        if edge_ids
        else []
    )
    values = (
        (await sess.execute(select(AttributeValue).where(AttributeValue.entity_id.in_(ids))))
        .scalars()
        .all()
    )
    observations = (
        (
            await sess.execute(
                select(Observation).where(
                    or_(
                        Observation.subject_entity_id.in_(ids),
                        Observation.geography_entity_id.in_(ids),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    entities = (await sess.execute(select(Entity).where(Entity.entity_id.in_(ids)))).scalars().all()
    definitions = (
        (
            await sess.execute(
                select(AttributeDefinition).where(
                    AttributeDefinition.attribute_id.in_({v.attribute_id for v in values} or {-1})
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "edges": {e.edge_id: snapshot(e) for e in [*edges, *contesting]},
        "values": {v.value_id: snapshot(v) for v in values},
        "observations": {o.observation_id: snapshot(o) for o in observations},
        "entities": {
            e.entity_id: {k: v for k, v in snapshot(e).items() if k != "embedding"}
            for e in entities
        },
        "usage": {d.attribute_id: d.usage_count for d in definitions},
    }


async def triple_count(sess, from_id: int, relation: str, to_id: int) -> int:
    return await sess.scalar(
        select(func.count())
        .select_from(Edge)
        .where(Edge.from_node == from_id, Edge.relation_type == relation, Edge.to_node == to_id)
    )


@pytest.fixture
async def trio(sess):
    return (
        await an_entity(sess, "Src"),
        await an_entity(sess, "Tgt"),
        await an_entity(sess, "Other"),
    )


async def test_one_claim_one_row_every_citation(sess, trio) -> None:
    """The failure as it was found: a moved edge on a held triple stayed a
    second row. After the merge there is one row, the held one, carrying the
    union of both edges' citations."""
    source, target, other = trio
    held = await an_edge(sess, target.entity_id, other.entity_id, chunks=[1, 2])
    moved = await an_edge(sess, source.entity_id, other.entity_id, chunks=[2, 3])

    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")

    assert await triple_count(sess, target.entity_id, "influences", other.entity_id) == 1
    await sess.refresh(held)
    assert held.supporting_chunk_ids == [1, 2, 3]
    assert await sess.get(Edge, moved.edge_id) is None
    [record] = entry.combined
    assert record["survivor_id"] == held.edge_id
    assert record["absorbed"]["edge_id"] == moved.edge_id
    assert record["absorbed"]["supporting_chunk_ids"] == [2, 3]


async def test_a_different_relation_is_a_different_claim(sess, trio) -> None:
    """Folding keys on the whole triple; a looser key would conflate claims."""
    source, target, other = trio
    await an_edge(sess, target.entity_id, other.entity_id)
    await an_edge(sess, source.entity_id, other.entity_id, relation_type="opposes")

    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")

    assert entry.combined is None
    assert await triple_count(sess, target.entity_id, "opposes", other.entity_id) == 1
    assert await triple_count(sess, target.entity_id, "influences", other.entity_id) == 1


@pytest.mark.parametrize(
    "held_tier,moved_tier,winner",
    [(3, 1, "held"), (1, 3, "moved"), (2, 2, "held"), (None, 2, "moved"), (2, None, "held")],
)
async def test_the_tier_never_goes_down(sess, trio, held_tier, moved_tier, winner) -> None:
    """§11.12: quality tier only moves up automatically. The judgement moves
    to the folded edge only if its tier is strictly higher — an equal tier
    disagreeing is a contradiction to record, not a value to overwrite."""
    source, target, other = trio
    held = await an_edge(
        sess, target.entity_id, other.entity_id, tier=held_tier, stance="supports", confidence=0.4
    )
    await an_edge(
        sess, source.entity_id, other.entity_id, tier=moved_tier, stance="opposes", confidence=0.9
    )

    await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    await sess.refresh(held)

    expected = held_tier if winner == "held" else moved_tier
    assert held.quality_tier == expected
    assert held.stance == ("supports" if winner == "held" else "opposes")
    assert held.model == (f"model-t{expected}")
    assert held.supporting_chunk_ids == [1]


async def test_a_fold_fills_an_empty_period_as_a_pair(sess, trio) -> None:
    """Borrowing one end of a period could make it end before it begins,
    which the table refuses; the pair moves together or not at all."""
    source, target, other = trio
    held = await an_edge(sess, target.entity_id, other.entity_id, valid_from=dt.date(2020, 1, 1))
    await an_edge(
        sess,
        source.entity_id,
        other.entity_id,
        valid_from=dt.date(2015, 1, 1),
        valid_to=dt.date(2016, 1, 1),
    )

    await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    await sess.refresh(held)

    assert (held.valid_from, held.valid_to) == (dt.date(2020, 1, 1), None)


async def test_a_contradiction_that_named_the_folded_edge_names_the_survivor(sess, trio) -> None:
    """A `contested_with` pointing at a row that has left the table would be a
    disagreement with nothing."""
    source, target, other = trio
    held = await an_edge(sess, target.entity_id, other.entity_id)
    moved = await an_edge(sess, source.entity_id, other.entity_id)
    rival = await an_edge(
        sess,
        other.entity_id,
        target.entity_id,
        relation_type="opposes",
        contested_with=[moved.edge_id],
    )

    await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    await sess.refresh(rival)

    assert rival.contested_with == [held.edge_id]


async def test_merge_then_reverse_is_the_exact_prior_state(sess, trio) -> None:
    """§5.5: merges must be reversible. Every row the merge touched — folded,
    moved, left, contradicted, and the entities themselves — comes back
    column for column, ids included."""
    source, target, other = trio
    s, t, o = source.entity_id, target.entity_id, other.entity_id
    held = await an_edge(sess, t, o, chunks=[1, 2], tier=1, topic_labels=["a"])
    moved = await an_edge(sess, s, o, chunks=[3], tier=3, topic_labels=["b"], stance="mixed")
    await an_edge(sess, s, o, relation_type="enables", chunks=[4])  # moves, no fold
    # Both ends of a source-target pair land on the same self-loop: two moved
    # edges folding into each other, each with one column that moved.
    await an_edge(sess, s, t, relation_type="links", chunks=[5])
    await an_edge(sess, t, s, relation_type="links", chunks=[6])
    await an_edge(sess, o, s, relation_type="opposes", contested_with=[moved.edge_id])
    await an_edge(sess, o, o, relation_type="opposes", contested_with=[held.edge_id])

    attribute = await an_attribute(sess)
    await a_value(sess, t, attribute.attribute_id, value="low", chunks=[7], tier=1)
    await a_value(sess, s, attribute.attribute_id, value="high", chunks=[8], tier=2)
    lone = await an_attribute(sess)
    await a_value(sess, s, lone.attribute_id, value="only", chunks=[9], tier=1)

    sess.add(
        Observation(
            subject_entity_id=t,
            geography_entity_id=s,
            metric="m",
            value_numeric=1.0,
            supporting_chunk_ids=[10],
        )
    )
    sess.add(
        Observation(subject_entity_id=s, metric="m", value_numeric=1.0, supporting_chunk_ids=[11])
    )
    await sess.flush()

    before = await state(sess, s, t, o)
    entry = await merge(sess, s, t, decided_by="auto")
    assert len(entry.combined) == 3, "one edge fold, one self-loop fold, one attribute fold"
    await reverse(sess, entry.merge_id, reversed_by="user")
    after = await state(sess, s, t, o)

    assert after == before


async def test_citations_are_never_lost(sess, trio) -> None:
    """Every chunk cited before the merge is cited after it, either live or
    in the folded row the log keeps whole."""
    source, target, other = trio
    await an_edge(sess, target.entity_id, other.entity_id, chunks=[1, 2])
    await an_edge(sess, source.entity_id, other.entity_id, chunks=[3, 4])
    attribute = await an_attribute(sess)
    await a_value(sess, target.entity_id, attribute.attribute_id, value="x", chunks=[5], tier=2)
    await a_value(sess, source.entity_id, attribute.attribute_id, value="y", chunks=[6], tier=1)

    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")

    live_edges = (await sess.execute(select(Edge.supporting_chunk_ids))).scalars().all()
    live_values = (
        (
            await sess.execute(
                select(AttributeValue.supporting_chunk_ids).where(
                    AttributeValue.attribute_id == attribute.attribute_id
                )
            )
        )
        .scalars()
        .all()
    )
    live = {c for chunks in [*live_edges, *live_values] for c in chunks}
    assert live == {1, 2, 3, 4, 5, 6}
    logged = {c for r in entry.combined for c in r["absorbed"]["supporting_chunk_ids"]}
    assert logged == {3, 4, 6}


async def test_a_reversal_keeps_citations_gained_after_the_merge(sess, trio) -> None:
    """The reversal undoes the merge, not the corroboration that followed it:
    a chunk `add_edge` attached to the survivor afterwards stays."""
    source, target, other = trio
    held = await an_edge(sess, target.entity_id, other.entity_id, chunks=[1])
    await an_edge(sess, source.entity_id, other.entity_id, chunks=[2])
    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")

    # What `add_edge` does when the same claim arrives again from a new chunk.
    await sess.refresh(held)
    held.supporting_chunk_ids = [*held.supporting_chunk_ids, 77]
    await sess.flush()

    await reverse(sess, entry.merge_id, reversed_by="user")
    await sess.refresh(held)

    assert held.supporting_chunk_ids == [1, 77]


async def test_a_reversal_whose_survivor_is_gone_is_refused_and_touches_nothing(sess, trio) -> None:
    """A later merge that folded the survivor away must be reversed first.
    The refusal comes before any row is touched."""
    source, target, other = trio
    held = await an_edge(sess, target.entity_id, other.entity_id)
    await an_edge(sess, source.entity_id, other.entity_id)
    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    merge_id = entry.merge_id
    # Simulate the survivor having been folded by a later merge.
    await sess.delete(await sess.get(Edge, held.edge_id))
    await sess.flush()
    before = await state(sess, source.entity_id, target.entity_id, other.entity_id)

    with pytest.raises(MergeError) as raised:
        await reverse(sess, merge_id, reversed_by="user")

    assert raised.value.reason == "order"
    assert await state(sess, source.entity_id, target.entity_id, other.entity_id) == before
    await sess.refresh(entry)
    assert entry.reversed_at is None


async def test_an_edge_joining_the_two_moves_back_only_the_end_that_moved(sess, trio) -> None:
    """The reversal used to move back every column naming the target, so an
    edge from the target to the source came back as a self-loop on the
    source. The per-row columns in the log say which end moved."""
    source, target, _ = trio
    edge = await an_edge(sess, target.entity_id, source.entity_id)

    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    await reverse(sess, entry.merge_id, reversed_by="user")
    await sess.refresh(edge)

    assert (edge.from_node, edge.to_node) == (target.entity_id, source.entity_id)


async def test_a_reversal_takes_back_the_aliases_the_merge_gave(sess, trio) -> None:
    """Left behind, the source's name among the target's aliases would send
    the next mention of the source straight back into the target."""
    source, target, _ = trio
    original = list(target.aliases)
    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    await sess.refresh(target)
    assert source.canonical_name in target.aliases

    await reverse(sess, entry.merge_id, reversed_by="user")
    await sess.refresh(target)

    assert target.aliases == original


async def test_an_attribute_both_carry_merges_instead_of_failing(sess, trio) -> None:
    """The table allows one value per entity and attribute, so the move itself
    used to be refused by the database. The target keeps its value unless the
    source's tier is strictly higher, and citations unite."""
    source, target, _ = trio
    attribute = await an_attribute(sess)
    kept = await a_value(
        sess, target.entity_id, attribute.attribute_id, value="t", chunks=[1], tier=2
    )
    await a_value(sess, source.entity_id, attribute.attribute_id, value="s", chunks=[2], tier=1)

    await merge(sess, source.entity_id, target.entity_id, decided_by="auto")
    await sess.refresh(kept)
    await sess.refresh(attribute)

    assert (kept.value, kept.quality_tier, kept.supporting_chunk_ids) == ("t", 2, [1, 2])
    assert attribute.usage_count == 1, "one row left the table"


async def test_observations_are_moved_not_folded(sess, trio) -> None:
    """Two identical readings are two pieces of evidence, not one claim; no
    rule in the codebase says otherwise, so a merge does not invent one."""
    source, target, _ = trio
    for entity in (source, target):
        sess.add(
            Observation(
                subject_entity_id=entity.entity_id,
                metric="m",
                value_numeric=1.0,
                supporting_chunk_ids=[1],
            )
        )
    await sess.flush()

    entry = await merge(sess, source.entity_id, target.entity_id, decided_by="auto")

    count = await sess.scalar(
        select(func.count())
        .select_from(Observation)
        .where(Observation.subject_entity_id == target.entity_id)
    )
    assert count == 2
    assert entry.combined is None


# --- the snapshot the fold keeps -------------------------------------------


@pytest.mark.parametrize("model", [Edge, AttributeValue])
async def test_a_snapshot_covers_every_column_and_revives_exactly(sess, trio, model) -> None:
    """Drift: the folded row is kept as a snapshot, and a column the snapshot
    missed would come back empty on reversal while the reversal looked exact.
    The column set is read from the table, never listed."""
    source, target, _ = trio
    if model is Edge:
        row = await an_edge(
            sess,
            source.entity_id,
            target.entity_id,
            valid_from=dt.date(2020, 1, 2),
            topic_labels=["x"],
            contested_with=[1],
            stance="supports",
            certainty="hedged",
            confidence=0.5,
        )
    else:
        attribute = await an_attribute(sess)
        row = await a_value(
            sess, source.entity_id, attribute.attribute_id, value="v", chunks=[1], tier=1
        )
        row.value_json = {"k": [1, 2]}
        row.tagged_at = NOW
        await sess.flush()
    await sess.refresh(row)

    snap = snapshot(row)
    assert set(snap) == {c.key for c in model.__table__.columns}
    revived = revive(model, snap)
    for column in model.__table__.columns:
        assert revived[column.key] == getattr(row, column.key), column.key


# --- the repair pass --------------------------------------------------------


async def a_legacy_duplicate(sess, trio):
    """The state a merge before this fix left: the moved edge beside the
    held one, logged without per-row columns or folds."""
    source, target, other = trio
    held = await an_edge(sess, target.entity_id, other.entity_id, chunks=[1], tier=2)
    moved = await an_edge(sess, source.entity_id, other.entity_id, chunks=[2], tier=3)
    pre_merge = await state(sess, source.entity_id, target.entity_id, other.entity_id)

    moved.from_node = target.entity_id
    source.redirects_to = target.entity_id
    target.merged_from = [source.entity_id]
    entry = MergeLog(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        decided_by="auto",
        moved_edge_ids=[moved.edge_id],
        moved_attribute_value_ids=[],
        moved_observation_ids=[],
    )
    sess.add(entry)
    await sess.flush()
    return held, moved, entry, pre_merge


async def test_the_repair_reports_by_default_and_writes_nothing(sess, trio) -> None:
    held, moved, entry, _ = await a_legacy_duplicate(sess, trio)
    source, target, other = trio
    before = await state(sess, source.entity_id, target.entity_id, other.entity_id)

    report = await fold_repeated_edges(sess, apply=False)

    assert report.groups == 1
    assert report.attributed == [(entry.merge_id, held.edge_id, [moved.edge_id])]
    assert await state(sess, source.entity_id, target.entity_id, other.entity_id) == before


async def test_the_repair_folds_is_idempotent_and_reverses_with_its_merge(sess, trio) -> None:
    """Applied, the duplicate folds into the held edge and the fold is logged
    on the merge that caused it; a second pass does nothing; and reversing
    that merge restores the state before it, exactly."""
    held, moved, entry, pre_merge = await a_legacy_duplicate(sess, trio)
    source, target, other = trio
    merge_id = entry.merge_id

    first = await fold_repeated_edges(sess, apply=True)
    assert first.folded == 1
    await sess.refresh(held)
    assert held.supporting_chunk_ids == [1, 2]
    assert held.quality_tier == 3, "the folded edge's higher tier moved up"
    once = await state(sess, source.entity_id, target.entity_id, other.entity_id)

    second = await fold_repeated_edges(sess, apply=True)
    assert (second.groups, second.folded) == (0, 0)
    assert await state(sess, source.entity_id, target.entity_id, other.entity_id) == once

    await reverse(sess, merge_id, reversed_by="user")
    after = await state(sess, source.entity_id, target.entity_id, other.entity_id)
    assert after["edges"] == pre_merge["edges"]


async def test_a_duplicate_no_merge_explains_is_left_alone(sess, trio) -> None:
    """Without a merge there is no log a reversal would read, and a fold that
    cannot be undone is the one thing the repair must not do."""
    _, target, other = trio
    await an_edge(sess, target.entity_id, other.entity_id)
    await an_edge(sess, target.entity_id, other.entity_id)

    report = await fold_repeated_edges(sess, apply=True)

    assert report.folded == 0
    assert len(report.unattributed) == 1
    assert await triple_count(sess, target.entity_id, "influences", other.entity_id) == 2


async def test_the_command_reports_unless_told_to_apply(sess, trio) -> None:
    held, moved, _, _ = await a_legacy_duplicate(sess, trio)

    @asynccontextmanager
    async def factory():
        sess.commit = sess.flush
        yield sess

    report = await run_pass(apply=False, session_factory=factory)
    assert report.folded == 1
    assert await sess.get(Edge, moved.edge_id) is not None

    await run_pass(apply=True, session_factory=factory)
    sess.expire_all()
    assert await sess.get(Edge, moved.edge_id) is None
