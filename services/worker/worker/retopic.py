"""Backfilling topics onto sources crawled before the column existed (task P2-14).

``python -m worker.retopic`` — reports by default, writes only with ``--apply``.

**Only the URL, never the queue.** The live path (`main.py`) unions two things:
the claim's topic, which is *provenance* — the reason this URL was fetched, held
on the queue row that produced the fetch — and a match against the URL itself.
After the fact only the second is available, and the join that would recover the
first is exactly the one `P2-14` rejected: a URL can be enqueued repeatedly under
different topics, and a redirect means the fetched URL is frequently not the
queued one. A label attached by a wrong join is indistinguishable from one the
crawl established, which makes it worse than no label at all.

So this pass records less than the live path does, deliberately, and says so.

**NULL and `{}` are different, and that difference is the queue.** NULL means no
pass has examined this source. `{}` means one has, and the URL implied nothing —
which is a real answer, and the thing that stops the next run re-reading the same
rows forever.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import time

from sqlalchemy import select

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Source

from .topicmatch import TopicVocabulary, load_topic_vocabulary

log = get_logger(__name__)

#: Sources per transaction.
BATCH = 500


@dataclasses.dataclass
class RetopicStats:
    examined: int = 0
    labelled: int = 0
    unmatched: int = 0

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


async def unexamined(sess, limit: int) -> list[Source]:
    """Sources no pass has looked at. ``topic_labels IS NULL`` is the whole queue."""
    rows = await sess.scalars(
        select(Source)
        .where(Source.topic_labels.is_(None))
        .order_by(Source.source_id)
        .limit(limit)
    )
    return list(rows)


def labels_for(vocabulary: TopicVocabulary, source: Source) -> list[str]:
    """Topics the source's own address implies.

    The URL actually served where a redirect recorded one, for the live path's
    reason: a redirect to `/transport/walking/...` says something about the
    document, and the address that was asked for says only what was guessed.
    """
    final = (source.extra or {}).get("final_url")
    return sorted(vocabulary.topics_for(final or source.url))


async def run_pass(*, apply: bool, vocabulary: TopicVocabulary | None = None) -> RetopicStats:
    stats = RetopicStats()
    async with session("rw") as sess:
        vocab = vocabulary if vocabulary is not None else await load_topic_vocabulary(sess)
        if not vocab:
            # An empty vocabulary would stamp `{}` on every source in the
            # corpus, which is a pass recording "examined, matched nothing"
            # about a question it never actually asked — and it is not
            # repeatable afterwards, because the queue is gone.
            log.warning("no topic vocabulary; nothing to match against")
            return stats

        while True:
            batch = await unexamined(sess, BATCH)
            if not batch:
                break
            for source in batch:
                labels = labels_for(vocab, source)
                stats.examined += 1
                if labels:
                    stats.labelled += 1
                else:
                    stats.unmatched += 1
                if apply:
                    source.topic_labels = labels
            if apply:
                await sess.commit()
            else:
                # Nothing was written, so the queue has not shrunk and the next
                # read would return the same batch forever.
                break

    log.info("retopic complete", extra={**stats.as_dict(), "applied": apply})
    return stats


def render(stats: RetopicStats, apply: bool) -> None:
    print("=== Topic backfill (§12.5, P2-14) ===")
    print(f"  examined     {stats.examined}")
    print(f"  labelled     {stats.labelled}")
    print(f"  no match     {stats.unmatched}")
    if not apply:
        print(f"\n  Dry run over the first {BATCH}. Pass --apply to write, and to continue.")
        print("  This matches on the URL only — the crawl's own topic is not recoverable")
        print("  after the fact, so these labels are weaker than the ones a crawl records.")


def main() -> None:
    """Entry point: ``python -m worker.retopic``."""
    parser = argparse.ArgumentParser(
        description="Label already-crawled sources by matching their URLs (§12.5).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the labels. Without it nothing is stored — and because the "
        "queue is `topic_labels IS NULL`, a dry run can only see the first batch.",
    )
    args = parser.parse_args()

    configure_logging("retopic")
    with bind_run_id(f"retopic-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(_run(apply=args.apply))
    render(stats, args.apply)


async def _run(*, apply: bool) -> RetopicStats:
    stats = await run_pass(apply=apply)
    await dispose_engines()
    return stats


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
