"""Re-embed chunks whose vectors predate the current embedding view (task `B-49`).

`worker.embed` embeds new chunks through `embedtext.embedding_view` and records
the view's version. Vectors computed before — or under an older view — are
stale only where the view actually changes the text, so this pass walks the
live chunks whose `embedding_view` is not current and, for each:

- the view equals the stored text (no links, no URLs): the vector is already
  what the current view would produce, so only the version is recorded;
- otherwise: the view is embedded and the vector replaced in place.

In place, never by clearing the vector first: a chunk keeps its old vector, and
stays searchable, until the new one lands. Resumable with no state beyond the
column: an interrupted pass leaves the unprocessed chunks exactly as stale as
they were. Report by default; ``--apply`` writes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.chunks import store_embeddings
from meridian_core.db import dispose_engines, session
from meridian_core.embedtext import VIEW_VERSION, embedding_view
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk

log = get_logger(__name__)

BATCH = 64


@dataclasses.dataclass
class ReembedStats:
    examined: int = 0
    unchanged: int = 0
    reembedded: int = 0
    batches: int = 0


def stale():
    """Live chunks with a vector computed under some other view."""
    return (
        Chunk.superseded_at.is_(None),
        Chunk.embedding.is_not(None),
        Chunk.embedding_view.is_distinct_from(VIEW_VERSION),
    )


async def _batch(sess: AsyncSession, after: int, size: int) -> list[tuple[int, str]]:
    rows = await sess.execute(
        select(Chunk.chunk_id, Chunk.text)
        .where(Chunk.chunk_id > after, *stale())
        .order_by(Chunk.chunk_id)
        .limit(size)
    )
    return [(cid, text) for cid, text in rows]


async def run_pass(
    embedder,
    *,
    apply: bool,
    batch_size: int = BATCH,
    start_after: int = 0,
    max_batches: int | None = None,
    session_factory=session,
) -> ReembedStats:
    stats = ReembedStats()
    after = start_after
    while max_batches is None or stats.batches < max_batches:
        async with session_factory() as sess:
            rows = await _batch(sess, after, batch_size)
        if not rows:
            break
        after = rows[-1][0]
        stats.batches += 1
        same = [cid for cid, text in rows if embedding_view(text) == text]
        changed = [
            (cid, embedding_view(text)) for cid, text in rows if embedding_view(text) != text
        ]
        stats.examined += len(rows)
        stats.unchanged += len(same)
        stats.reembedded += len(changed)
        if not apply:
            continue
        vectors = await embedder.embed([view for _, view in changed]) if changed else []
        async with session_factory() as sess:
            if same:
                await sess.execute(
                    update(Chunk)
                    .where(Chunk.chunk_id.in_(same))
                    .values(embedding_view=VIEW_VERSION)
                )
            if changed:
                await store_embeddings(
                    sess,
                    {cid: v for (cid, _), v in zip(changed, vectors, strict=True)},
                    view=VIEW_VERSION,
                )
            await sess.commit()
        log.info(
            "re-embed batch",
            extra={"after_id": after, "reembedded": len(changed), "unchanged": len(same)},
        )
    return stats


def main() -> None:
    """Entry point: ``python -m worker.reembed``."""
    parser = argparse.ArgumentParser(description="Re-embed chunks under the current view (B-49).")
    parser.add_argument("--apply", action="store_true", help="write; without it, count only")
    parser.add_argument("--max-batches", type=int)
    args = parser.parse_args()
    configure_logging("reembed")

    async def go() -> ReembedStats:
        from .vectors import build_embedder

        embedder = await build_embedder() if args.apply else None
        try:
            return await run_pass(embedder, apply=args.apply, max_batches=args.max_batches)
        finally:
            await dispose_engines()

    with bind_run_id(f"reembed-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    verb = "re-embedded" if args.apply else "would re-embed"
    print(f"examined {stats.examined}; {verb} {stats.reembedded}; unchanged {stats.unchanged}")


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
