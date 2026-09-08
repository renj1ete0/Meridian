"""The novelty pass (task P2-03, spec §6.1, §5.4).

The gate itself lives in :mod:`meridian_core.novelty`, because it is a query
and this package must not own one. This is the thing that runs it: read a batch
of embedded-but-unjudged chunks, ask Postgres for each one's nearest earlier
neighbour, write the verdicts, demote the sources that turned out to be
duplicates of something the corpus already had.

**Its own process, not a stage of the fetch loop.** §6.1 draws the gate inside
the fast loop, between extract and store, and it cannot be there in this build
for the same reason embedding is not: the vector arrives one pass later
(`P2-01`), so at the moment a chunk is written there is nothing to compare. The
gate runs where the vectors are.

**And not a stage of the embedding backfill either**, though it could be. The
gate needs no model at all — it is Postgres and arithmetic — so binding it to
the one process that carries 2.3GB of weights would mean the corpus could only
be deduplicated on a machine that could also embed it. Two passes, one of which
runs anywhere.

**Resumable, and cheap to interrupt.** ``novelty_checked_at IS NULL`` is the
whole queue, one batch is one transaction, and the cursor moves past each batch
rather than re-querying from zero. A pass killed in hour three keeps everything
it committed and the next one starts where it stopped.

Runs on demand (``python -m worker.novelty``) or as a loop that watches the
backlog, exactly like ``worker.embed``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import signal
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.novelty import (
    NoveltySettings,
    chunks_awaiting_novelty,
    demote_duplicate_sources,
    judge,
    nearest_earlier_neighbours,
    novelty_backlog,
    record_verdicts,
)

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: How long the loop waits when nothing is waiting to be judged.
DEFAULT_IDLE_SLEEP_S = 60.0


@dataclasses.dataclass
class NoveltyStats:
    """What one pass did."""

    judged: int = 0
    duplicates: int = 0
    sources_demoted: int = 0
    batches: int = 0
    seconds: float = 0.0

    @property
    def pass_rate(self) -> float | None:
        """Fraction of this pass's chunks that were novel — §12.5's number."""
        if not self.judged:
            return None
        return round((self.judged - self.duplicates) / self.judged, 4)

    def as_dict(self) -> dict[str, object]:
        return {
            "judged": self.judged,
            "duplicates": self.duplicates,
            "sources_demoted": self.sources_demoted,
            "batches": self.batches,
            "pass_rate": self.pass_rate,
            "seconds": round(self.seconds, 1),
        }


class NoveltyPass:
    """Judges every embedded chunk that has not been judged."""

    def __init__(
        self,
        settings: NoveltySettings | None = None,
        *,
        session_factory: SessionFactory = session,
        idle_sleep_s: float = DEFAULT_IDLE_SLEEP_S,
        max_batches: int | None = None,
        start_after: int = 0,
    ) -> None:
        self._settings = settings or NoveltySettings()
        self._session_factory = session_factory
        self._idle_sleep_s = idle_sleep_s
        self._max_batches = max_batches
        # Where the scan begins. 0 is the whole table, which is what a first
        # pass wants; a test that must not judge the dev corpus sitting beside
        # its own rows passes the id it wants to start past, as
        # `worker.embed.Backfill` does for the same reason.
        self._start_after = start_after
        self._stopping = asyncio.Event()

    @property
    def settings(self) -> NoveltySettings:
        return self._settings

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> NoveltyStats:
        """Judge everything currently waiting, then return."""
        stats = NoveltyStats()
        started = time.monotonic()
        after_id = self._start_after

        while not self._stopping.is_set():
            if self._max_batches is not None and stats.batches >= self._max_batches:
                break

            async with self._session_factory() as sess:
                batch = await chunks_awaiting_novelty(
                    sess, limit=self._settings.batch_size, after_id=after_id
                )
                if not batch:
                    break

                chunk_ids = [chunk_id for chunk_id, _ in batch]
                # The whole batch is compared against the corpus as it stood
                # before the batch, and only then written. Judging and writing
                # one chunk at a time would let a chunk earlier in this batch
                # become a candidate for one later in it, which is the same
                # answer by a slower route — the id ordering already decides
                # who survives.
                neighbours = await nearest_earlier_neighbours(sess, chunk_ids)
                verdicts = judge(chunk_ids, neighbours, threshold=self._settings.threshold)
                written = await record_verdicts(sess, verdicts)
                demoted = await demote_duplicate_sources(
                    sess,
                    sorted({source_id for _, source_id in batch}),
                    fraction=self._settings.source_fraction,
                )
                await sess.commit()

            after_id = chunk_ids[-1]
            stats.batches += 1
            stats.judged += written
            stats.duplicates += sum(1 for verdict in verdicts if not verdict.novel)
            stats.sources_demoted += len(demoted)
            log.info(
                "novelty batch judged",
                extra={
                    "chunks": written,
                    "duplicates": sum(1 for v in verdicts if not v.novel),
                    "sources_demoted": len(demoted),
                    "after_id": after_id,
                },
            )

        stats.seconds = time.monotonic() - started
        return stats

    async def run_forever(self) -> NoveltyStats:
        """Keep the backlog at zero until stopped."""
        total = NoveltyStats()
        while not self._stopping.is_set():
            stats = await self.run_once()
            total.judged += stats.judged
            total.duplicates += stats.duplicates
            total.sources_demoted += stats.sources_demoted
            total.batches += stats.batches
            total.seconds += stats.seconds
            if self._stopping.is_set():
                break
            if stats.judged == 0:
                await self._sleep(self._idle_sleep_s)
        return total

    async def _sleep(self, seconds: float) -> None:
        """Wait, but wake immediately when asked to stop."""
        if seconds <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)


async def run_pass(*, once: bool, max_batches: int | None) -> NoveltyStats:
    settings = NoveltySettings.from_env()
    gate = NoveltyPass(settings, max_batches=max_batches)

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signame), gate.stop)

    async with session() as sess:
        backlog = await novelty_backlog(sess)
    log.info(
        "novelty gate starting",
        extra={"backlog": backlog, "threshold": settings.threshold, "once": once},
    )

    try:
        stats = await gate.run_once() if once else await gate.run_forever()
    finally:
        await dispose_engines()

    log.info("novelty gate finished", extra=stats.as_dict())
    return stats


def main() -> None:
    """Entry point: ``python -m worker.novelty``."""
    parser = argparse.ArgumentParser(
        description="Mark near-duplicate chunks, and the sources that are made of them."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="judge the current backlog and exit, rather than watching for more",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="stop after this many batches; for a bounded first run",
    )
    args = parser.parse_args()

    configure_logging("novelty")
    with bind_run_id(f"novelty-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_pass(once=args.once, max_batches=args.max_batches))


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
