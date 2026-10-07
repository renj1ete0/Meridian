"""Read the language of stored sources that never declared one (task `B-153`).

``python -m worker.relanguage`` — reports by default, writes only with ``--apply``. The fetch
loop detects an undeclared language as it stores a page now; this does the same for sources
stored before it, from their first passages. A source found to be in another language, and its
passages, are marked for relabelling, so the next `topics` pass scores them as one (`B-53`). See
docs/features/extraction.md#language.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, ChunkTopics, Source
from meridian_core.topiclabels import is_english

from .extract.language import SAMPLE_CHARS, detect_language

log = get_logger(__name__)

#: Sources per transaction.
BATCH = 500

#: Passages read per source: enough text for a confident answer.
LEAD_PASSAGES = 3

#: Written over a passage label's basis to mark it stale: never a real fingerprint.
STALE_BASIS = "stale:language"


@dataclasses.dataclass
class RelanguageStats:
    examined: int = 0
    detected: int = 0
    unsure: int = 0
    relabel: int = 0
    by_language: collections.Counter = dataclasses.field(default_factory=collections.Counter)


async def _lead_text(sess: AsyncSession, source_ids: list[int]) -> dict[int, str]:
    rows = await sess.execute(
        select(Chunk.source_id, Chunk.text)
        .where(
            Chunk.source_id.in_(source_ids),
            Chunk.superseded_at.is_(None),
            Chunk.chunk_index < LEAD_PASSAGES,
        )
        .order_by(Chunk.source_id, Chunk.chunk_index)
    )
    out: dict[int, list[str]] = collections.defaultdict(list)
    for source_id, text in rows:
        out[source_id].append(text)
    return {k: "\n".join(v)[:SAMPLE_CHARS] for k, v in out.items()}


async def _stale_passages(sess: AsyncSession, source_ids: list[int]) -> None:
    """Mark these sources' passage labels as from another basis; the rows are kept."""
    await sess.execute(
        update(ChunkTopics)
        .where(
            ChunkTopics.chunk_id.in_(select(Chunk.chunk_id).where(Chunk.source_id.in_(source_ids)))
        )
        .values(topic_basis=STALE_BASIS)
        .execution_options(synchronize_session=False)
    )


async def run_pass(
    *, apply: bool, session_factory=session, start_after: int = 0
) -> RelanguageStats:
    """Walk sources with no language in id order. Commits per batch when applying."""
    stats = RelanguageStats()
    after = start_after
    while True:
        async with session_factory() as sess:
            rows = list(
                await sess.scalars(
                    select(Source)
                    .where(Source.source_id > after, Source.language.is_(None))
                    .order_by(Source.source_id)
                    .limit(BATCH)
                )
            )
            if not rows:
                break
            after = rows[-1].source_id
            leads = await _lead_text(sess, [r.source_id for r in rows])
            relabelled: list[int] = []
            for source in rows:
                stats.examined += 1
                language = detect_language(leads.get(source.source_id))
                if language is None:
                    stats.unsure += 1
                    continue
                stats.detected += 1
                stats.by_language[language] += 1
                # Unknown was scored as English; only another language changes a score.
                relabel = not is_english(language) and source.topic_basis is not None
                stats.relabel += relabel
                if apply:
                    source.language = language
                    source.extra = {**(source.extra or {}), "language_from": "text"}
                    if relabel:
                        source.topic_basis = None
                        relabelled.append(source.source_id)
            if apply:
                if relabelled:
                    await _stale_passages(sess, relabelled)
                await sess.commit()
        log.info("relanguage batch", extra={"after_id": after, "examined": stats.examined})
    return stats


def main() -> None:
    """Entry point: ``python -m worker.relanguage``."""
    parser = argparse.ArgumentParser(
        description="Read the language of stored sources that never declared one (B-153)."
    )
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("relanguage")

    async def go() -> RelanguageStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"relanguage-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    done = "" if args.apply else "would be "
    print(
        f"examined {stats.examined}  detected {stats.detected}  unsure {stats.unsure}  "
        f"{stats.relabel} in another language {done}sent back for topic labels"
    )
    for language, n in stats.by_language.most_common(15):
        print(f"    {n:6}  {language}")
    if not args.apply:
        print("\nReport only. --apply writes; each detected language is marked language_from=text.")


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
