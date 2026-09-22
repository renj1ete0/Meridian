"""Turning a name a model read into a node the graph holds (task `P4-16`, §5.5).

`resolution.py` decides and deliberately does not act: it normalises, blocks,
scores and bands, and `merge` is kept separate so a change to a threshold is
not a change to a function that rewrites rows. That leaves a gap exactly one
caller wide — the extraction stage, which has a name and a type and needs an
`entity_id` before `add_edge` will look at it. This module is that caller, and
it is here rather than inside `resolution.py` so that module's own boundary
stays true.

**A duplicate is preferred to a merge nobody asked for.** §5.5 bands the
middle range "queue for model adjudication", and the safe disposition of that
band is a *second node* plus a notification, not a merge on the balance of
probability. A duplicate is visible — two similar names, and somebody notices —
while a conflation leaves one plausible-looking node and no evidence it was
ever two. The adjudication notification is written here rather than left to
the caller, because a caller that forgot it would drop the middle band in
silence, which is the failure this whole band exists to prevent.

**A model may not mint an annotation.** `annotation` is the one node type that
means "a person wrote this" (`P6-05`, §12.5), and nothing downstream
distinguishes a forged one. Refused here as well as omitted from the prompt:
the prompt is what the model reads, this is what the database gets.

**Jurisdiction separates before scoring does.** §5.5's uniqueness key includes
it because one name routinely denotes unrelated things in two countries, and
those are cases where string and embedding similarity both say "the same" with
complete confidence. Two *stated* and different jurisdictions is a fact about
identity, not a signal to be weighed against others.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from .annotations import ANNOTATION
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
    #: The best candidate's verdict, when there was a candidate at all. Kept so
    #: the journal can say "merged at 0.94, string 0.99 context 0.88" rather
    #: than "merged" — a number nobody can argue with is a threshold nobody can
    #: tune.
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

    §5.5 wants the middle band small, cheap and the only place a model adds
    value. Until something adjudicates it, the row that matters is the one a
    reader can act on — so this says which two nodes and why, not that a
    decision is outstanding.
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
    now: dt.datetime,
) -> Resolved:
    """The node this mention refers to, creating one if it refers to nothing yet.

    Flushes so the id exists; does not commit. The caller's transaction owns
    whether any of this survives, which is what lets `--dry-run` cover a stage
    that never heard of it.
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
        for candidate in await block(sess, cleaned, node_type, expansions=expansions)
        if _compatible(candidate.entity, jurisdiction)
    ]

    # Scored against a transient row rather than a second code path: `score`
    # takes two entities and reads four fields off each, so building the one we
    # are about to write anyway keeps the signals identical to the ones a later
    # comparison of two stored entities would produce.
    #
    # **With no context, deliberately.** A mention has been seen exactly once,
    # so "shares no neighbours with that entity" is an absence, not a
    # disagreement — and §5.5's rule for an absent signal is to drop it and
    # renormalise, never to count it as zero. Passing the batch's chunks here
    # instead scores an exact name match at 0.43 the moment the second passage
    # mentioning it is a different chunk, which is below the separation
    # threshold: every mention would then found a new node until two of them
    # happened to share a chunk, which is the fragmentation this whole module
    # exists to prevent. The chunks are still recorded on the row below; they
    # are what gives the *entity* a neighbourhood for next time.
    probe = Entity(
        canonical_name=cleaned,
        node_type=node_type,
        jurisdiction=jurisdiction,
        supporting_chunk_ids=[],
    )

    best: Verdict | None = None
    match: Entity | None = None
    for candidate in candidates:
        verdict = score(probe, candidate.entity, expansions=expansions)
        if best is None or verdict.score > best.score:
            best, match = verdict, candidate.entity

    if best is not None and match is not None and best.merges:
        # The mention's chunks join the entity's, which is what makes context
        # overlap — §5.5's strongest signal — get better with every mention
        # rather than staying at whatever the first one happened to see.
        match.supporting_chunk_ids = sorted({*match.supporting_chunk_ids, *supporting_chunk_ids})
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
