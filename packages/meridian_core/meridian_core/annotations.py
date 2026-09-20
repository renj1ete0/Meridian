"""Annotation as first-class nodes (task P6-05, spec §12.5, §2 principle 3).

> "**Annotation as first-class nodes** — my own notes and edges, tagged as mine.
> Over months this becomes the highest-quality layer in the system and the one
> that actually reflects my thinking. Build the affordance early or it won't get
> used."

A note is an ``entities`` row with ``node_type='annotation'``, attached to what
it is about by ordinary ``annotates`` edges. Not a side table, because §12.1's
traversal, path mode and canvas filters all read `entities` and `edges` — a
notes table would need every one of them taught about it, and the layer §12.5
calls the highest-quality one in the system would be the only layer the graph
cannot see.

**Authorship is set here and nowhere else.** The layer is worth having because a
reader can tell their own thinking from the corpus's, so `produced_by` is
assigned by this module rather than accepted from a caller (§11.8: never trust
the structure in a request). A note that merely *says* a human wrote it, on a
surface where anything could say that, is not a distinguishable layer — and a
model with a write tool (`P4-04`) will eventually be calling into this service.

**A person is not on the agent registry's scale.** §11.12's `quality_tier` is an
ordinal over models, local small to hosted frontier. A note carrying one would
be ranked against model output on an axis it is not on, and "quality tier only
moves up" would become a rule about a person. It stays null, and so does
`model`.

**Where a citation lives, and why in two places.** The note's own
`supporting_chunk_ids` is the source of truth: a note with no target yet — a
thought that has not found its node — has no edges at all, and the passages the
reader was looking at would otherwise have nowhere to go. Each `annotates` edge
then carries the same list, because AGENTS.md's invariant is that *every* edge
names the chunks behind it and writing `{}` onto an edge while holding the ids
would be forfeiting that for tidiness. The two cannot drift: this module is the
only writer, and it rewrites the edges from the note on every change.
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
]

#: The reserved `produced_by`. Not an agent id and deliberately not shaped like
#: one — the registry's ids name a provider and a tier (`hosted-frontier`), and
#: this names the one author who has neither. `scripts/seed.py` refuses to
#: register an agent under it, because an agent that could would be able to
#: write rows indistinguishable from the reader's own thinking.
HUMAN = "human"

#: The relation a note attaches by. One relation rather than a vocabulary
#: ("disputes", "confirms"): the note's prose already says which, and a
#: controlled list would make the reader classify a thought before writing it.
ANNOTATES = "annotates"

#: `entities.node_type`, already in the typed ontology (§5.4).
ANNOTATION = "annotation"


async def create(sess: AsyncSession, body: AnnotationCreate) -> Entity:
    """Write a note. Raises before writing anything if it cannot be written whole.

    `LookupError` for a node that does not exist, `ValueError` for a chunk that
    does not. Both are checked first: a partly-applied write here is an
    annotation with a dangling edge, which is the shape that survives review
    because the note itself looks fine.
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

    Unset fields are left alone; ``about`` given is ``about`` *replaced*, because
    re-reading changes what a note is about and a note that accumulated every
    node it was ever pointed at would end up attached to the reader's whole
    search history.

    Raises `LookupError` for anything that is not the reader's own note —
    including a corpus-derived entity. §2.4 re-derives the graph from source
    chunks, so a hand-edit that survived into a derived node would be a change
    nothing can re-derive or explain.
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

    # The edges are rewritten whenever either half of what they carry moved —
    # the targets, or the citations copied onto them. Rebuilt wholesale rather
    # than diffed: an `annotates` edge holds nothing the note does not, so there
    # is no state on it worth preserving, and a diff is a second code path that
    # can disagree with this one about what the note now says.
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


async def _own_note(sess: AsyncSession, entity_id: int) -> Entity:
    """The note, or `LookupError` — including when the row exists and is not one.

    Not found rather than forbidden. "This is a corpus node, not yours" would
    answer a question the caller did not ask and tell an unidentified caller
    which entity ids exist.
    """
    note = await sess.get(Entity, entity_id)
    if note is None or not note.is_annotation:
        raise LookupError(f"No annotation {entity_id}.")
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

    The one thing this corpus exists to prevent (§2 principle 3), and worse on
    an annotation than anywhere else: the annotation layer is the part a reader
    trusts without re-checking, so a citation that goes nowhere here is one
    nobody will ever click to discover.
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
    sess: AsyncSession, *, about: int | None = None, limit: int = 50, offset: int = 0
) -> AnnotationsRead:
    """The reader's notes, most recently *written* first.

    Ordered by `produced_at` rather than `created_at`, so a note rewritten this
    morning comes back to the top — which is what "most recent" means for a
    notebook, and not what it means for a crawl.

    ``about`` narrows to the notes attached to one node. A note that mentions
    the node in its prose and is not attached to it does not match, and that is
    the right answer: the attachment is the claim, and matching on text would
    make the panel's contents depend on wording.
    """
    where = [Entity.is_annotation.is_(True)]
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

    `P6-04`'s rule applied here: a panel showing ``entity_id: 412`` asks the
    reader to resolve a foreign key by hand. One query for every note's targets
    rather than one per note, because the node panel and the notes list both
    render a page of these at a time.
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
        )
        for note in notes
    ]


def to_markdown(notes: Sequence[AnnotationRead], *, title: str = "Meridian notes") -> str:
    """Notes as Markdown (§12.5: "avoid trapping material in a bespoke store").

    The export a reader leaves with, so it carries what the note *means* — its
    text and what it is about, by name — rather than what the database needed to
    store it. Chunk ids are kept as a trailing line because they are the thread
    back into the corpus, and dropping them would make the export prettier and
    unfollowable.
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
