"""The embedding backfill (task P2-01, spec §6.1, §4).

A separate pass from the crawl: `embedding IS NULL` is the whole queue, one batch is one
transaction, and an interrupted pass keeps everything up to its last batch. Runs on
demand (`python -m worker.embed`) or as a loop that watches the backlog; never inside
`worker.main`. See docs/features/embedding.md#the-backfill.
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
    EMBED_TIERS,
    NEWEST_FIRST_TIER,
    chunks_without_embeddings,
    embedding_backlog,
    store_embeddings,
)
from meridian_core.db import dispose_engines, session
from meridian_core.embedtext import VIEW_VERSION, embedding_view
from meridian_core.logging import bind_run_id, configure_logging, get_logger

from .embeddings import EmbeddingError
from .liveness import beat
from .vectors import AsyncEmbedder, build_embedder

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
        embedder: AsyncEmbedder,
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
        # Where the queue scan begins; 0 is the whole table. An operator or a test
        # passes the id to start past.
        self._start_after = start_after
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> EmbedStats:
        """Embed everything currently waiting, then return.

        The cursor advances past each batch, so a failed batch is skipped, counted
        and left for a later pass rather than retried for ever.
        """
        stats = EmbedStats()
        started = time.monotonic()
        # One cursor per tier (`B-66`). Each batch is drawn from the highest
        # tier with anything left, re-checked every batch, so passages a search
        # brings in mid-pass go ahead of the off-topic tail already queued.
        cursors = dict.fromkeys(EMBED_TIERS, self._start_after)
        after_id = self._start_after
        # `B-75`: the first tier newest first, so what a crawl just fetched is
        # labelled while it can still steer the crawl. No cursor — embedded
        # rows leave the queue — so a failed batch is stepped past by id.
        failed: set[int] = set()

        while not self._stopping.is_set():
            # Before the batch, not after it (`B-28`): a batch can take minutes, and a
            # heartbeat written on completion would go stale during normal work.
            beat()
            if self._max_batches is not None and stats.batches >= self._max_batches:
                break

            async with self._session_factory() as sess:
                chunks, tier = [], None
                for tier in EMBED_TIERS:
                    newest = tier == NEWEST_FIRST_TIER
                    chunks = await chunks_without_embeddings(
                        sess,
                        limit=self._batch_size,
                        after_id=self._start_after if newest else cursors[tier],
                        tier=tier,
                        newest_first=newest,
                        exclude=failed if newest else (),
                    )
                    if chunks:
                        break
                if not chunks:
                    break
                # Read out of the ORM before the model runs, so no connection is held
                # across encoding. The view, not the stored text (`B-49`).
                batch = [(chunk.chunk_id, embedding_view(chunk.text)) for chunk in chunks]

            if tier == NEWEST_FIRST_TIER:
                after_id = batch[0][0]
            else:
                after_id = cursors[tier] = batch[-1][0]
            stats.batches += 1

            try:
                # Async: the embedder may be the sidecar (`P2-19`); `LocalEmbedder`
                # keeps the loop free in-process, so a signal still stops the pass.
                vectors = await self._embedder.embed([text for _, text in batch])
            except (EmbeddingError, ValueError):
                stats.failed_batches += 1
                failed.update(chunk_id for chunk_id, _ in batch)
                log.exception(
                    "could not embed a batch; leaving it for a later pass",
                    extra={"chunks": len(batch), "after_id": after_id},
                )
                continue

            async with self._session_factory() as sess:
                # `strict=True`: a count mismatch would silently pair every chunk with
                # another's vector.
                written = await store_embeddings(
                    sess,
                    {
                        chunk_id: vector
                        for (chunk_id, _), vector in zip(batch, vectors, strict=True)
                    },
                    view=VIEW_VERSION,
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
                # An idle pass is still a live one: beat while the backlog is empty too.
                beat()
                await self._sleep(self._idle_sleep_s)
        return total

    async def _sleep(self, seconds: float) -> None:
        """Wait, but wake immediately when asked to stop."""
        if seconds <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)


async def run_backfill(*, once: bool, max_batches: int | None) -> EmbedStats:
    # The sidecar when there is one (`P2-19`). A stack running both the query
    # path and this pass used to hold two copies of 2.3GB of weights on a
    # machine chosen for being small.
    embedder = await build_embedder()
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
