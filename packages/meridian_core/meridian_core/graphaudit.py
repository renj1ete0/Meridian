"""Edge precision sampling and the entity resolution audit (task `P7-10`, spec §14.1).

A draw takes a reproducible random sample of the relations written and the merges made in a
window, and keeps each with what a grader will be shown. A person grades them; precision is
reported per draw and per producer. Nothing here changes the graph: a relation graded wrong
stays, and a merge graded wrong is not reversed. See
docs/features/knowledge-graph.md#the-graph-audit.
"""

from __future__ import annotations

import collections
import datetime as dt
import math
import secrets
from collections.abc import Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .annotations import HUMAN
from .logging import get_logger
from .models import Chunk, Edge, Entity, GraphAuditDraw, GraphAuditSample, MergeLog, Source
from .schemas.graphaudit import (
    NOTE_MAX_CHARS,
    AuditDrawDetailRead,
    AuditDrawRead,
    AuditProducerRead,
    AuditSampleRead,
    AuditTallyRead,
    GraphAuditRead,
)

log = get_logger(__name__)

#: §14.1: "Read 30 random edges monthly."
EDGES_PER_DRAW = 30
#: The spec names no number for merges; fewer are made than relations.
MERGES_PER_DRAW = 20
#: The most either sample may ask for: a draw is read by a person.
MAX_PER_DRAW = 200

#: The first draw looks back this far; each later one starts where the last ended.
FIRST_WINDOW = dt.timedelta(days=30)
#: A draw over less than this is refused: a second run minutes after the first would draw
#: from a handful of rows and split one month's grades into two thin samples.
MIN_WINDOW = dt.timedelta(days=1)

#: Passages kept with a sampled relation, and per side of a merge, and how much of each.
PASSAGES_PER_EDGE = 3
PASSAGES_PER_SIDE = 2
PASSAGE_CHARS = 600

#: The normal quantile for a 95% interval.
Z95 = 1.959964

GRADES = ("correct", "wrong", "unsure")
KINDS = ("edge", "merge")


class AuditRefused(RuntimeError):
    """A draw or a grade that cannot be made, said in words a person can act on."""


# --------------------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------------------


def wilson(correct: int, judged: int, z: float = Z95) -> tuple[float, float] | None:
    """The Wilson score interval for ``correct`` of ``judged``; None when nothing is judged.

    Chosen over the plain ±z·√(p(1−p)/n) because it stays inside [0, 1] and does not
    collapse to a zero-width interval at 30 of 30.
    """
    if judged <= 0:
        return None
    if not 0 <= correct <= judged:
        raise ValueError(f"{correct} correct of {judged} judged is not a proportion")
    p = correct / judged
    z2 = z * z
    denom = 1 + z2 / judged
    centre = (p + z2 / (2 * judged)) / denom
    half = z * math.sqrt(p * (1 - p) / judged + z2 / (4 * judged * judged)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def tally(grades: Iterable[str | None]) -> AuditTallyRead:
    """Counts and precision over a set of grades (None is drawn and not yet graded)."""
    counts = collections.Counter(grades)
    unknown = set(counts) - {None, *GRADES}
    if unknown:
        raise ValueError(f"not a grade: {sorted(map(str, unknown))}")
    correct, wrong, unsure = counts["correct"], counts["wrong"], counts["unsure"]
    judged = correct + wrong
    interval = wilson(correct, judged)
    return AuditTallyRead(
        drawn=sum(counts.values()),
        graded=correct + wrong + unsure,
        correct=correct,
        wrong=wrong,
        unsure=unsure,
        precision=correct / judged if judged else None,
        low=interval[0] if interval else None,
        high=interval[1] if interval else None,
    )


# --------------------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------------------


def _population(kind: str, start: dt.datetime, end: dt.datetime) -> tuple:
    """The table's id column and the filters for what a draw of ``kind`` samples from."""
    if kind == "edge":
        # A reader's note is the reader's own claim, not the corpus's; it is not graded.
        return Edge.edge_id, (
            Edge.created_at >= start,
            Edge.created_at < end,
            or_(Edge.produced_by.is_(None), Edge.produced_by != HUMAN),
        )
    if kind == "merge":
        # A reversed merge has already been judged wrong by somebody; it is counted apart.
        return MergeLog.merge_id, (
            MergeLog.at >= start,
            MergeLog.at < end,
            MergeLog.reversed_at.is_(None),
        )
    raise ValueError(f"not a kind of audited item: {kind!r}")


async def population(sess: AsyncSession, kind: str, start: dt.datetime, end: dt.datetime) -> int:
    """How many items of ``kind`` a draw over ``[start, end)`` samples from."""
    column, where = _population(kind, start, end)
    return int(await sess.scalar(select(func.count(column)).where(*where)) or 0)


async def sample_ids(
    sess: AsyncSession, kind: str, *, seed: str, start: dt.datetime, end: dt.datetime, n: int
) -> list[int]:
    """The first ``n`` ids of the window, ordered by ``md5(seed:id)``: random and repeatable.

    The same seed over the same rows gives the same ids in the same order, so a recorded
    draw can be checked by drawing again. Ties (none in practice) break by id.
    """
    if n <= 0:
        return []
    column, where = _population(kind, start, end)
    order = func.md5(func.concat(seed, ":", column))
    return list(await sess.scalars(select(column).where(*where).order_by(order, column).limit(n)))


async def _passages(sess: AsyncSession, chunk_ids: Sequence[int], limit: int) -> list[dict]:
    """Up to ``limit`` of ``chunk_ids`` with their text and source, in the order given."""
    wanted = list(dict.fromkeys(chunk_ids))[:limit]
    if not wanted:
        return []
    rows = (
        await sess.execute(
            select(Chunk.chunk_id, Chunk.source_id, Chunk.text, Source.title)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id.in_(wanted))
        )
    ).all()
    found = {
        chunk_id: {
            "chunk_id": chunk_id,
            "source_id": source_id,
            "title": title,
            "text": (text or "")[:PASSAGE_CHARS],
        }
        for chunk_id, source_id, text, title in rows
    }
    return [found[i] for i in wanted if i in found]


def _node(entity: Entity | None, entity_id: int) -> dict:
    if entity is None:
        return {"entity_id": entity_id, "name": f"#{entity_id}", "node_type": None}
    return {
        "entity_id": entity.entity_id,
        "name": entity.canonical_name,
        "node_type": entity.node_type,
        "jurisdiction": entity.jurisdiction,
    }


async def _edge_shown(sess: AsyncSession, edge: Edge) -> dict:
    ends = {
        e.entity_id: e
        for e in await sess.scalars(
            select(Entity).where(Entity.entity_id.in_((edge.from_node, edge.to_node)))
        )
    }
    return {
        "from": _node(ends.get(edge.from_node), edge.from_node),
        "relation": edge.relation_type,
        "to": _node(ends.get(edge.to_node), edge.to_node),
        "stance": edge.stance,
        "certainty": edge.certainty,
        "similarity_dimension": edge.similarity_dimension,
        "disanalogy": edge.disanalogy,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "chunk_ids": list(edge.supporting_chunk_ids or ()),
        "passages": await _passages(sess, edge.supporting_chunk_ids or (), PASSAGES_PER_EDGE),
    }


async def _merge_shown(sess: AsyncSession, entry: MergeLog) -> dict:
    source = await sess.get(Entity, entry.source_entity_id)
    target = await sess.get(Entity, entry.target_entity_id)
    before = (entry.target_fields or {}).get("before") or {}

    async def side(entity: Entity | None, entity_id: int, chunks: Sequence[int]) -> dict:
        return {
            **_node(entity, entity_id),
            "description": entity.description if entity else None,
            "passages": await _passages(sess, chunks, PASSAGES_PER_SIDE),
        }

    # The target as it stood before the merge, so the source's chunks and name are not
    # shown as the target's own evidence.
    target_chunks = before.get("supporting_chunk_ids")
    if target_chunks is None and target is not None:
        target_chunks = target.supporting_chunk_ids
    return {
        "source": await side(
            source, entry.source_entity_id, source.supporting_chunk_ids if source else ()
        ),
        "target": {
            **await side(target, entry.target_entity_id, target_chunks or ()),
            "aliases": sorted(before.get("aliases") or ()),
        },
        "score": entry.score,
        "signals": entry.signals,
        "decided_by": entry.decided_by,
        "merged_at": entry.at.isoformat(),
        "moved_edges": len(entry.moved_edge_ids or ()),
    }


async def draw(
    sess: AsyncSession,
    *,
    now: dt.datetime,
    edges: int = EDGES_PER_DRAW,
    merges: int = MERGES_PER_DRAW,
    seed: str | None = None,
    since: dt.datetime | None = None,
    drawn_by: str = "scheduler",
) -> GraphAuditDraw | None:
    """Sample the relations and merges made since the last draw, for a person to grade.

    The window is ``[since, now)``: ``since`` defaults to where the last draw ended, or
    :data:`FIRST_WINDOW` before ``now`` for the first. None, and nothing recorded, when the
    window holds nothing to sample, so the next draw covers it. Refuses a window shorter
    than :data:`MIN_WINDOW` and sizes outside ``0..MAX_PER_DRAW``. Flushes; does not commit.
    """
    if now.tzinfo is None:
        raise AuditRefused("A draw needs an instant with a zone, not a local time.")
    for name, size in (("edges", edges), ("merges", merges)):
        if not 0 <= size <= MAX_PER_DRAW:
            raise AuditRefused(f"{name}={size}: a draw takes 0 to {MAX_PER_DRAW} of each.")
    if edges == 0 and merges == 0:
        raise AuditRefused("A draw of nothing: ask for some relations or some merges.")

    if since is None:
        last = await sess.scalar(select(func.max(GraphAuditDraw.window_end)))
        since = last or now - FIRST_WINDOW
    if now - since < MIN_WINDOW:
        raise AuditRefused(
            f"Too soon: the window would start at {since.isoformat()}, less than "
            f"{MIN_WINDOW.days} day before now."
        )

    seed = seed or secrets.token_hex(8)
    edge_pop = await population(sess, "edge", since, now)
    merge_pop = await population(sess, "merge", since, now)
    reversed_count = int(
        await sess.scalar(
            select(func.count(MergeLog.merge_id)).where(
                MergeLog.at >= since, MergeLog.at < now, MergeLog.reversed_at.is_not(None)
            )
        )
        or 0
    )
    if edge_pop == 0 and merge_pop == 0:
        log.info(
            "graph audit: nothing to sample",
            extra={"since": since.isoformat(), "through": now.isoformat()},
        )
        return None

    row = GraphAuditDraw(
        seed=seed,
        window_start=since,
        window_end=now,
        edge_population=edge_pop,
        merge_population=merge_pop,
        merges_reversed=reversed_count,
        drawn_by=drawn_by,
    )
    sess.add(row)
    await sess.flush()

    edge_ids = await sample_ids(sess, "edge", seed=seed, start=since, end=now, n=edges)
    for position, edge_id in enumerate(edge_ids):
        edge = await sess.get(Edge, edge_id)
        sess.add(
            GraphAuditSample(
                draw_id=row.draw_id,
                kind="edge",
                item_id=edge_id,
                position=position,
                shown=await _edge_shown(sess, edge),
                produced_by=edge.produced_by,
                model=edge.model,
                quality_tier=edge.quality_tier,
            )
        )
    merge_ids = await sample_ids(sess, "merge", seed=seed, start=since, end=now, n=merges)
    for position, merge_id in enumerate(merge_ids, start=len(edge_ids)):
        entry = await sess.get(MergeLog, merge_id)
        sess.add(
            GraphAuditSample(
                draw_id=row.draw_id,
                kind="merge",
                item_id=merge_id,
                position=position,
                shown=await _merge_shown(sess, entry),
                produced_by=entry.decided_by,
            )
        )
    await sess.flush()
    log.info(
        "graph audit drawn",
        extra={
            "draw_id": row.draw_id,
            "edges": len(edge_ids),
            "merges": len(merge_ids),
            "edge_population": edge_pop,
            "merge_population": merge_pop,
        },
    )
    return row


# --------------------------------------------------------------------------------------
# Grading and reporting
# --------------------------------------------------------------------------------------


def _iso(moment: dt.datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _sample_read(sample: GraphAuditSample) -> AuditSampleRead:
    return AuditSampleRead(
        sample_id=sample.sample_id,
        draw_id=sample.draw_id,
        kind=sample.kind,
        item_id=sample.item_id,
        position=sample.position,
        shown=sample.shown,
        grade=sample.grade,
        note=sample.note,
        graded_by=sample.graded_by,
        graded_at=_iso(sample.graded_at),
    )


async def grade(
    sess: AsyncSession,
    sample_id: int,
    verdict: str,
    *,
    graded_by: str,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> AuditSampleRead:
    """Record a person's grade on a sampled item, replacing any earlier one.

    Changes only the sample: the relation or merge it judges is left exactly as it is.
    Flushes; does not commit.
    """
    if verdict not in GRADES:
        raise AuditRefused(f"{verdict!r} is not a grade; correct, wrong or unsure.")
    if not graded_by:
        raise AuditRefused("A grade needs to say who gave it.")
    cleaned = (note or "").strip() or None
    if cleaned is not None and len(cleaned) > NOTE_MAX_CHARS:
        raise AuditRefused(f"A note is at most {NOTE_MAX_CHARS} characters.")
    sample = await sess.get(GraphAuditSample, sample_id, with_for_update=True)
    if sample is None:
        raise AuditRefused(f"No sampled item {sample_id}.")
    sample.grade = verdict
    sample.note = cleaned
    sample.graded_by = graded_by
    sample.graded_at = now or dt.datetime.now(dt.UTC)
    await sess.flush()
    return _sample_read(sample)


def _draw_read(row: GraphAuditDraw, samples: Sequence[GraphAuditSample]) -> AuditDrawRead:
    producers: dict[tuple[str, str | None, str | None], list[str | None]] = {}
    for s in samples:
        producers.setdefault((s.kind, s.produced_by, s.model), []).append(s.grade)
    return AuditDrawRead(
        draw_id=row.draw_id,
        drawn_at=row.created_at.isoformat(),
        window_start=row.window_start.isoformat(),
        window_end=row.window_end.isoformat(),
        seed=row.seed,
        drawn_by=row.drawn_by,
        edge_population=row.edge_population,
        merge_population=row.merge_population,
        merges_reversed=row.merges_reversed,
        edges=tally(s.grade for s in samples if s.kind == "edge"),
        merges=tally(s.grade for s in samples if s.kind == "merge"),
        by_producer=[
            AuditProducerRead(kind=kind, produced_by=who, model=model, tally=tally(grades))
            for (kind, who, model), grades in sorted(
                producers.items(), key=lambda item: (item[0][0], str(item[0][1]), str(item[0][2]))
            )
        ],
    )


async def report(sess: AsyncSession, *, limit: int = 24) -> GraphAuditRead:
    """The newest ``limit`` draws with their precision, and how much is left to grade."""
    draws = list(
        await sess.scalars(
            select(GraphAuditDraw).order_by(GraphAuditDraw.window_end.desc()).limit(limit)
        )
    )
    by_draw: dict[int, list[GraphAuditSample]] = collections.defaultdict(list)
    if draws:
        for s in await sess.scalars(
            select(GraphAuditSample).where(GraphAuditSample.draw_id.in_([d.draw_id for d in draws]))
        ):
            by_draw[s.draw_id].append(s)
    ungraded = int(
        await sess.scalar(
            select(func.count(GraphAuditSample.sample_id)).where(GraphAuditSample.grade.is_(None))
        )
        or 0
    )
    return GraphAuditRead(
        draws=[_draw_read(d, by_draw[d.draw_id]) for d in draws], ungraded=ungraded
    )


async def draw_detail(sess: AsyncSession, draw_id: int) -> AuditDrawDetailRead:
    """One draw and every item in it, in the order drawn."""
    row = await sess.get(GraphAuditDraw, draw_id)
    if row is None:
        raise AuditRefused(f"No draw {draw_id}.")
    samples = list(
        await sess.scalars(
            select(GraphAuditSample)
            .where(GraphAuditSample.draw_id == draw_id)
            .order_by(GraphAuditSample.position)
        )
    )
    return AuditDrawDetailRead(
        draw=_draw_read(row, samples), samples=[_sample_read(s) for s in samples]
    )


def _percent(t: AuditTallyRead) -> str:
    if t.precision is None:
        return "not graded"
    return f"{t.precision:.0%} ({t.low:.0%}–{t.high:.0%}) of {t.correct + t.wrong} judged"


def summary(current: AuditDrawRead, previous: AuditDrawRead | None) -> str:
    """The message a draw sends: what to grade now, and how the last draw came out."""
    lines = [
        f"Graph audit: {current.edges.drawn} relations and {current.merges.drawn} merges to grade",
        "",
        f"drawn from {current.edge_population} relations and {current.merge_population} "
        f"merges since {current.window_start[:10]}",
    ]
    if current.merges_reversed:
        lines.append(f"{current.merges_reversed} merges in the window were already reversed")
    if previous is not None:
        lines.append(
            f"last draw: relations {_percent(previous.edges)}; merges {_percent(previous.merges)}"
        )
    return "\n".join(lines)
