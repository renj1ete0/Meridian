"""Mark sources that are copies of an earlier source (task `B-44`).

``python -m worker.docdupes`` compares every live, non-junk source's passages,
title, mean vector and length (`meridian_core.docdupes` has the rules and why
they are strict) and reports the copies it finds; ``--apply`` points each copy
at its canonical source and clears marks that no longer hold. Search, the map
and synthesis leave a marked source out. Nothing is deleted, and a source
anything cites is never marked.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time

from sqlalchemy import select, update

from meridian_core.chunks import cited_source_ids
from meridian_core.db import dispose_engines, session
from meridian_core.docdupes import Pair, canonical, exact_pairs, near_pairs
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Source
from meridian_core.topiclabels import source_vectors

log = get_logger(__name__)

EXAMPLES = 25


@dataclasses.dataclass
class DupeStats:
    examined: int = 0
    exact: int = 0
    near: int = 0
    cleared: int = 0
    protected: int = 0
    examples: list[tuple[str, str, str, float]] = dataclasses.field(default_factory=list)


async def run_pass(*, apply: bool, session_factory=session) -> DupeStats:
    stats = DupeStats()
    async with session_factory() as sess:
        sources = (
            await sess.execute(
                select(Source.source_id, Source.url, Source.title, Source.duplicate_of).where(
                    Source.retention_tier != "junk"
                )
            )
        ).all()
        ids = [s.source_id for s in sources]
        passages: dict[int, list[str]] = collections.defaultdict(list)
        lengths: collections.Counter[int] = collections.Counter()
        rows = await sess.execute(
            select(Chunk.source_id, Chunk.text).where(
                Chunk.superseded_at.is_(None), Chunk.source_id.in_(ids)
            )
        )
        for sid, text in rows:
            passages[sid].append(text)
            lengths[sid] += len(text)
        vectors = {}
        for start in range(0, len(ids), 500):
            vectors.update(await source_vectors(sess, ids[start : start + 500]))
        protected = await cited_source_ids(sess)

        stats.examined = len(passages)
        pairs: list[Pair] = exact_pairs(passages) + near_pairs(
            {s.source_id: s.title for s in sources}, vectors, lengths
        )
        stats.protected = len({p.later for p in pairs} & protected)
        verdicts = canonical(pairs, protected=protected)
        stats.exact = sum(1 for p in verdicts.values() if p.reason == "exact")
        stats.near = sum(1 for p in verdicts.values() if p.reason == "near")
        urls = {s.source_id: s.url for s in sources}
        for pair in list(verdicts.values())[:EXAMPLES]:
            stats.examples.append((urls[pair.later], urls[pair.earlier], pair.reason, pair.score))

        stale = [
            s.source_id
            for s in sources
            if s.duplicate_of is not None and s.source_id not in verdicts
        ]
        stats.cleared = len(stale)
        if apply:
            if stale:
                await sess.execute(
                    update(Source)
                    .where(Source.source_id.in_(stale))
                    .values(duplicate_of=None, duplicate_reason=None)
                )
            for pair in verdicts.values():
                await sess.execute(
                    update(Source)
                    .where(Source.source_id == pair.later)
                    .values(duplicate_of=pair.earlier, duplicate_reason=pair.reason)
                )
            await sess.commit()
    log.info(
        "document duplicates judged",
        extra={
            "exact": stats.exact,
            "near": stats.near,
            "cleared": stats.cleared,
            "applied": apply,
        },
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark sources that copy an earlier one (B-44).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("docdupes")

    async def go() -> DupeStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"docdupes-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    print(
        f"examined {stats.examined}; copies: exact {stats.exact}, near {stats.near}; "
        f"cited and left alone {stats.protected}; marks cleared {stats.cleared}"
    )
    for later, earlier, reason, score in stats.examples:
        print(f"  {reason:5} {score:<6} {later[:80]}\n        of {earlier[:80]}")
    if not args.apply:
        print("\nReport only. --apply marks them (nothing is deleted).")


if __name__ == "__main__":
    main()
