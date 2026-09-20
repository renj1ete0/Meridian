"""The four write tools against a real Postgres (task `P4-04`, §11.6, §11.8).

Every one of these is a write a model's output caused, so the tests are mostly
about refusals: a node that does not exist, a chunk that does not resolve, a
relation type that is prose, a cheaper model overwriting a better one, a seed
past the cap. §11.8's framing is that server-side validation is the control and
everything else is defence in depth — this is that control, exercised.

The two that are not refusals are the ones with the most downstream reach: the
same claim asserted twice must corroborate rather than duplicate, and every
tool must count what it did on the run.

**The fixture removes its own rows.** It creates entities and reads real
chunks, because the guards are about rows existing and a fixture that invented
chunk ids would be testing nothing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select

from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    Edge,
    Entity,
    QueueTask,
    Run,
)
from meridian_core.runs import begin_or_resume, unfinished
from meridian_core.validation import ValidationError
from meridian_core.writes import add_edge, advance_mark, enqueue_seed, tag_entity

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)

#: Provenance every derived write must carry (§2.3, §11.12).
BY = {"produced_by": "hosted-frontier", "model": "test-model-v1", "quality_tier": 4}


@pytest.fixture
async def world(session_for) -> AsyncIterator[tuple]:
    """A run, two entities, an attribute and two real chunk ids.

    Real chunks, because `check_chunks_resolve` is about rows existing and a
    fixture that invented ids would be asserting against its own invention.
    """
    sess = await session_for("rw")
    await sess.rollback()

    marker = uuid.uuid4().hex[:8]
    before_runs = {row.run_id for row in await sess.scalars(select(Run))}

    stranded = await unfinished(sess)
    held = None
    if stranded is not None:
        held = (stranded.run_id, stranded.status, stranded.stage, stranded.heartbeat_at)
        stranded.status = "failed"
    await sess.flush()

    chunk_ids = list(await sess.scalars(select(Chunk.chunk_id).limit(2)))
    if len(chunk_ids) < 2:
        pytest.skip("the dev corpus holds fewer than two chunks")

    left = Entity(canonical_name=f"zz-left-{marker}", node_type="organisation")
    right = Entity(canonical_name=f"zz-right-{marker}", node_type="organisation")
    attribute = AttributeDefinition(name=f"zz-attr-{marker}", scope="global", status="active")
    sess.add_all([left, right, attribute])
    await sess.flush()

    run, _ = await begin_or_resume(sess, now=NOW)
    await sess.flush()

    yield sess, run, left, right, attribute, chunk_ids, marker

    await sess.rollback()
    await sess.execute(
        delete(AttributeValue).where(
            AttributeValue.entity_id.in_([left.entity_id, right.entity_id])
        )
    )
    await sess.execute(delete(Edge).where(Edge.from_node.in_([left.entity_id, right.entity_id])))
    await sess.execute(
        delete(Entity).where(Entity.entity_id.in_([left.entity_id, right.entity_id]))
    )
    await sess.execute(delete(AttributeDefinition).where(AttributeDefinition.name.contains(marker)))
    await sess.execute(delete(QueueTask).where(QueueTask.url_or_query.contains(marker)))
    await sess.execute(delete(Run).where(Run.run_id.notin_(before_runs or {-1})))
    if held is not None:
        run_id, status, stage, heartbeat = held
        row = await sess.get(Run, run_id)
        row.status, row.stage, row.heartbeat_at, row.error = status, stage, heartbeat, None
    await sess.commit()


# --------------------------------------------------------------------------
# add_edge
# --------------------------------------------------------------------------


async def test_an_edge_records_its_citations_and_its_provenance(world) -> None:
    sess, run, left, right, _attr, chunks, _m = world

    result = await add_edge(
        sess,
        run,
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=chunks,
        now=NOW,
        **BY,
    )

    edge = await sess.get(Edge, result.row_id)
    assert result.created
    assert edge.supporting_chunk_ids == sorted(chunks)
    assert (edge.produced_by, edge.model, edge.quality_tier) == (
        BY["produced_by"],
        BY["model"],
        BY["quality_tier"],
    )


async def test_the_same_claim_twice_is_corroboration_not_a_second_edge(world) -> None:
    """Two extractions of one relation are one relation with two citations.

    Inserting both would double every edge count, and everything built on them
    — contested pairs, coverage, the digest's "edges added" — would be counting
    extraction passes rather than knowledge.
    """
    sess, run, left, right, _attr, chunks, _m = world
    first = await add_edge(
        sess,
        run,
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=[chunks[0]],
        now=NOW,
        **BY,
    )

    second = await add_edge(
        sess,
        run,
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=[chunks[1]],
        now=NOW,
        **BY,
    )

    assert second.row_id == first.row_id
    assert second.created is False
    edge = await sess.get(Edge, first.row_id)
    assert edge.supporting_chunk_ids == sorted(chunks), "the citations accumulate"


async def test_only_a_new_edge_counts_towards_the_run(world) -> None:
    # §11.9 compares volume per run. Counting corroboration as a new edge would
    # make a run that learned nothing look productive.
    sess, run, left, right, _attr, chunks, _m = world
    args = dict(
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=[chunks[0]],
        now=NOW,
        **BY,
    )

    await add_edge(sess, run, **args)
    await add_edge(sess, run, **args)

    assert run.edges_added == 1


async def test_a_cheaper_model_cannot_overwrite_a_better_one(world) -> None:
    """§11.12's guard, and the schedule is why it matters.

    Nightly tier-2 tagging runs far more often than the frontier sessions that
    produce tier-4 edges, so without this the cheap work overwrites the good
    work on a timer — while every individual run reports success.
    """
    sess, run, left, right, _attr, chunks, _m = world
    await add_edge(
        sess,
        run,
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=[chunks[0]],
        confidence=0.9,
        now=NOW,
        **BY,
    )

    with pytest.raises(ValidationError, match="tier"):
        await add_edge(
            sess,
            run,
            from_node=left.entity_id,
            to_node=right.entity_id,
            relation_type="supersedes",
            supporting_chunk_ids=[chunks[1]],
            produced_by="local-llamacpp",
            model="small-v1",
            quality_tier=2,
            now=NOW,
        )


async def test_a_better_model_replaces_the_judgement_and_keeps_the_citations(world) -> None:
    sess, run, left, right, _attr, chunks, _m = world
    await add_edge(
        sess,
        run,
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=[chunks[0]],
        produced_by="hosted-mid",
        model="mid-v1",
        quality_tier=3,
        confidence=0.5,
        now=NOW,
    )

    await add_edge(
        sess,
        run,
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=[chunks[1]],
        confidence=0.95,
        stance="supports",
        now=NOW,
        **BY,
    )

    edge = await sess.scalar(select(Edge).where(Edge.from_node == left.entity_id))
    assert edge.quality_tier == 4
    assert edge.confidence == pytest.approx(0.95)
    assert edge.supporting_chunk_ids == sorted(chunks), "evidence is never discarded"


@pytest.mark.parametrize(
    "override,rule",
    [
        ({"to_node": 999_999_999}, "node_exists"),
        ({"supporting_chunk_ids": [999_999_999]}, "chunk"),
        ({"relation_type": "is the thing that supersedes, per the document"}, "relation_type"),
        ({"relation_type": "   "}, "relation_type"),
        ({"quality_tier": None}, "provenance"),
        ({"produced_by": "human"}, "provenance"),
    ],
)
async def test_an_invalid_edge_is_refused_and_writes_nothing(world, override, rule) -> None:
    """§11.8: "add an edge to node 99999999" is what a model does after reading
    a page that told it to. A refusal must leave no row behind."""
    sess, run, left, right, _attr, chunks, _m = world
    args = dict(
        from_node=left.entity_id,
        to_node=right.entity_id,
        relation_type="supersedes",
        supporting_chunk_ids=chunks,
        now=NOW,
        **BY,
    )
    args.update(override)

    with pytest.raises(ValidationError):
        await add_edge(sess, run, **args)

    # Not a rollback: that would discard the row whether or not it was written,
    # and the assertion would hold for a tool that wrote one.
    await sess.flush()
    assert not list(await sess.scalars(select(Edge).where(Edge.from_node == left.entity_id)))
    assert run.edges_added == 0


async def test_an_edge_to_itself_is_refused(world) -> None:
    sess, run, left, _right, _attr, chunks, _m = world

    with pytest.raises(ValidationError):
        await add_edge(
            sess,
            run,
            from_node=left.entity_id,
            to_node=left.entity_id,
            relation_type="supersedes",
            supporting_chunk_ids=chunks,
            now=NOW,
            **BY,
        )


async def test_a_comparison_must_state_its_limits(world) -> None:
    """§7.2: every comparison edge carries the dimension *and* the disanalogy.

    Refused here rather than only by the CHECK constraint, so the message names
    the rule instead of the constraint — a model that reads "violates
    comparison_states_its_limits" cannot act on it.
    """
    sess, run, left, right, _attr, chunks, _m = world

    with pytest.raises(ValidationError, match="disanalogy"):
        await add_edge(
            sess,
            run,
            from_node=left.entity_id,
            to_node=right.entity_id,
            relation_type="comparable_to",
            supporting_chunk_ids=chunks,
            similarity_dimension="scale",
            now=NOW,
            **BY,
        )


# --------------------------------------------------------------------------
# tag_entity
# --------------------------------------------------------------------------


async def test_tagging_records_the_value_and_counts_it(world) -> None:
    sess, run, left, _right, attr, chunks, _m = world

    result = await tag_entity(
        sess,
        run,
        entity_id=left.entity_id,
        attribute=attr.name,
        supporting_chunk_ids=chunks,
        value="municipal",
        now=NOW,
        **BY,
    )

    row = await sess.get(AttributeValue, result.row_id)
    assert row.value == "municipal"
    assert row.supporting_chunk_ids == sorted(chunks)
    assert run.tags_added == 1


async def test_tagging_twice_updates_rather_than_accumulating(world) -> None:
    # The table permits one row per entity, attribute and schema version. A
    # tool that inserted regardless would fail at flush, mid-batch.
    sess, run, left, _right, attr, chunks, _m = world
    first = await tag_entity(
        sess,
        run,
        entity_id=left.entity_id,
        attribute=attr.name,
        supporting_chunk_ids=[chunks[0]],
        value="municipal",
        now=NOW,
        **BY,
    )

    second = await tag_entity(
        sess,
        run,
        entity_id=left.entity_id,
        attribute=attr.name,
        supporting_chunk_ids=[chunks[1]],
        value="regional",
        now=NOW,
        **BY,
    )

    assert second.row_id == first.row_id
    row = await sess.get(AttributeValue, first.row_id)
    assert row.value == "regional"
    assert row.supporting_chunk_ids == sorted(chunks)


async def test_an_unknown_attribute_is_not_created_on_first_use(world) -> None:
    """§7.3 caps the comparison dimensions and `P7-01` gates proposals.

    A tool that created a definition on first use would route around both, and
    the cap would become whatever the models happened to invent.
    """
    sess, run, left, _right, _attr, chunks, _m = world

    with pytest.raises(ValidationError, match="never created by tagging"):
        await tag_entity(
            sess,
            run,
            entity_id=left.entity_id,
            attribute="zz-invented-dimension",
            supporting_chunk_ids=chunks,
            value="x",
            now=NOW,
            **BY,
        )


@pytest.mark.parametrize("status", ["proposed", "retired"])
async def test_an_attribute_that_is_not_active_cannot_be_assigned(world, status) -> None:
    sess, run, left, _right, attr, chunks, _m = world
    attr.status = status
    await sess.flush()

    with pytest.raises(ValidationError, match=status):
        await tag_entity(
            sess,
            run,
            entity_id=left.entity_id,
            attribute=attr.name,
            supporting_chunk_ids=chunks,
            value="x",
            now=NOW,
            **BY,
        )


async def test_a_tag_with_no_value_is_refused(world) -> None:
    sess, run, left, _right, attr, chunks, _m = world

    with pytest.raises(ValidationError, match="no value"):
        await tag_entity(
            sess,
            run,
            entity_id=left.entity_id,
            attribute=attr.name,
            supporting_chunk_ids=chunks,
            now=NOW,
            **BY,
        )


async def test_tagging_counts_towards_the_attribute_s_usage(world) -> None:
    # §7.3's audit needs to know how often a dimension is actually used, and a
    # count maintained only by a nightly sweep lags exactly the attributes
    # being adopted fastest.
    sess, run, left, _right, attr, chunks, _m = world
    before = attr.usage_count

    await tag_entity(
        sess,
        run,
        entity_id=left.entity_id,
        attribute=attr.name,
        supporting_chunk_ids=chunks,
        value="x",
        now=NOW,
        **BY,
    )

    assert attr.usage_count == before + 1


# --------------------------------------------------------------------------
# enqueue_seed
# --------------------------------------------------------------------------


async def test_a_seed_is_queued_against_the_cap(world) -> None:
    sess, run, _left, _right, _attr, _chunks, marker = world
    target = f"https://{marker}.example.test/a"

    result = await enqueue_seed(sess, run, target=target, cap=5, now=NOW)

    task = await sess.get(QueueTask, result.row_id)
    assert task.url_or_query == target
    assert task.seed_source == "model"
    assert run.seeds_emitted == 1


async def test_an_unset_cap_refuses(world) -> None:
    """§11.9's compounding loop is the one nothing else bounds, and it is
    unattended — so "nobody configured a cap" must never read as unlimited."""
    sess, run, _left, _right, _attr, _chunks, marker = world

    with pytest.raises(ValidationError):
        await enqueue_seed(sess, run, target=f"https://{marker}.example.test/a", cap=None, now=NOW)


async def test_a_seed_past_the_cap_is_refused_and_queues_nothing(world) -> None:
    sess, run, _left, _right, _attr, _chunks, marker = world
    await enqueue_seed(sess, run, target=f"https://{marker}.example.test/1", cap=1, now=NOW)

    with pytest.raises(ValidationError):
        await enqueue_seed(sess, run, target=f"https://{marker}.example.test/2", cap=1, now=NOW)

    await sess.flush()
    queued = list(
        await sess.scalars(select(QueueTask).where(QueueTask.url_or_query.contains(marker)))
    )
    assert len(queued) == 1, "the first is still there and the second never landed"
    assert run.seeds_emitted == 1


async def test_a_seed_that_reaches_inward_is_refused_before_it_is_queued(world) -> None:
    """A refusal that wrote first would leave the target in `queue` being
    retried with backoff — a rejection that has scheduled the attack."""
    sess, run, _left, _right, _attr, _chunks, _m = world

    with pytest.raises(ValidationError):
        await enqueue_seed(sess, run, target="http://169.254.169.254/latest/", cap=5, now=NOW)

    await sess.flush()
    assert not list(
        await sess.scalars(select(QueueTask).where(QueueTask.url_or_query.contains("169.254")))
    )
    assert run.seeds_emitted == 0, "a refused seed does not spend the cap"


async def test_a_search_term_is_queued_as_a_query(world) -> None:
    sess, run, _left, _right, _attr, _chunks, marker = world

    result = await enqueue_seed(sess, run, target=f"{marker} some question", cap=5, now=NOW)

    task = await sess.get(QueueTask, result.row_id)
    assert task.task_type == "query", "a search term must not be fetched as a URL"


# --------------------------------------------------------------------------
# advance_mark
# --------------------------------------------------------------------------


async def test_the_mark_advances_through_the_tool(world) -> None:
    sess, run, _left, _right, _attr, _chunks, _m = world

    await advance_mark(sess, run, 4242, now=NOW)

    assert run.last_chunk_id == 4242


async def test_the_tool_refuses_to_advance_over_unwritten_work(world) -> None:
    """A model asking to advance before its writes have landed is told so.

    This is §6.3's rule reaching the tool surface: the claim "everything up to
    here has been reasoned over" is false while the reasoning is unflushed.
    """
    sess, run, left, right, _attr, chunks, _m = world
    sess.add(
        Edge(
            from_node=left.entity_id,
            to_node=right.entity_id,
            relation_type="x",
            supporting_chunk_ids=chunks,
        )
    )

    with pytest.raises(ValueError, match="unwritten changes"):
        await advance_mark(sess, run, 10, now=NOW)
