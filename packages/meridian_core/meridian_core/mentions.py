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
from .models import Entity, Notification
from .resolution import Verdict, block, score
from .validation import ValidationError

log = get_logger(__name__)

__all__ = [
    "ANNOTATION",
    "Resolved",
    "resolve_mention",
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
    now: dt.datetime,
) -> Resolved:
    """The node this mention refers to, creating one if it refers to nothing yet.

    ``embedding`` is the mention name's vector (`B-40`); without it blocking and
    scoring use the name alone. Flushes so the id exists; does not commit.
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
