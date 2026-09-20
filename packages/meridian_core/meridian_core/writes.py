"""The four write tools (task `P4-04`, §11.6, §11.8).

§11.6 lists the write surface as "narrow, validated, orchestrator scope only",
and each of those three words is a decision somewhere else in the tree:

*Narrow* is this file — four functions, not an ORM handed to a model.
*Validated* is `validation.py` (`P4-05`), built first so these are callers
rather than authors of the rules. *Orchestrator scope only* is `grants.py`,
where `PROFILE_TOOLS` gives no shared profile a write tool at all, including
`operator`. Nothing here is reachable over the MCP surface an external agent
holds a grant for.

**These are the only writes a model's output ever causes.** §11.1b's point is
that three callers reach the same writes and none gets privileged access, so
the validation lives below this layer and cannot be skipped by arriving from a
different direction.

Four decisions worth stating:

**The same claim twice is corroboration, not a second edge.** Two extractions
of "A supersedes B" from different chunks are one relation with two citations.
Inserting both would double every edge count, and everything built on those
counts — contested pairs, coverage scores, the digest's "edges added" — would
be counting extraction passes rather than knowledge.

**A cheaper model never overwrites a better one.** §11.12's guard is
load-bearing because of the schedule: nightly tier-2 tagging runs far more
often than the frontier sessions producing tier-4 edges, so without it the
cheap work overwrites the good work on a timer while every run reports success.

**A refusal writes nothing, including the queue.** A seed rejected at seed time
must not land in `queue` to be retried with backoff — that turns a rejection
into a schedule for the thing that was rejected.

**Every tool counts what it did on the run.** §11.9 compares cost and volume
per run week on week, and a tool that wrote without counting would make the
run look cheaper than it was.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
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

    `created` separates a new row from an existing one that was corroborated or
    updated. The orchestrator counts only the first towards `edges_added`, and
    a model that cannot tell the difference will keep re-asserting what the
    corpus already holds because nothing told it otherwise.
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
) -> WriteResult:
    """Assert one relation, with the chunks that justify it (§11.6, §11.8).

    Validation first and in one call: `check_edge` exists so a tool cannot pass
    four guards by forgetting the fifth — the failure this design is built
    against is a guard that never ran, which looks exactly like one that
    passed.

    **An existing edge is corroborated rather than duplicated.** Same subject,
    relation and object is the same claim; the new chunks join its citations.
    Scalar judgements — confidence, stance, certainty — are replaced only by a
    *strictly better* tier, because an equal tier disagreeing with itself is a
    contradiction to record (`P7-05`) rather than a value to overwrite.
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
    sess.add(edge)
    await sess.flush()
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

    **The attribute must already be active.** §7.3 caps the comparison
    dimensions and `P7-01` gates proposals; a tool that created a definition on
    first use would route around both, and the cap would be whatever the models
    happened to invent.

    One row per entity, attribute and schema version — the table says so — so a
    re-tag updates rather than accumulating. The downgrade guard applies for
    the same reason it does on edges.
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

    **The cap is reserved before the row is written**, and `cap=None` refuses:
    "nobody configured a cap" must never read as unlimited, because §11.9's
    compounding loop — seeds become documents become more seeds — is the one
    nothing else bounds, and it is unattended.

    **A refused seed leaves no row.** Writing first and validating after would
    leave a rejected target in `queue` to be retried with backoff, which is a
    rejection that has scheduled the thing it rejected.
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

    A tool rather than direct access to `runs.mark`, because §11.6 names it as
    one of the four and a model calling it is making a claim — "everything up
    to here has been reasoned over" — that deserves the same refusals as any
    other write. `mark` itself refuses while unflushed changes are outstanding,
    so a model that asks to advance before its writes have landed is told so.
    """
    await mark(sess, run, chunk_id, now=now)
    return WriteResult(run.run_id, False, f"mark at {chunk_id}")
