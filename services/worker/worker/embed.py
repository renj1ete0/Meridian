"""The embedding backfill (task P2-01, spec §6.1, §4).

§6.1 draws embedding inside the fast loop — `chunk → embed → store` — and this
runs it as a separate pass instead. The reason is the model: bge-m3 is 2.3GB of
weights that the fetch path never touches, and putting it in the crawler means
every lane carries it, the worker image quadruples, and a slow encode stalls a
fetch that had nothing to do with it. The schema was already built for the
split, since `chunks.embedding` is nullable and `P2-02` writes chunks without
vectors by design.

What that costs is a window where a chunk exists and is not yet searchable, and
the honest answer is that it does not matter: nothing searches yet, and once
something does, a backlog is a number on the health line rather than a silent
gap.

**Resumable by construction.** The queue is `embedding IS NULL`, so a pass that
dies halfway leaves the rest of its work exactly where it was and the next pass
picks it up. There is no cursor to corrupt and no state outside the table.

**One batch, one transaction.** A crash between encoding and committing loses
that batch's work and nothing else. Committing per batch rather than per pass is
what makes a four-hour backfill survivable — an interrupted run keeps everything
up to its last batch.

Runs on demand (`python -m worker.embed`) or as a loop that watches the backlog.
Deliberately not wired into `worker.main`'s housekeeping: the crawler must keep
working on a machine with no model installed at all, which is the same
fast-loop invariant (§2.1) that keeps ingestion independent of the reasoning
plane.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import os
import signal
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.chunks import (
    chunks_without_embeddings,
    embedding_backlog,
    store_embeddings,
)
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger

from .embeddings import BGEEmbedder, Embedder, EmbedderSettings, EmbeddingError

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: How many chunks are read and written per transaction. Larger than the model
#: batch on purpose: the round-trip to Postgres is the cheap part, and one
#: commit per 256 chunks is far less write amplification than one per 8.
DEFAULT_CHUNK_BATCH = 256

#: How long the loop waits when the backlog is empty.
DEFAULT_IDLE_SLEEP_S = 60.0


@dataclasses.dataclass
class EmbedStats:
    """What one pass did."""

    embedded: int = 0
    batches: int = 0
    failed_batches: int = 0
    seconds: float = 0.0

    @property
    def per_second(self) -> float:
        return self.embedded / self.seconds if self.seconds else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "embedded": self.embedded,
            "batches": self.batches,
            "failed_batches": self.failed_batches,
            "seconds": round(self.seconds, 1),
            "chunks_per_second": round(self.per_second, 2),
        }


class Backfill:
    """Gives vectors to every chunk that has none."""

    def __init__(
        self,
        embedder: Embedder,
        *,
        session_factory: SessionFactory = session,
        batch_size: int = DEFAULT_CHUNK_BATCH,
        idle_sleep_s: float = DEFAULT_IDLE_SLEEP_S,
        max_batches: int | None = None,
        start_after: int = 0,
    ) -> None:
        self._embedder = embedder
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._idle_sleep_s = idle_sleep_s
        self._max_batches = max_batches
        # Where the queue scan begins. 0 means the whole table, which is what a
        # backfill wants; an operator embedding only what a recent crawl added
        # passes the id it wants to start past, and so does a test that must not
        # pick up the corpus somebody else's chunks are sitting in.
        self._start_after = start_after
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> EmbedStats:
        """Embed everything currently waiting, then return.

        The cursor advances past each batch rather than re-querying from zero,
        so a batch that failed does not become an infinite loop over the same
        rows — it is skipped, counted, and left for a later pass to retry once
        whatever broke has been fixed.
        """
        stats = EmbedStats()
        started = time.monotonic()
        after_id = self._start_after

        while not self._stopping.is_set():
            if self._max_batches is not None and stats.batches >= self._max_batches:
                break

            async with self._session_factory() as sess:
                chunks = await chunks_without_embeddings(
                    sess, limit=self._batch_size, after_id=after_id
                )
                if not chunks:
                    break
                # Read out of the ORM before the model runs: encoding is slow,
                # and holding a database connection across it would pin one for
                # the whole batch.
                batch = [(chunk.chunk_id, chunk.text) for chunk in chunks]

            after_id = batch[-1][0]
            stats.batches += 1

            try:
                # The model is synchronous and CPU-bound; a thread keeps the
                # event loop free so a signal still stops the pass promptly.
                vectors = await asyncio.to_thread(self._embedder.embed, [text for _, text in batch])
            except (EmbeddingError, ValueError):
                stats.failed_batches += 1
                log.exception(
                    "could not embed a batch; leaving it for a later pass",
                    extra={"chunks": len(batch), "after_id": after_id},
                )
                continue

            async with self._session_factory() as sess:
                # `strict=True`: a model that returned a different number of
                # vectors than it was given texts has silently misaligned every
                # chunk in the batch with somebody else's meaning, which is a
                # corruption no later check would catch.
                written = await store_embeddings(
                    sess,
                    {
                        chunk_id: vector
                        for (chunk_id, _), vector in zip(batch, vectors, strict=True)
                    },
                )
                await sess.commit()

            stats.embedded += written
            log.info(
                "embedded a batch",
                extra={"chunks": written, "after_id": after_id, "total": stats.embedded},
            )

        stats.seconds = time.monotonic() - started
        return stats

    async def run_forever(self) -> EmbedStats:
        """Keep the backlog at zero until stopped."""
        total = EmbedStats()
        while not self._stopping.is_set():
            stats = await self.run_once()
            total.embedded += stats.embedded
            total.batches += stats.batches
            total.failed_batches += stats.failed_batches
            total.seconds += stats.seconds
            if self._stopping.is_set():
                break
            if stats.embedded == 0:
                await self._sleep(self._idle_sleep_s)
        return total

    async def _sleep(self, seconds: float) -> None:
        """Wait, but wake immediately when asked to stop."""
        if seconds <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)


async def run_backfill(*, once: bool, max_batches: int | None) -> EmbedStats:
    embedder = BGEEmbedder(EmbedderSettings.from_env())
    backfill = Backfill(
        embedder,
        batch_size=_int_env("MERIDIAN_EMBED_CHUNK_BATCH", DEFAULT_CHUNK_BATCH),
        idle_sleep_s=float(os.environ.get("MERIDIAN_EMBED_IDLE_SLEEP_S") or DEFAULT_IDLE_SLEEP_S),
        max_batches=max_batches,
    )

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signame), backfill.stop)

    async with session() as sess:
        backlog = await embedding_backlog(sess)
    log.info("embedding backfill starting", extra={"backlog": backlog, "once": once})

    try:
        stats = await backfill.run_once() if once else await backfill.run_forever()
    finally:
        await dispose_engines()

    log.info("embedding backfill finished", extra=stats.as_dict())
    return stats


def main() -> None:
    """Entry point: `python -m worker.embed`."""
    parser = argparse.ArgumentParser(description="Give vectors to chunks that have none.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="drain the current backlog and exit, rather than watching for more",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="stop after this many batches; for a bounded first run",
    )
    args = parser.parse_args()

    configure_logging("embedder")
    with bind_run_id(f"embed-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_backfill(once=args.once, max_batches=args.max_batches))


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive, got {value}")
    return value


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
