"""Retire stored passages no reader could read (task `B-235`).

The chunker now leaves such passages out (`worker.cleancut`); this finds the ones stored
before it did, by the same test, and supersedes them: out of search, embedding and every
reader surface, still there for any citation. Report by default; ``--apply`` writes. See
docs/features/extraction.md#unreadable-passages.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import time
from collections.abc import Sequence

from sqlalchemy import func, select, update

from meridian_core.chunks import cited_clause
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk

from .extract.readable import UNREADABLE_CLASS, is_unreadable

log = get_logger(__name__)

#: Candidate rows read per query, walked by chunk id.
BATCH = 2000

EXAMPLES = 12


@dataclasses.dataclass
class UnreadableStats:
    #: Live passages holding at least one unreadable character, which the test then judged.
    candidates: int = 0
    unreadable: int = 0
    sources: set[int] = dataclasses.field(default_factory=set)
    embedded: int = 0
    #: Cited by an edge or tag. Superseded all the same: the citation still resolves.
    cited: int = 0
    examples: list[tuple[int, int, str]] = dataclasses.field(default_factory=list)


async def run_pass(
    *,
    apply: bool,
    limit: int | None = None,
    source_ids: Sequence[int] | None = None,
    session_factory=session,
) -> UnreadableStats:
    """Judge every live passage holding an unreadable character; supersede the unreadable.

    ``source_ids`` narrows the pass to those sources.
    """
    stats = UnreadableStats()
    found: list[int] = []
    async with session_factory() as sess:
        after = 0
        while True:
            query = (
                select(Chunk.chunk_id, Chunk.source_id, Chunk.text, Chunk.embedding.is_not(None))
                .where(
                    Chunk.superseded_at.is_(None),
                    Chunk.chunk_id > after,
                    Chunk.text.op("~")(UNREADABLE_CLASS),
                )
                .order_by(Chunk.chunk_id)
                .limit(BATCH)
            )
            if source_ids is not None:
                query = query.where(Chunk.source_id.in_(list(source_ids)))
            rows = (await sess.execute(query)).all()
            if not rows:
                break
            after = rows[-1][0]
            for chunk_id, source_id, body, embedded in rows:
                stats.candidates += 1
                if not is_unreadable(body):
                    continue
                found.append(chunk_id)
                stats.sources.add(source_id)
                stats.embedded += bool(embedded)
                if len(stats.examples) < EXAMPLES:
                    stats.examples.append((chunk_id, source_id, body))
            if limit is not None and len(found) >= limit:
                found = found[:limit]
                break

        stats.unreadable = len(found)
        for start in range(0, len(found), BATCH):
            ids = found[start : start + BATCH]
            stats.cited += (
                await sess.scalar(
                    select(func.count())
                    .select_from(Chunk)
                    .where(Chunk.chunk_id.in_(ids), cited_clause())
                )
                or 0
            )
            if apply:
                await sess.execute(
                    update(Chunk)
                    .where(Chunk.chunk_id.in_(ids), Chunk.superseded_at.is_(None))
                    .values(superseded_at=func.now())
                )
        # Report only writes nothing, so leaving the session uncommitted is the whole of it.
        if apply:
            await sess.commit()

    log.info(
        "unreadable pass complete",
        extra={
            "candidates": stats.candidates,
            "unreadable": stats.unreadable,
            "sources": len(stats.sources),
            "applied": apply,
        },
    )
    return stats


def render(stats: UnreadableStats, apply: bool) -> None:
    print("=== Unreadable passages (B-235) ===")
    print(f"  live passages holding an unreadable character  {stats.candidates}")
    verb = "superseded" if apply else "would supersede"
    print(f"  {'unreadable, ' + verb:47}{stats.unreadable}")
    print(f"    in sources                                   {len(stats.sources)}")
    print(f"    embedded                                     {stats.embedded}")
    print(f"    cited (the citation still resolves)          {stats.cited}")
    for chunk_id, source_id, body in stats.examples:
        print(f"  chunk {chunk_id} (source {source_id}): {body[:90]!r}")
    if not apply:
        print("\n  Report only. --apply supersedes them (nothing is deleted).")


def main() -> None:
    """Entry point: ``python -m worker.unreadable``."""
    parser = argparse.ArgumentParser(description="Retire unreadable stored passages (B-235).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    parser.add_argument("--limit", type=int, help="stop after this many unreadable passages")
    parser.add_argument(
        "--source", type=int, action="append", help="only this source id (repeat for several)"
    )
    args = parser.parse_args()
    configure_logging("unreadable")

    async def go() -> UnreadableStats:
        try:
            return await run_pass(apply=args.apply, limit=args.limit, source_ids=args.source)
        finally:
            await dispose_engines()

    with bind_run_id(f"unreadable-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        render(asyncio.run(go()), args.apply)


if __name__ == "__main__":
    main()
