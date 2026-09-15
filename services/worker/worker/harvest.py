"""Auto-harvesting acronym definitions (task P5-02, spec §5.6).

``python -m worker.harvest`` — one pass over documents nobody has read yet.

§5.6 is blunt about where the gazetteer comes from: **"do not hand-write it —
bootstrap it."** Fifty terms are seeded by a person, and then the table grows
from the observation that government and academic documents define their
acronyms on first use. ``Full Name Here (ACRONYM)`` is one regex over text that
has already been extracted, and §5.6 expects it to populate most of the list.

**Nothing it finds is approved.** Terms land ``approved=false`` and stay there
until a person confirms them or the same expansion turns up in enough separate
documents. That is not caution for its own sake: an approved row loads into the
``EntityRuler``, and the ruler *overrides* statistical NER — so a regex mistake
promoted straight to approved does not merely add a wrong entity, it takes the
model's say away on every document that mentions it.

**Corroboration is counted in documents, not occurrences.** A report that
defines a term in its glossary and again in each of forty sections has said one
thing forty times, and counting hits would auto-approve on the strength of a
single author's typo.

**Two expansions for one acronym is the finding, not the failure.** It gets both
rows flagged ambiguous, which keeps both out of the ruler and hands the mention
to §5.5's resolver, which can see the rest of the document. The alternative —
keeping whichever was inserted first — decides between two readings by row order
and leaves no trace that there was a choice.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import time
from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.gazetteer import find_acronyms
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, GazetteerTerm, Source

log = get_logger(__name__)

#: Documents per transaction. Small, because a document's chunks are read in
#: full and a batch is held in memory; large enough that the commit is not the
#: dominant cost.
BATCH = 50

#: How many separate documents must define a term the same way before it is
#: approved without anyone looking. §5.6 allows "auto-approve above a frequency
#: threshold"; three independent documents agreeing on an expansion is a much
#: stronger claim than thirty mentions in one.
APPROVAL_THRESHOLD = 3

#: What a harvested term is, in §5.6's five types. A regex cannot tell an agency
#: from a scheme from a metric, and `concept` is the one that claims least — a
#: term filed here and later corrected costs a curator one dropdown, where a
#: term wrongly filed as `agency` reads as a fact somebody established.
HARVEST_ENTITY_TYPE = "concept"

HARVEST_SOURCE = "auto_acronym"


@dataclasses.dataclass
class HarvestStats:
    documents: int = 0
    definitions: int = 0
    created: int = 0
    corroborated: int = 0
    approved: int = 0
    ambiguous: int = 0

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


async def sources_awaiting_harvest(sess: AsyncSession, limit: int) -> list[int]:
    """The queue: documents with text that nobody has read for acronyms.

    ``acronyms_harvested_at IS NULL`` is the whole predicate, the same shape the
    novelty gate uses — so a pass killed in hour three keeps everything it
    committed and the next one starts where it stopped, with no cursor to store
    and nothing to reconcile if two passes overlap.
    """
    rows = await sess.scalars(
        select(Source.source_id)
        .where(Source.acronyms_harvested_at.is_(None), Source.text_available.is_(True))
        .order_by(Source.source_id)
        .limit(limit)
    )
    return list(rows)


async def document_text(sess: AsyncSession, source_id: int) -> str:
    """One document's chunks, rejoined in order.

    Rejoined rather than scanned chunk by chunk because a definition split across
    a chunk boundary is invisible to both halves — "…the Land Transport" ends one
    chunk and "Authority (LTA) said…" begins the next, and each alone looks like
    text with no definition in it. Chunking has no overlap (`P2-02`), so the join
    reconstructs the document rather than duplicating it.
    """
    rows = await sess.scalars(
        select(Chunk.text).where(Chunk.source_id == source_id).order_by(Chunk.chunk_index)
    )
    return "\n".join(rows)


def _canonical_match(rows: list[GazetteerTerm], expansion: str) -> GazetteerTerm | None:
    """An existing row for this expansion, compared case-insensitively.

    Case-insensitively because a document that writes a term in a heading and the
    curator who typed it in sentence case mean the same term, and a second row
    that differs only in capitalisation is the fragmentation §5.5 exists to
    prevent — arriving through the table that is supposed to fix it.
    """
    wanted = expansion.casefold()
    for row in rows:
        if row.canonical.casefold() == wanted:
            return row
    return None


async def record(
    sess: AsyncSession, acronym: str, expansion: str, documents: int, stats: HarvestStats
) -> GazetteerTerm:
    """File one definition. Flushes; does not commit."""
    existing = list(
        await sess.scalars(
            select(GazetteerTerm).where(func.lower(GazetteerTerm.canonical) == expansion.lower())
        )
    )
    row = _canonical_match(existing, expansion)

    if row is None:
        row = GazetteerTerm(
            canonical=expansion,
            aliases=[acronym],
            entity_type=HARVEST_ENTITY_TYPE,
            jurisdiction=None,
            source=HARVEST_SOURCE,
            approved=False,
            occurrence_count=documents,
        )
        sess.add(row)
        stats.created += 1
    else:
        row.occurrence_count = (row.occurrence_count or 0) + documents
        stats.corroborated += 1
        # A regex may not edit curated data. An approved row is already loaded
        # into the ruler, so an alias appended here would take effect with no
        # one having agreed to it — which is the approval queue being bypassed by
        # the one mechanism it exists to hold back. The count still rises, and
        # that is the corroboration signal either way.
        if not row.approved and acronym not in (row.aliases or []):
            row.aliases = [*(row.aliases or []), acronym]

    if (
        not row.approved
        and row.source == HARVEST_SOURCE
        and row.occurrence_count >= APPROVAL_THRESHOLD
    ):
        row.approved = True
        stats.approved += 1

    await sess.flush()
    return row


async def flag_ambiguous(sess: AsyncSession, acronym: str, stats: HarvestStats) -> None:
    """Two expansions for one acronym: flag every row that claims it.

    Flagged, never resolved. The column's own comment is the rule — where context
    is insufficient a mention must be left unresolved rather than guessed,
    because a wrong resolution corrupts the graph invisibly and an unresolved
    mention stays visible and fixable. This pass has no context: it has read two
    documents that disagree, which is exactly the evidence that the surface form
    cannot be decided from the surface form.
    """
    rows = list(
        await sess.scalars(
            select(GazetteerTerm).where(GazetteerTerm.aliases.any(acronym))  # type: ignore[attr-defined]
        )
    )
    if len({row.canonical.casefold() for row in rows}) < 2:
        return
    for row in rows:
        if not row.ambiguous:
            row.ambiguous = True
            stats.ambiguous += 1
    await sess.flush()


async def harvest_batch(sess: AsyncSession, source_ids: list[int], stats: HarvestStats) -> None:
    """Read a batch of documents and file everything they define."""
    # acronym -> expansion -> the documents that defined it that way. Built for
    # the whole batch before anything is written, so a term three documents in
    # this batch agree on is corroborated in one step rather than three.
    seen: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))

    for source_id in source_ids:
        text = await document_text(sess, source_id)
        for definition in find_acronyms(text):
            seen[definition.acronym][definition.expansion].add(source_id)
        stats.documents += 1

    for acronym, expansions in seen.items():
        for expansion, documents in expansions.items():
            await record(sess, acronym, expansion, len(documents), stats)
            stats.definitions += 1
        await flag_ambiguous(sess, acronym, stats)

    await sess.execute(
        update(Source)
        .where(Source.source_id.in_(source_ids))
        .values(acronyms_harvested_at=dt.datetime.now(dt.UTC))
    )


async def run_pass(*, max_documents: int | None = None) -> HarvestStats:
    """Harvest until the queue is empty or the cap is reached."""
    stats = HarvestStats()
    async with session("rw") as sess:
        while max_documents is None or stats.documents < max_documents:
            left = BATCH if max_documents is None else min(BATCH, max_documents - stats.documents)
            source_ids = await sources_awaiting_harvest(sess, left)
            if not source_ids:
                break
            await harvest_batch(sess, source_ids, stats)
            await sess.commit()
            log.info("harvest batch", extra=stats.as_dict())

    log.info("harvest complete", extra=stats.as_dict())
    return stats


def render(stats: HarvestStats) -> None:
    print("=== Acronym harvest (§5.6) ===")
    print(f"  documents read     {stats.documents}")
    print(f"  definitions found  {stats.definitions}")
    print(f"  new terms          {stats.created}")
    print(f"  corroborated       {stats.corroborated}")
    print(f"  auto-approved      {stats.approved}  (>= {APPROVAL_THRESHOLD} documents agreeing)")
    print(f"  flagged ambiguous  {stats.ambiguous}")
    if stats.created or stats.corroborated:
        print("\n  New terms wait for approval. Approved ones override statistical NER.")


def main() -> None:
    """Entry point: ``python -m worker.harvest``."""
    parser = argparse.ArgumentParser(
        description="Harvest `Full Name (ACRONYM)` definitions into the gazetteer (§5.6).",
    )
    parser.add_argument(
        "--max-documents",
        type=int,
        default=None,
        help="stop after this many documents. Without it the pass drains the queue.",
    )
    args = parser.parse_args()

    configure_logging("harvest")
    with bind_run_id(f"harvest-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(_run(max_documents=args.max_documents))
    render(stats)


async def _run(*, max_documents: int | None) -> HarvestStats:
    stats = await run_pass(max_documents=max_documents)
    await dispose_engines()
    return stats


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
