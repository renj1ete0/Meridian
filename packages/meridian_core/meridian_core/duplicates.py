"""Deciding a possible duplicate (task `B-202`, spec §5.5).

Resolution queues the middle band for a person as a `merge_adjudication` notification: a run
read a name it could not place, created a separate node, and named the existing one it might
be. This module lists those still undecided, with both nodes and their evidence, and records
a decision: merge through `resolution.merge` (reversible), or keep apart. The decision is
written onto the notification, which is what the bell reads as settled.
See docs/features/knowledge-graph.md#deciding-a-possible-duplicate.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, Edge, Entity, Notification, Source
from .resolution import MergeError, merge, reverse
from .schemas.duplicates import (
    DuplicateDecisionRead,
    DuplicatePairRead,
    DuplicatePassageRead,
    DuplicateSideRead,
    DuplicatesRead,
)

#: Passages shown per side: enough to see what each node is about, not a reading list.
PASSAGES_PER_SIDE = 2

#: A passage is cut here on the page; the source page holds the rest.
PASSAGE_CHARS = 360

KIND = "merge_adjudication"


class DuplicateRefused(RuntimeError):
    """A decision that cannot be made: an unknown or settled pair, or a merge refused."""


def _open() -> list:
    # A decided pair carries its decision; an undecided one has none.
    return [Notification.notification_type == KIND, Notification.payload["decision"].is_(None)]


async def _side(sess: AsyncSession, entity: Entity) -> DuplicateSideRead:
    links = await sess.scalar(
        select(func.count())
        .select_from(Edge)
        .where(or_(Edge.from_node == entity.entity_id, Edge.to_node == entity.entity_id))
    )
    ids = list(entity.supporting_chunk_ids or ())[:PASSAGES_PER_SIDE]
    rows = (
        (
            await sess.execute(
                select(Chunk.chunk_id, Chunk.source_id, Chunk.text, Source.title)
                .join(Source, Source.source_id == Chunk.source_id)
                .where(Chunk.chunk_id.in_(ids))
            )
        ).all()
        if ids
        else []
    )
    return DuplicateSideRead(
        entity_id=entity.entity_id,
        canonical_name=entity.canonical_name,
        node_type=entity.node_type,
        jurisdiction=entity.jurisdiction,
        aliases=sorted(entity.aliases or ()),
        description=entity.description,
        links=int(links or 0),
        passages=[
            DuplicatePassageRead(
                chunk_id=chunk_id, source_id=source_id, title=title, text=text[:PASSAGE_CHARS]
            )
            for chunk_id, source_id, text, title in rows
        ],
    )


async def open_pairs(sess: AsyncSession, *, limit: int = 20) -> DuplicatesRead:
    """Undecided pairs, newest first, each with both nodes as they stand now.

    A pair either side of which is gone or already redirects is left out of the page: it was
    settled some other way, and showing it would offer a merge `resolution.merge` refuses.
    """
    total = int(
        await sess.scalar(select(func.count()).select_from(Notification).where(*_open())) or 0
    )
    notes = list(
        await sess.scalars(
            select(Notification)
            .where(*_open())
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    )
    pairs = []
    for note in notes:
        payload = note.payload or {}
        created = await sess.get(Entity, payload.get("created"))
        candidate = await sess.get(Entity, payload.get("candidate"))
        if created is None or candidate is None or created.redirects_to or candidate.redirects_to:
            continue
        pairs.append(
            DuplicatePairRead(
                notification_id=note.notification_id,
                mention=str(payload.get("mention") or created.canonical_name),
                created=await _side(sess, created),
                candidate=await _side(sess, candidate),
                created_at=note.created_at.isoformat(),
            )
        )
    return DuplicatesRead(pairs=pairs, total=total)


async def _pair(sess: AsyncSession, notification_id: int) -> Notification:
    note = await sess.get(Notification, notification_id, with_for_update=True)
    if note is None or note.notification_type != KIND:
        raise DuplicateRefused(f"No possible duplicate {notification_id}.")
    return note


async def decide(
    sess: AsyncSession, notification_id: int, decision: str, *, decided_by: str
) -> DuplicateDecisionRead:
    """Merge the created node into the candidate, or keep them apart; recorded on the pair.

    Merging goes through `resolution.merge`, which keeps the created node as a redirect and
    logs what it moved, so it can be undone with :func:`undo`.
    """
    note = await _pair(sess, notification_id)
    payload = dict(note.payload or {})
    if payload.get("decision"):
        raise DuplicateRefused(f"Already decided: {payload['decision']}.")

    merge_id = None
    if decision == "merge":
        try:
            entry = await merge(
                sess, int(payload["created"]), int(payload["candidate"]), decided_by=decided_by
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DuplicateRefused("This pair does not name two nodes.") from exc
        except MergeError as exc:
            raise DuplicateRefused(str(exc)) from exc
        merge_id = entry.merge_id
        payload.update(decision="merged", merge_id=merge_id)
    elif decision == "keep":
        payload.update(decision="kept apart")
    else:
        raise DuplicateRefused(f"{decision!r} is not a decision; merge or keep.")

    payload["decided_by"] = decided_by
    # Reassigned, not mutated: a JSON column only notices a new value.
    note.payload = payload
    note.read_at = dt.datetime.now(dt.UTC)
    await sess.flush()
    return DuplicateDecisionRead(
        notification_id=notification_id, decision=payload["decision"], merge_id=merge_id
    )


async def undo(
    sess: AsyncSession, notification_id: int, *, decided_by: str
) -> DuplicateDecisionRead:
    """Reverse a decision: split a merge exactly, or reopen a pair kept apart."""
    note = await _pair(sess, notification_id)
    payload = dict(note.payload or {})
    if not payload.get("decision"):
        raise DuplicateRefused("Nothing has been decided about this pair.")
    if payload.get("merge_id") is not None:
        try:
            await reverse(sess, int(payload["merge_id"]), reversed_by=decided_by)
        except MergeError as exc:
            raise DuplicateRefused(str(exc)) from exc
    for key in ("decision", "merge_id", "decided_by"):
        payload.pop(key, None)
    note.payload = payload
    note.read_at = None
    await sess.flush()
    return DuplicateDecisionRead(notification_id=notification_id, decision="reopened")
