"""Which places each source's content is about (task P2-23, §7.2).

``python -m worker.places`` — reports by default, writes only with ``--apply``. The
loop around :mod:`meridian_core.places`, shaped like `worker.retopic`. Needs neither a
model nor the embedder.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import datetime as dt
import random
import sys
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Source
from meridian_core.placenames import display_name, is_city
from meridian_core.places import (
    MIN_MENTIONS,
    SHARE_OF_BEST,
    basis_fingerprint,
    cited_places,
    examine,
    load_vocabulary,
    record_places,
    source_texts,
    sources_awaiting,
)

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: Sources per transaction.
BATCH = 200

#: Examples printed in the report.
EXAMPLES = 20


@dataclasses.dataclass
class Example:
    source_id: int
    title: str | None
    url: str
    before: list[str] | None
    after: list[str]
    decided: dict[str, list[str]]


@dataclasses.dataclass
class PlaceStats:
    examined: int = 0
    changed: int = 0
    batches: int = 0
    #: Sources by how many countries they got: 0, 1, 2, 3+.
    by_count: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    per_place: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Which combination of signals tagged each place, as ``text+domain`` etc.
    per_signals: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    samples: list[Example] = dataclasses.field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "examined": self.examined,
            "changed": self.changed,
            "batches": self.batches,
            "no_place": self.by_count[0],
            "one_country": self.by_count[1],
            "two_countries": self.by_count[2],
            "three_plus": self.by_count[3],
            "seconds": round(self.seconds, 1),
        }


class PlaceTagger:
    """Tags every source whose places are missing or stale."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = session,
        batch_size: int = BATCH,
        start_after: int = 0,
        max_batches: int | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._batch_size = batch_size
        # Where the scan begins — a test that must not tag the dev corpus
        # sitting beside its own rows starts past it, as `worker.retopic` does.
        self._start_after = start_after
        self._max_batches = max_batches
        self._rng = rng or random.Random(0)

    async def run(self, *, apply: bool) -> PlaceStats:
        """One pass over the queue. Writes only with ``apply``."""
        stats = PlaceStats()
        started = time.monotonic()
        async with self._session_factory() as sess:
            vocab = await load_vocabulary(sess)
        fingerprint = basis_fingerprint(vocab)
        now = dt.datetime.now(dt.UTC)
        after = self._start_after

        while self._max_batches is None or stats.batches < self._max_batches:
            async with self._session_factory() as sess:
                ids = await sources_awaiting(sess, fingerprint, limit=self._batch_size, after=after)
                if not ids:
                    break
                texts = await source_texts(sess, ids)
                entities = await cited_places(sess, ids)
                rows = {
                    row.source_id: row
                    for row in await sess.scalars(select(Source).where(Source.source_id.in_(ids)))
                }
                for source_id in ids:
                    source = rows[source_id]
                    examined = examine(
                        source, texts.get(source_id, ""), vocab, entities.get(source_id)
                    )
                    before = list(source.places) if source.places is not None else None
                    self._tally(stats, source, examined.decision, before)
                    if apply:
                        await record_places(sess, examined, fingerprint=fingerprint, now=now)
                if apply:
                    await sess.commit()
            after = ids[-1]
            stats.batches += 1
            log.info(
                "place batch examined",
                extra={"sources": len(ids), "after_id": after, "applied": apply},
            )

        stats.seconds = time.monotonic() - started
        return stats

    def _tally(self, stats: PlaceStats, source: Source, decision, before) -> None:
        seen = stats.examined
        stats.examined += 1
        countries = [c for c in decision.places if not is_city(c)]
        stats.by_count[min(len(countries), 3)] += 1
        stats.per_place.update(decision.places)
        for signals in decision.decided.values():
            stats.per_signals["+".join(signals)] += 1
        if before is None or sorted(before) != sorted(decision.places):
            stats.changed += 1
        example = Example(
            source.source_id, source.title, source.url, before, decision.places, decision.decided
        )
        # Reservoir sampling, so the examples are a fair draw from the whole
        # pass rather than the first site the crawl reached.
        if len(stats.samples) < EXAMPLES:
            stats.samples.append(example)
        else:
            slot = self._rng.randint(0, seen)
            if slot < EXAMPLES:
                stats.samples[slot] = example


def render(stats: PlaceStats, *, apply: bool) -> None:
    print("=== Places from content (§7.2, P2-23) ===")
    print(f"  at least {MIN_MENTIONS} mentions, and {SHARE_OF_BEST:.0%} of the most-named country")
    print(f"  examined         {stats.examined}")
    print(f"  places changed   {stats.changed}")
    counts = stats.by_count
    print(f"  countries 0 / 1 / 2 / 3+   {counts[0]} / {counts[1]} / {counts[2]} / {counts[3]}")
    print("  by place:")
    for code, n in sorted(stats.per_place.items(), key=lambda kv: (-kv[1], kv[0]))[:40]:
        print(f"    {code:6} {display_name(code):28} {n}")
    print("  decided by:")
    for signals, n in stats.per_signals.most_common():
        print(f"    {signals:32} {n}")
    if stats.samples:
        print("\n  A random sample:")
        for ex in stats.samples:
            print(f"    #{ex.source_id} {(ex.title or '(untitled)')[:70]}")
            print(f"      {ex.url[:100]}")
            print(f"      was {ex.before}  now {ex.after}  by {ex.decided}")
    if not apply:
        print("\n  Report only. Pass --apply to write the places.")


def main() -> None:
    """Entry point: ``python -m worker.places``."""
    parser = argparse.ArgumentParser(
        description="Tag each source with the places its content is about (§7.2, P2-23).",
    )
    parser.add_argument("--apply", action="store_true", help="write the places")
    parser.add_argument("--max-batches", type=int, default=None)
    args = parser.parse_args()

    configure_logging("places")
    with bind_run_id(f"places-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        code = asyncio.run(_run(args.apply, args.max_batches))
    sys.exit(code)


async def _run(apply: bool, max_batches: int | None) -> int:
    try:
        stats = await PlaceTagger(max_batches=max_batches).run(apply=apply)
    finally:
        await dispose_engines()
    log.info("place tagging complete", extra={**stats.as_dict(), "applied": apply})
    render(stats, apply=apply)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
