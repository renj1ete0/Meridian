"""Contradiction tracking: which edges disagree, marked on both (task `P7-05`, §9).

Two edges contradict when they join the same subject to the same object and either
assert the same relation from opposite sides (one passage argues for it, another
against), or assert opposite relations (`increases` against `reduces`) both argued for.
Nothing is resolved: both edges stay and each names the other in `contested_with`.
See docs/features/knowledge-graph.md#contradictions.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Edge
from .models.graph import OPPOSED_STANCE

log = get_logger(__name__)

__all__ = [
    "AGAINST",
    "OPPOSITE_RELATIONS",
    "ContestedReport",
    "contradicts",
    "counterparts",
    "mark",
    "opposed",
    "opposites",
    "reconcile",
]

#: The stance that puts an assertion on the other side of its claim. `mixed`,
#: `neutral` and `unclear` take no side, so they corroborate the claim as stated.
AGAINST = OPPOSED_STANCE

#: Relations that cannot both hold between one subject and one object. Relations are
#: open (§5.4), so this names only directional pairs extraction is seen to produce; an
#: unlisted antonym is simply not detected.
OPPOSITE_RELATIONS: tuple[tuple[str, str], ...] = (
    ("increases", "reduces"),
    ("increases", "decreases"),
    ("improves", "worsens"),
    ("enables", "obstructs"),
    ("enables", "prevents"),
    ("causes", "prevents"),
    ("supports", "opposes"),
)


def opposites(relation: str) -> frozenset[str]:
    """The relations listed as unable to hold alongside ``relation``."""
    return frozenset(b if a == relation else a for a, b in OPPOSITE_RELATIONS if relation in (a, b))


def opposed(stance: str | None) -> bool:
    """Whether an assertion with this stance argues against its claim.

    The same rule as the `edges.opposed` generated column, which keys the claim.
    """
    return stance == AGAINST


def contradicts(a: Edge, b: Edge) -> bool:
    """Whether two edges disagree in a way §9 marks.

    Pure: reads the edges' own fields, so it holds for rows not yet flushed. Two denials
    of opposite relations ("does not increase", "does not reduce") are compatible, and
    are not marked.
    """
    if a.edge_id is not None and a.edge_id == b.edge_id:
        return False
    if (a.from_node, a.to_node) != (b.from_node, b.to_node):
        return False
    if a.relation_type == b.relation_type:
        return opposed(a.stance) != opposed(b.stance)
    return (
        b.relation_type in opposites(a.relation_type)
        and not opposed(a.stance)
        and not opposed(b.stance)
    )


def _counterpart_clause(edge: Edge):
    side = opposed(edge.stance)
    rivals = sorted(opposites(edge.relation_type))
    same_claim = and_(Edge.relation_type == edge.relation_type, Edge.opposed.is_(not side))
    if side or not rivals:
        return same_claim
    return or_(same_claim, and_(Edge.relation_type.in_(rivals), Edge.opposed.is_(False)))


async def counterparts(sess: AsyncSession, edge: Edge) -> list[Edge]:
    """Every edge that contradicts ``edge``, lowest id first, locked for update."""
    rows = (
        await sess.scalars(
            select(Edge)
            .where(
                Edge.from_node == edge.from_node,
                Edge.to_node == edge.to_node,
                Edge.edge_id != edge.edge_id,
                _counterpart_clause(edge),
            )
            .order_by(Edge.edge_id)
            .with_for_update()
        )
    ).all()
    # The SQL reads the generated column; the pure rule reads stance. Both must agree.
    return [row for row in rows if contradicts(edge, row)]


def _name(edge: Edge, other_id: int) -> dict[str, list[int]] | None:
    """Add ``other_id`` to ``edge.contested_with``; the before and after, or None."""
    # NULL stays NULL in `before`, so a reversal restores "never marked" exactly.
    was = None if edge.contested_with is None else list(edge.contested_with)
    if other_id in (was or ()):
        return None
    edge.contested_with = sorted({*(was or ()), other_id})
    return {"before": was, "after": list(edge.contested_with)}


async def mark(sess: AsyncSession, edge: Edge) -> list[dict]:
    """Mark ``edge`` and every edge contradicting it as contested with each other.

    Additive and idempotent: a mark already present is left, none is removed. Returns
    one ``{"edge_id", "before", "after"}`` per edge whose `contested_with` changed, so a
    merge can log what it marked and a reversal can take exactly that back.
    """
    await sess.flush()
    changes: dict[int, dict] = {}
    for other in await counterparts(sess, edge):
        for row, named in ((edge, other.edge_id), (other, edge.edge_id)):
            change = _name(row, named)
            if change is None:
                continue
            first = changes.setdefault(row.edge_id, {"edge_id": row.edge_id, **change})
            first["after"] = change["after"]
    if changes:
        await sess.flush()
        log.info(
            "contradiction marked",
            extra={"edge_id": edge.edge_id, "edges": sorted(changes)},
        )
    return list(changes.values())


@dataclasses.dataclass
class ContestedReport:
    """What a sweep over the whole graph found, and what it did or would do."""

    #: Contradicting pairs found, each once as (lower id, higher id).
    pairs: list[tuple[int, int]] = dataclasses.field(default_factory=list)
    #: Pairs not yet marked on both edges (marked by the sweep when it applies).
    unmarked: list[tuple[int, int]] = dataclasses.field(default_factory=list)
    #: Marks naming an edge the rule does not find contradicting. Reported, never removed:
    #: removing a mark would be resolving a disagreement.
    unexplained: list[tuple[int, int]] = dataclasses.field(default_factory=list)


def _pairs(edges: Iterable[Edge]) -> list[tuple[int, int]]:
    by_endpoints: dict[tuple[int, int], list[Edge]] = {}
    for edge in edges:
        by_endpoints.setdefault((edge.from_node, edge.to_node), []).append(edge)
    found: set[tuple[int, int]] = set()
    for group in by_endpoints.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if contradicts(a, b):
                    found.add((min(a.edge_id, b.edge_id), max(a.edge_id, b.edge_id)))
    return sorted(found)


async def reconcile(sess: AsyncSession, *, apply: bool) -> ContestedReport:
    """Find every contradiction in the graph and, with ``apply``, mark the unmarked.

    For edges written before `P7-05`, and for the pairs a merge brings onto one subject
    and object. Reads only subject-object pairs holding more than one edge.
    """
    crowded = (
        select(Edge.from_node, Edge.to_node)
        .group_by(Edge.from_node, Edge.to_node)
        .having(func.count() > 1)
        .subquery()
    )
    query = (
        select(Edge)
        .join(
            crowded,
            and_(Edge.from_node == crowded.c.from_node, Edge.to_node == crowded.c.to_node),
        )
        .order_by(Edge.edge_id)
    )
    if apply:
        query = query.with_for_update(of=Edge)
    edges = (await sess.scalars(query)).all()
    by_id = {edge.edge_id: edge for edge in edges}

    report = ContestedReport(pairs=_pairs(edges))
    expected = set(report.pairs)
    for low, high in report.pairs:
        if high not in (by_id[low].contested_with or ()) or low not in (
            by_id[high].contested_with or ()
        ):
            report.unmarked.append((low, high))
            if apply:
                _name(by_id[low], high)
                _name(by_id[high], low)

    marked = (
        await sess.execute(
            select(Edge.edge_id, Edge.contested_with).where(Edge.contested_with.is_not(None))
        )
    ).all()
    report.unexplained = sorted(
        {
            (min(edge_id, other), max(edge_id, other))
            for edge_id, named in marked
            for other in named or ()
            if (min(edge_id, other), max(edge_id, other)) not in expected
        }
    )
    if apply:
        await sess.flush()
    log.info(
        "contested sweep",
        extra={
            "pairs": len(report.pairs),
            "unmarked": len(report.unmarked),
            "unexplained": len(report.unexplained),
            "applied": apply,
        },
    )
    return report
