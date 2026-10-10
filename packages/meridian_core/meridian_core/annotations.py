"""Annotation as first-class nodes (task P6-05, spec §12.5, §2 principle 3).

A note is an ``entities`` row with ``node_type='annotation'``, attached by ``annotates``
edges. This module is the only writer: it sets authorship (never a tier or model), and
rewrites the edges, which carry the note's own citations, on every change. See
docs/features/knowledge-graph.md#annotations.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk, Edge, Entity
from .schemas.annotations import (
    MAX_ABOUT,
    MAX_CITED,
    AnnotationCreate,
    AnnotationEdit,
    AnnotationRead,
    AnnotationsRead,
    AnnotationTarget,
)

log = get_logger(__name__)

__all__ = [
    "ANNOTATES",
    "ANNOTATION",
    "HUMAN",
    "MAX_ABOUT",
    "MAX_CITED",
    "create",
    "edit",
    "hydrate",
    "listing",
    "to_markdown",
    "withdraw",
]

#: The reserved `produced_by`, deliberately not shaped like an agent id;
#: `scripts/seed.py` refuses to register an agent under it.
HUMAN = "human"

#: The relation a note attaches by. One relation rather than a vocabulary
#: ("disputes", "confirms"): the note's prose already says which, and a
#: controlled list would make the reader classify a thought before writing it.
ANNOTATES = "annotates"

#: `entities.node_type`, already in the typed ontology (§5.4).
ANNOTATION = "annotation"


async def create(sess: AsyncSession, body: AnnotationCreate) -> Entity:
    """Write a note. Raises before writing anything if it cannot be written whole.

    `LookupError` for a node that does not exist, `ValueError` for a chunk that does not.
    """
    await _targets_exist(sess, body.about)
    await _citations_resolve(sess, body.supporting_chunk_ids)

    note = Entity(
        canonical_name=body.title,
        node_type=ANNOTATION,
        description=body.body,
        topic_labels=body.topic_labels,
        is_annotation=True,
        supporting_chunk_ids=list(body.supporting_chunk_ids),
        produced_by=HUMAN,
        # Explicitly null rather than merely left out: see the module docstring.
        model=None,
        quality_tier=None,
    )
    sess.add(note)
    await sess.flush()

    _attach(sess, note, body.about, body.supporting_chunk_ids)
    await sess.commit()
    await sess.refresh(note)
    log.info("annotation written", extra={"entity_id": note.entity_id, "about": len(body.about)})
    return note


async def edit(sess: AsyncSession, entity_id: int, change: AnnotationEdit) -> Entity:
    """Rewrite a note.

    Unset fields are left alone; ``about`` given replaces ``about``. Raises `LookupError`
    for anything that is not the reader's own note, including a derived entity.
    """
    note = await _own_note(sess, entity_id)
    changes = change.model_dump(exclude_unset=True)

    about = changes.get("about")
    cited = changes.get("supporting_chunk_ids")
    if about is not None:
        await _targets_exist(sess, about)
    if cited is not None:
        await _citations_resolve(sess, cited)

    if "title" in changes and changes["title"] is not None:
        note.canonical_name = changes["title"]
    if "body" in changes:
        note.description = changes["body"]
    if "topic_labels" in changes:
        note.topic_labels = changes["topic_labels"]
    if cited is not None:
        note.supporting_chunk_ids = list(cited)

    # When the text was last the author's, which is a different question from
    # when the row appeared: a list ordered by `created_at` shows a note
    # rewritten today in the position it had in March.
    note.produced_at = func.now()

    # Rebuilt wholesale when the targets or citations moved: the edges hold nothing the
    # note does not.
    if about is not None or cited is not None:
        targets = about if about is not None else await _current_targets(sess, entity_id)
        await sess.execute(delete(Edge).where(Edge.from_node == entity_id))
        _attach(sess, note, targets, note.supporting_chunk_ids)

    await sess.commit()
    await sess.refresh(note)
    return note


def _attach(sess: AsyncSession, note: Entity, about: Sequence[int], cited: Sequence[int]) -> None:
    """One `annotates` edge per target, each carrying the note's citations.

    Deduplicated, because ``about=[7, 7]`` is a reader clicking twice and two
    identical edges would double this note in every traversal that counts them.
    """
    for target in dict.fromkeys(about):
        sess.add(
            Edge(
                from_node=note.entity_id,
                to_node=target,
                relation_type=ANNOTATES,
                supporting_chunk_ids=list(cited),
                topic_labels=note.topic_labels,
                produced_by=HUMAN,
                model=None,
                quality_tier=None,
                created_by=HUMAN,
            )
        )


async def withdraw(sess: AsyncSession, entity_id: int, *, withdrawn: bool = True) -> Entity:
    """Withdraw a note, or put a withdrawn one back (`B-201`, ADR 0020).

    The row and its `annotates` edges are kept: a withdrawal is undone by clearing the
    stamp, and nothing that names the note loses it. Withdrawn, it leaves every list, node
    panel, export and graph walk. Raises `LookupError` for anything that is not a note.
    Withdrawing twice keeps the first stamp.
    """
    note = await _own_note(sess, entity_id, withdrawn=None)
    if withdrawn and note.withdrawn_at is None:
        note.withdrawn_at = func.now()
    elif not withdrawn:
        note.withdrawn_at = None
    await sess.commit()
    await sess.refresh(note)
    log.info(
        "annotation withdrawn" if withdrawn else "annotation restored",
        extra={"entity_id": entity_id},
    )
    return note


async def _own_note(
    sess: AsyncSession, entity_id: int, *, withdrawn: bool | None = False
) -> Entity:
    """The note, or `LookupError` — including when the row exists and is not one.

    ``withdrawn=False`` (an edit) refuses a withdrawn note too: it is put back before it
    is rewritten. None takes it either way.

    Not found rather than forbidden. "This is a corpus node, not yours" would
    answer a question the caller did not ask and tell an unidentified caller
    which entity ids exist.
    """
    note = await sess.get(Entity, entity_id)
    if note is None or not note.is_annotation:
        raise LookupError(f"No annotation {entity_id}.")
    if withdrawn is False and note.withdrawn_at is not None:
        raise LookupError(f"Annotation {entity_id} is withdrawn; restore it to edit it.")
    return note


async def _targets_exist(sess: AsyncSession, about: Sequence[int]) -> None:
    if not about:
        return
    wanted = set(about)
    found = set(await sess.scalars(select(Entity.entity_id).where(Entity.entity_id.in_(wanted))))
    missing = sorted(wanted - found)
    if missing:
        raise LookupError(f"No such node{'' if len(missing) == 1 else 's'}: {missing}.")


async def _citations_resolve(sess: AsyncSession, cited: Sequence[int]) -> None:
    """Refuse a chunk id the corpus cannot follow.

    §2 principle 3; a reader trusts this layer without re-checking.
    """
    if not cited:
        return
    wanted = set(cited)
    found = set(await sess.scalars(select(Chunk.chunk_id).where(Chunk.chunk_id.in_(wanted))))
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(
            f"No such chunk{'' if len(missing) == 1 else 's'}: {missing}. "
            "A note may cite nothing, but not something that is not there."
        )


async def _current_targets(sess: AsyncSession, entity_id: int) -> list[int]:
    return list(
        await sess.scalars(
            select(Edge.to_node)
            .where(Edge.from_node == entity_id, Edge.relation_type == ANNOTATES)
            .order_by(Edge.edge_id)
        )
    )


# ---------------------------------------------------------------------------
# Reading them back
# ---------------------------------------------------------------------------


async def listing(
    sess: AsyncSession,
    *,
    about: int | None = None,
    limit: int = 50,
    offset: int = 0,
    withdrawn: bool = False,
) -> AnnotationsRead:
    """The reader's notes, most recently *written* first.

    ``about`` narrows to the notes attached to one node, by attachment, not by text.
    ``withdrawn`` lists the withdrawn ones instead of those that stand (`B-201`).
    """
    where = [
        Entity.is_annotation.is_(True),
        Entity.withdrawn_at.is_not(None) if withdrawn else Entity.withdrawn_at.is_(None),
    ]
    query = select(Entity)
    counted = select(func.count()).select_from(Entity)
    if about is not None:
        attached = select(Edge.from_node).where(
            Edge.to_node == about, Edge.relation_type == ANNOTATES
        )
        where.append(Entity.entity_id.in_(attached))

    total = await sess.scalar(counted.where(*where))
    rows = list(
        await sess.scalars(
            query.where(*where)
            .order_by(
                Entity.produced_at.desc().nullslast(),
                Entity.created_at.desc(),
                Entity.entity_id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return AnnotationsRead(annotations=await hydrate(sess, rows), total=int(total or 0))


async def hydrate(sess: AsyncSession, notes: Sequence[Entity]) -> list[AnnotationRead]:
    """Notes with their targets named rather than numbered.

    One query for every note's targets (`P6-04`).
    """
    if not notes:
        return []

    ids = [note.entity_id for note in notes]
    rows = (
        await sess.execute(
            select(Edge.from_node, Entity)
            .join(Entity, Entity.entity_id == Edge.to_node)
            .where(Edge.from_node.in_(ids), Edge.relation_type == ANNOTATES)
            .order_by(Edge.edge_id)
        )
    ).all()

    targets: dict[int, list[AnnotationTarget]] = {}
    for from_node, target in rows:
        targets.setdefault(from_node, []).append(AnnotationTarget.model_validate(target))

    return [
        AnnotationRead(
            entity_id=note.entity_id,
            title=note.canonical_name,
            body=note.description,
            about=targets.get(note.entity_id, []),
            supporting_chunk_ids=list(note.supporting_chunk_ids or ()),
            topic_labels=note.topic_labels,
            produced_by=note.produced_by or HUMAN,
            produced_at=note.produced_at,
            created_at=note.created_at,
            withdrawn_at=note.withdrawn_at,
        )
        for note in notes
    ]


def to_markdown(notes: Sequence[AnnotationRead], *, title: str = "Meridian notes") -> str:
    """Notes as Markdown (§12.5: "avoid trapping material in a bespoke store").

    Text and targets by name, with the chunk ids on a trailing line.
    """
    lines = [f"# {title}", ""]
    count = len(notes)
    lines += [f"{count} note{'' if count == 1 else 's'}", ""]

    for note in notes:
        lines.append(f"## {note.title}")
        lines.append("")
        if note.about:
            named = ", ".join(f"{t.canonical_name} ({t.node_type})" for t in note.about)
            lines += [f"About: {named}", ""]
        if note.body:
            lines += [note.body.strip(), ""]
        written = (note.produced_at or note.created_at).date().isoformat()
        trail = [f"written {written}"]
        if note.supporting_chunk_ids:
            trail.append("chunks " + ", ".join(str(c) for c in note.supporting_chunk_ids))
        lines += ["— " + " · ".join(trail), ""]

    return "\n".join(lines)
