"""The four write tools (task `P4-04`, §11.6, §11.8).

The only writes a model's output ever causes. Validation lives below them in
`validation.py`; no MCP profile holds them (`grants.py`). The same claim twice is
corroboration, a lower tier never overwrites a higher one, a refusal writes nothing, and
every tool counts what it did on the run. See
docs/features/knowledge-graph.md#writes-and-validation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import AttributeDefinition, AttributeValue, Edge, Run
from .models.graph import COMPARISON_RELATION
from .queueing import enqueue
from .runs import mark, record
from .validation import (
    ValidationError,
    check_chunks_resolve,
    check_edge,
    check_nodes_exist,
    check_provenance,
    check_seed_allowed,
    check_tier_not_downgraded,
    reserve_seeds,
)

log = get_logger(__name__)

__all__ = [
    "WriteResult",
    "add_edge",
    "advance_mark",
    "enqueue_seed",
    "tag_entity",
]


@dataclasses.dataclass(frozen=True)
class WriteResult:
    """What a write tool did, in terms the caller can act on.

    `created` separates a new row from one corroborated or updated; only new rows count
    towards `edges_added`.
    """

    row_id: int
    created: bool
    detail: str


def _union(existing: Sequence[int] | None, incoming: Sequence[int]) -> list[int]:
    """Citations accumulate. Sorted so two runs produce the same array."""
    return sorted({*(existing or []), *incoming})


async def add_edge(
    sess: AsyncSession,
    run: Run,
    *,
    from_node: int,
    to_node: int,
    relation_type: str,
    supporting_chunk_ids: Sequence[int],
    produced_by: str | None,
    model: str | None,
    quality_tier: int | None,
    confidence: float | None = None,
    stance: str | None = None,
    certainty: str | None = None,
    topic_labels: Sequence[str] | None = None,
    similarity_dimension: str | None = None,
    disanalogy: str | None = None,
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    now: dt.datetime,
    _retry: bool = False,
) -> WriteResult:
    """Assert one relation, with the chunks that justify it (§11.6, §11.8).

    Validated first by `check_edge`. An existing claim gains the new chunks as citations;
    confidence, stance and certainty are replaced only by a strictly better tier.
    """
    await check_edge(
        sess,
        from_node=from_node,
        to_node=to_node,
        relation_type=relation_type,
        supporting_chunk_ids=supporting_chunk_ids,
        produced_by=produced_by,
        model=model,
        quality_tier=quality_tier,
    )
    if relation_type == COMPARISON_RELATION and not (similarity_dimension and disanalogy):
        # The database enforces this too. Refused here so the message names the
        # rule rather than the constraint (§7.2).
        raise ValidationError(
            "comparison_states_its_limits",
            f"A {COMPARISON_RELATION!r} edge must state its similarity dimension "
            "and its disanalogy (§7.2).",
        )

    existing = await sess.scalar(
        select(Edge)
        .where(
            Edge.from_node == from_node,
            Edge.to_node == to_node,
            Edge.relation_type == relation_type,
        )
        .with_for_update()
    )

    if existing is not None:
        check_tier_not_downgraded(existing=existing.quality_tier, incoming=quality_tier)
        before = len(existing.supporting_chunk_ids or [])
        existing.supporting_chunk_ids = _union(existing.supporting_chunk_ids, supporting_chunk_ids)
        added = len(existing.supporting_chunk_ids) - before

        if quality_tier is not None and (existing.quality_tier or 0) < quality_tier:
            existing.confidence = confidence
            existing.stance = stance
            existing.certainty = certainty
            existing.produced_by = produced_by
            existing.model = model
            existing.quality_tier = quality_tier
            existing.produced_at = now
        await sess.flush()
        return WriteResult(
            existing.edge_id, False, f"corroborated with {added} further citation(s)"
        )

    edge = Edge(
        from_node=from_node,
        to_node=to_node,
        relation_type=relation_type,
        supporting_chunk_ids=sorted(set(supporting_chunk_ids)),
        confidence=confidence,
        stance=stance,
        certainty=certainty,
        topic_labels=list(topic_labels) if topic_labels else None,
        similarity_dimension=similarity_dimension,
        disanalogy=disanalogy,
        valid_from=valid_from,
        valid_to=valid_to,
        produced_by=produced_by,
        model=model,
        quality_tier=quality_tier,
        produced_at=now,
    )
    try:
        # The claim constraint is deferred to commit for merges' sake (`B-60`);
        # here it is checked at the insert, inside a savepoint, so a writer that
        # loses the race finds out now and the caller's transaction survives.
        async with sess.begin_nested():
            await sess.execute(text("SET CONSTRAINTS uq_edges_claim IMMEDIATE"))
            sess.add(edge)
            await sess.flush()
    except IntegrityError as exc:
        if "uq_edges_claim" not in str(exc.orig) or _retry:
            raise
        lost = True
    else:
        lost = False
    finally:
        await sess.execute(text("SET CONSTRAINTS uq_edges_claim DEFERRED"))
    if lost:
        # Another writer inserted this claim between our read and our insert.
        # Theirs stands; ours corroborates it, as if we had come second.
        return await add_edge(
            sess,
            run,
            from_node=from_node,
            to_node=to_node,
            relation_type=relation_type,
            supporting_chunk_ids=supporting_chunk_ids,
            confidence=confidence,
            stance=stance,
            certainty=certainty,
            topic_labels=topic_labels,
            similarity_dimension=similarity_dimension,
            disanalogy=disanalogy,
            valid_from=valid_from,
            valid_to=valid_to,
            produced_by=produced_by,
            model=model,
            quality_tier=quality_tier,
            now=now,
            _retry=True,
        )
    await record(sess, run, edges=1)
    return WriteResult(edge.edge_id, True, "edge created")


async def tag_entity(
    sess: AsyncSession,
    run: Run,
    *,
    entity_id: int,
    attribute: str,
    supporting_chunk_ids: Sequence[int],
    produced_by: str | None,
    model: str | None,
    quality_tier: int | None,
    value: str | None = None,
    value_numeric: float | None = None,
    value_json: dict[str, Any] | None = None,
    confidence: float | None = None,
    now: dt.datetime,
) -> WriteResult:
    """Assign one audited attribute to one entity (§7.3, §11.6).

    The attribute must already be active. One row per entity, attribute and schema
    version, so a re-tag updates; the downgrade guard applies as on edges.
    """
    check_provenance(produced_by=produced_by, model=model, quality_tier=quality_tier)
    if value is None and value_numeric is None and value_json is None:
        raise ValidationError("attribute_value", f"{attribute!r} was tagged with no value.")

    await check_nodes_exist(sess, [entity_id])
    await check_chunks_resolve(sess, supporting_chunk_ids)

    definition = await sess.scalar(
        select(AttributeDefinition).where(AttributeDefinition.name == attribute)
    )
    if definition is None:
        raise ValidationError(
            "attribute_exists",
            f"No attribute {attribute!r}. Attributes are proposed and audited "
            "(§7.3), never created by tagging.",
        )
    if definition.status != "active":
        raise ValidationError(
            "attribute_active",
            f"{attribute!r} is {definition.status}, not active; it cannot be assigned.",
        )

    existing = await sess.scalar(
        select(AttributeValue)
        .where(
            AttributeValue.entity_id == entity_id,
            AttributeValue.attribute_id == definition.attribute_id,
            AttributeValue.schema_version == definition.schema_version,
        )
        .with_for_update()
    )

    if existing is not None:
        check_tier_not_downgraded(existing=existing.quality_tier, incoming=quality_tier)
        existing.supporting_chunk_ids = _union(existing.supporting_chunk_ids, supporting_chunk_ids)
        existing.value = value
        existing.value_numeric = value_numeric
        existing.value_json = value_json
        existing.confidence = confidence
        existing.produced_by = produced_by
        existing.model = model
        existing.quality_tier = quality_tier
        existing.produced_at = now
        existing.tagged_at = now
        await sess.flush()
        return WriteResult(existing.value_id, False, f"{attribute} updated")

    row = AttributeValue(
        entity_id=entity_id,
        attribute_id=definition.attribute_id,
        schema_version=definition.schema_version,
        value=value,
        value_numeric=value_numeric,
        value_json=value_json,
        confidence=confidence,
        supporting_chunk_ids=sorted(set(supporting_chunk_ids)),
        tagged_at=now,
        produced_by=produced_by,
        model=model,
        quality_tier=quality_tier,
        produced_at=now,
    )
    sess.add(row)
    await sess.flush()
    # §7.3's audit needs to know how often a dimension is actually used; a
    # count maintained only by a nightly sweep would lag exactly the attributes
    # being adopted fastest.
    definition.usage_count += 1
    await sess.flush()
    await record(sess, run, tags=1)
    return WriteResult(row.value_id, True, f"{attribute} tagged")


async def enqueue_seed(
    sess: AsyncSession,
    run: Run,
    *,
    target: str,
    cap: int | None,
    topic: str | None = None,
    priority: int = 50,
    now: dt.datetime,
) -> WriteResult:
    """Queue one seed against this run's cap (§11.4, §11.9).

    The cap is reserved before the row is written, and `cap=None` refuses. A refused
    seed leaves no row.
    """
    if not target or not target.strip():
        raise ValidationError("seed_url", "A seed needs a target.")

    task_type = "url" if "://" in target else "query"
    if task_type == "url":
        # The same checks an operator's own `/seed` gets. A model-proposed
        # target is not more trustworthy for having been reasoned about.
        await check_seed_allowed(sess, target)

    await reserve_seeds(sess, run.run_id, 1, cap=cap)
    task = await enqueue(
        sess, target, topic=topic, seed_source="model", task_type=task_type, priority=priority
    )
    log.info("seed queued by a run", extra={"run": run.run_id, "kind": task_type})
    return WriteResult(task.task_id, True, f"queued {task_type}")


async def advance_mark(
    sess: AsyncSession, run: Run, chunk_id: int, *, now: dt.datetime
) -> WriteResult:
    """Move the high-water mark, after the writes it covers (§6.3, `P4-11`).

    Refused while unflushed changes are outstanding (`runs.mark`).
    """
    await mark(sess, run, chunk_id, now=now)
    return WriteResult(run.run_id, False, f"mark at {chunk_id}")
