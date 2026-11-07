"""Turning a name a model read into a node the graph holds (task `P4-16`, §5.5).

The middle band creates a second node and a notification rather than a merge; a model
may not mint an annotation; two stated, different jurisdictions never match. See
docs/features/knowledge-graph.md#resolution.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .annotations import ANNOTATION
from .embedder import MAX_TEXTS, EmbeddingUnavailable
from .logging import get_logger
from .models import Entity, Notification, ResolutionMatch
from .resolution import Verdict, block, score
from .validation import ValidationError

log = get_logger(__name__)

__all__ = [
    "ANNOTATION",
    "MatchSplit",
    "Resolved",
    "SplitRefused",
    "resolve_mention",
    "split_match",
]


@dataclasses.dataclass(frozen=True)
class Resolved:
    """Which node a mention became, and how that was decided."""

    entity: Entity
    created: bool
    band: str
    #: The best candidate's verdict, when there was one: the journal shows the score and
    #: its signals so the thresholds can be tuned.
    verdict: Verdict | None = None

    @property
    def summary(self) -> str:
        if self.verdict is None:
            return f"new {self.entity.node_type} (nothing to compare against)"
        detail = ", ".join(f"{k} {v:.2f}" for k, v in sorted(self.verdict.signals.items()))
        return f"{self.band} at {self.verdict.score:.2f} ({detail})"


def _compatible(candidate: Entity, jurisdiction: str | None) -> bool:
    """Whether two jurisdictions are allowed to be the same thing.

    One side unstated is not a disagreement — most passages never say — so only
    two stated and differing values separate.
    """
    if not jurisdiction or not candidate.jurisdiction:
        return True
    return candidate.jurisdiction.casefold() == jurisdiction.casefold()


async def _adjudication(sess: AsyncSession, *, mention: str, kept: Entity, other: Entity) -> None:
    """Queue the middle band for a person, naming both rows.

    Says which two nodes and why, so a reader can act on it.
    """
    sess.add(
        Notification(
            notification_type="merge_adjudication",
            title=f"Possible duplicate: {kept.canonical_name}",
            body=(
                f"A run read {mention!r} and could not tell whether it is "
                f"{other.canonical_name!r} (#{other.entity_id}). A separate node "
                f"(#{kept.entity_id}) was created, because a duplicate is "
                "recoverable and a bad merge is not."
            ),
            payload={"mention": mention, "created": kept.entity_id, "candidate": other.entity_id},
            surface="admin",
        )
    )
    await sess.flush()


async def resolve_mention(
    sess: AsyncSession,
    *,
    name: str,
    node_type: str,
    supporting_chunk_ids: Sequence[int],
    produced_by: str | None,
    model: str | None,
    quality_tier: int | None,
    jurisdiction: str | None = None,
    expansions: dict[str, str] | None = None,
    embedding: Sequence[float] | None = None,
    run_id: int | None = None,
    now: dt.datetime,
) -> Resolved:
    """The node this mention refers to, creating one if it refers to nothing yet.

    ``embedding`` is the mention name's vector (`B-40`); without it blocking and
    scoring use the name alone. A match to an existing node is recorded as a
    `ResolutionMatch` for the graph audit, naming ``run_id``. Flushes so the id
    exists; does not commit.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("mention_name", "A mention needs a name.")
    if node_type == ANNOTATION:
        raise ValidationError(
            "node_type",
            "An annotation is a note a person wrote (§12.5); a run may not create one.",
        )

    candidates = [
        candidate
        for candidate in await block(
            sess, cleaned, node_type, embedding=embedding, expansions=expansions
        )
        if _compatible(candidate.entity, jurisdiction)
    ]

    # Scored against a transient row, with no context: a mention seen once has no
    # neighbourhood to disagree with. See docs/features/knowledge-graph.md#resolution.
    vector = list(embedding) if embedding is not None else None
    probe = Entity(
        canonical_name=cleaned,
        node_type=node_type,
        jurisdiction=jurisdiction,
        supporting_chunk_ids=[],
        embedding=vector,
    )

    best: Verdict | None = None
    match: Entity | None = None
    for candidate in candidates:
        verdict = score(probe, candidate.entity, expansions=expansions)
        # Strictly greater, over candidates in id order: a tie goes to the
        # oldest node, the same one on every read (`B-37`). A resumed batch
        # depends on resolving each name exactly as it did the first time.
        if best is None or verdict.score > best.score:
            best, match = verdict, candidate.entity

    if best is not None and match is not None and best.merges:
        # Recorded before the node takes the mention in, so the audit shows the node as the
        # resolver saw it. See docs/features/knowledge-graph.md#the-graph-audit.
        sess.add(
            ResolutionMatch(
                at=now,
                entity_id=match.entity_id,
                mention=cleaned,
                node_type=node_type,
                jurisdiction=jurisdiction,
                supporting_chunk_ids=sorted(set(supporting_chunk_ids)),
                node_name=match.canonical_name,
                node_aliases=list(match.aliases or []),
                node_chunk_ids=list(match.supporting_chunk_ids or []),
                # A cosine can round a hair past 1; the column holds [0, 1].
                score=min(1.0, max(0.0, best.score)),
                signals=dict(best.signals),
                run_id=run_id,
                produced_by=produced_by,
                model=model,
                quality_tier=quality_tier,
            )
        )
        # The mention's chunks join the entity's, which is what makes context
        # overlap — §5.5's strongest signal — get better with every mention
        # rather than staying at whatever the first one happened to see.
        match.supporting_chunk_ids = sorted({*match.supporting_chunk_ids, *supporting_chunk_ids})
        if match.embedding is None and vector is not None:
            # A node made before entities were embedded gets its vector the
            # first time a mention of it is resolved with one.
            match.embedding = vector
        if cleaned.casefold() != match.canonical_name.casefold():
            aliases = list(match.aliases or [])
            if not any(alias.casefold() == cleaned.casefold() for alias in aliases):
                aliases.append(cleaned)
            match.aliases = aliases
        await sess.flush()
        log.info(
            "mention resolved to an existing node",
            extra={"entity": match.entity_id, "score": round(best.score, 3)},
        )
        return Resolved(entity=match, created=False, band=best.band, verdict=best)

    entity = Entity(
        canonical_name=cleaned,
        node_type=node_type,
        jurisdiction=jurisdiction,
        supporting_chunk_ids=sorted(set(supporting_chunk_ids)),
        embedding=vector,
        produced_by=produced_by,
        model=model,
        quality_tier=quality_tier,
        produced_at=now,
    )
    sess.add(entity)
    await sess.flush()

    band = best.band if best is not None else "separate"
    if band == "adjudicate" and match is not None:
        await _adjudication(sess, mention=cleaned, kept=entity, other=match)

    return Resolved(entity=entity, created=True, band=band, verdict=best)


class SplitRefused(RuntimeError):
    """A split that cannot be made, said in words a person can act on."""


@dataclasses.dataclass(frozen=True)
class MatchSplit:
    """What a split did: the node the mention became, and what left the node it had joined."""

    match_id: int
    entity_id: int
    split_entity_id: int
    alias_removed: bool
    chunks_moved: int


async def split_match(
    sess: AsyncSession, match_id: int, *, split_by: str, now: dt.datetime
) -> MatchSplit:
    """Split a matched mention back off the node it joined, into a node of its own (`B-274`).

    The new node takes the mention's name, type, jurisdiction, provenance and passages; the
    old node loses the alias and the passages this match added, unless another unsplit match
    on it brought the same. Relations and tags written from the mention stay where they are:
    they name no mention, so they cannot be told apart. A merge undoes a split. Refuses a
    match already split, or whose node is gone or merged away. Flushes; does not commit.
    See docs/features/knowledge-graph.md#the-graph-audit.
    """
    if not split_by:
        raise SplitRefused("A split needs to say who made it.")
    match = await sess.get(ResolutionMatch, match_id, with_for_update=True)
    if match is None:
        raise SplitRefused(f"No match {match_id}.")
    if match.split_entity_id is not None:
        raise SplitRefused(
            f"Already split off, as node #{match.split_entity_id}, by {match.split_by}."
        )
    node = await sess.get(Entity, match.entity_id, with_for_update=True)
    if node is None:
        raise SplitRefused(f"Node #{match.entity_id} no longer exists; there is nothing to split.")
    if node.redirects_to is not None:
        raise SplitRefused(
            f"Node #{node.entity_id} has since been merged into #{node.redirects_to}; "
            "undo that merge first, or split the name off there by hand."
        )

    # What other matches on this node, still standing, brought in: a split must not take
    # away what they rest on.
    others = list(
        await sess.scalars(
            select(ResolutionMatch).where(
                ResolutionMatch.entity_id == node.entity_id,
                ResolutionMatch.match_id != match.match_id,
                ResolutionMatch.split_at.is_(None),
            )
        )
    )
    kept_chunks = set(match.node_chunk_ids) | {
        c for other in others for c in other.supporting_chunk_ids
    }
    moved = set(match.supporting_chunk_ids) - kept_chunks
    node.supporting_chunk_ids = sorted(set(node.supporting_chunk_ids or ()) - moved)

    folded = match.mention.casefold()
    had_it = folded == match.node_name.casefold() or any(
        alias.casefold() == folded for alias in match.node_aliases
    )
    still_named = any(other.mention.casefold() == folded for other in others)
    alias_removed = False
    if not had_it and not still_named and node.aliases:
        remaining = [alias for alias in node.aliases if alias.casefold() != folded]
        alias_removed = len(remaining) != len(node.aliases)
        node.aliases = remaining

    entity = Entity(
        canonical_name=match.mention,
        node_type=match.node_type,
        jurisdiction=match.jurisdiction,
        supporting_chunk_ids=sorted(set(match.supporting_chunk_ids)),
        produced_by=match.produced_by,
        model=match.model,
        quality_tier=match.quality_tier,
        produced_at=now,
    )
    sess.add(entity)
    await sess.flush()

    match.split_at = now
    match.split_by = split_by
    match.split_entity_id = entity.entity_id
    await sess.flush()
    log.info(
        "matched mention split off",
        extra={
            "match_id": match.match_id,
            "entity": node.entity_id,
            "split_entity": entity.entity_id,
            "chunks_moved": len(moved),
        },
    )
    return MatchSplit(
        match_id=match.match_id,
        entity_id=node.entity_id,
        split_entity_id=entity.entity_id,
        alias_removed=alias_removed,
        chunks_moved=len(moved),
    )


#: Entities embedded per backfill call. Small, because it runs inside a
#: synthesis stage and names are short; a corpus with thousands of unembedded
#: entities catches up over a few runs rather than stalling one.
BACKFILL_BATCH = 256


async def embed_names(embedder, names: Sequence[str]) -> dict[str, list[float]]:
    """One vector per distinct name, in as few requests as the cap allows.

    Returns an empty mapping when there is no embedder or it cannot answer:
    resolution then runs on names alone, as it always did — degraded, not
    stopped, the same position search takes (§11.3).
    """
    if embedder is None:
        return {}
    distinct = sorted({name.strip() for name in names if name and name.strip()})
    if not distinct:
        return {}
    vectors: dict[str, list[float]] = {}
    try:
        for start in range(0, len(distinct), MAX_TEXTS):
            chunk = distinct[start : start + MAX_TEXTS]
            for name, vector in zip(chunk, await embedder.embed(chunk), strict=True):
                vectors[name] = vector
    except EmbeddingUnavailable as exc:
        log.warning(
            "mention names not embedded; resolving by name alone", extra={"detail": str(exc)}
        )
        return {}
    return vectors


async def embed_missing_entities(
    sess: AsyncSession, embedder, *, limit: int = BACKFILL_BATCH
) -> int:
    """Give entities with no vector one, from their canonical name. Returns how many.

    Entities made before `B-40` have none, so the embedding half of blocking
    and scoring cannot see them; this closes that gap a batch at a time.
    """
    if embedder is None:
        return 0
    rows = list(
        await sess.scalars(
            select(Entity)
            .where(Entity.embedding.is_(None), Entity.redirects_to.is_(None))
            .order_by(Entity.entity_id)
            .limit(limit)
        )
    )
    vectors = await embed_names(embedder, [row.canonical_name for row in rows])
    done = 0
    for row in rows:
        vector = vectors.get(row.canonical_name.strip())
        if vector is not None:
            row.embedding = vector
            done += 1
    await sess.flush()
    return done
