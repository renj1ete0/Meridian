"""Which topics each source's content is about (tasks P2-14, P2-21, §12.5).

``python -m worker.retopic`` — reports by default, writes only with ``--apply``.

`P2-14` wrote this as a URL-path backfill for sources crawled before topics
existed. `P2-21` made it the only thing that writes ``sources.topic_labels``:
the fetch path records why a page was crawled (``crawled_for``), and this pass
reads what the page says. The method, the thresholds and their calibration live
in :mod:`meridian_core.topiclabels`; this is the loop around them.

**A pass, not a fetch-time step**, for the reason `novelty` is one: the vectors
arrive after the fetch, from the `embed` service, so at the moment a page is
written there is nothing to compare. A source is only examined once every live
chunk it has is embedded — labelling half a document would label whichever half
embedded first.

**Resumable, and cheap to re-run.** The queue is a predicate — unexamined, or
examined under a different basis, or rewritten since — so a pass killed halfway
keeps what it committed and the next starts where it stopped. A topic added or
archived changes the basis and puts every source back in the queue; the cost of
that is one aggregate query per batch and a matrix product, no model calls
beyond embedding the prototypes once.

**`--demote-offtopic` is off unless asked for**, and does nothing without
``--apply``. It marks sources whose best topic scores under
:data:`~meridian_core.topiclabels.OFFTOPIC_FLOOR` as ``junk`` — which search,
the map and synthesis already leave out, and which the retention sweep will
drop the raw file of when *it* is run with ``--apply``. The scheduled run never
passes it.
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
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.embedder import EmbeddingUnavailable, RemoteEmbedder
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Source
from meridian_core.topiclabels import (
    LABEL_FLOOR,
    LABEL_MARGIN,
    OFFTOPIC_FLOOR,
    REFERENCE_TEXTS,
    Basis,
    basis_fingerprint,
    best_score,
    decide,
    demote,
    load_prototypes,
    offtopic_candidates,
    record_labels,
    source_vectors,
    sources_awaiting,
)

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: Sources per transaction.
BATCH = 500

#: Report histogram bins for the best-topic score.
BINS = (0.0, 0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 1.01)

#: Examples printed per category in the report.
EXAMPLES = 20


class Embeds(Protocol):
    """What the labeller needs from an embedder: vectors, and the model's name."""

    expect_model: str | None

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class NoTopics(RuntimeError):
    """Nothing to label against. Refused rather than recorded as ``{}`` for every
    source — that would claim the whole corpus was examined and found off-topic
    by a question nobody asked."""


@dataclasses.dataclass
class Example:
    source_id: int
    title: str | None
    url: str
    crawled_for: list[str] | None
    before: list[str] | None
    after: list[str]
    best: float | None


@dataclasses.dataclass
class LabelStats:
    examined: int = 0
    changed: int = 0
    batches: int = 0
    #: Sources by how many labels they got: 0, 1, 2, 3+.
    by_count: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    per_topic: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    best_hist: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Sources now labelled with a topic they were *not* crawled for, and
    #: sources crawled for a topic their content does not carry.
    gained_topic: int = 0
    lost_crawled_topic: int = 0
    offtopic: list[tuple[int, float]] = dataclasses.field(default_factory=list)
    demoted: int = 0
    samples: list[Example] = dataclasses.field(default_factory=list)
    offtopic_samples: list[Example] = dataclasses.field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "examined": self.examined,
            "changed": self.changed,
            "batches": self.batches,
            "labels_0": self.by_count[0],
            "labels_1": self.by_count[1],
            "labels_2": self.by_count[2],
            "labels_3_plus": self.by_count[3],
            "gained_topic": self.gained_topic,
            "lost_crawled_topic": self.lost_crawled_topic,
            "offtopic_candidates": len(self.offtopic),
            "demoted": self.demoted,
            "seconds": round(self.seconds, 1),
        }


def _bin(score: float) -> str:
    for low, high in zip(BINS, BINS[1:], strict=False):
        if score < high:
            return f"[{low:.2f}, {min(high, 1.0):.2f})"
    return f"[{BINS[-2]:.2f}, 1.00]"


class Labeller:
    """Labels every source whose labels are missing or stale."""

    def __init__(
        self,
        embedder: Embeds,
        *,
        session_factory: SessionFactory = session,
        batch_size: int = BATCH,
        start_after: int = 0,
        max_batches: int | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._embedder = embedder
        self._session_factory = session_factory
        self._batch_size = batch_size
        # Where the scan begins — a test that must not label the dev corpus
        # sitting beside its own rows starts past it, as `worker.embed` does.
        self._start_after = start_after
        self._max_batches = max_batches
        self._rng = rng or random.Random(0)
        self._offtopic_floor = OFFTOPIC_FLOOR

    async def basis(self) -> Basis:
        """Embed every labelling topic's prototype and the reference texts, once."""
        async with self._session_factory() as sess:
            prototypes = await load_prototypes(sess)
        if not prototypes:
            raise NoTopics("no topic is configured for labelling; nothing was examined")

        model = getattr(self._embedder, "expect_model", None)
        if not model and hasattr(self._embedder, "describe"):
            described = await self._embedder.describe()
            model = (described or {}).get("model")

        texts = [p.text for p in prototypes] + list(REFERENCE_TEXTS)
        vectors = await self._embedder.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingUnavailable(f"asked for {len(texts)} vectors and got {len(vectors)}")
        topic_vectors = {p.topic: vectors[i] for i, p in enumerate(prototypes)}
        return Basis.build(
            topic_vectors,
            vectors[len(prototypes) :],
            basis_fingerprint(prototypes, model),
        )

    async def run(
        self,
        *,
        apply: bool,
        demote_offtopic: bool = False,
        offtopic_floor: float = OFFTOPIC_FLOOR,
    ) -> LabelStats:
        """One pass over the queue. Writes only with ``apply``; demotes only with
        both ``apply`` and ``demote_offtopic``."""
        if not 0.0 <= offtopic_floor < LABEL_FLOOR:
            # A floor at or above the labelling floor would demote sources that
            # carry a label — about a topic, and junked for it.
            raise ValueError(
                f"the off-topic floor must be below the labelling floor ({LABEL_FLOOR})"
            )
        self._offtopic_floor = offtopic_floor
        stats = LabelStats()
        started = time.monotonic()
        basis = await self.basis()
        now = dt.datetime.now(dt.UTC)
        after = self._start_after
        seen = 0

        while self._max_batches is None or stats.batches < self._max_batches:
            async with self._session_factory() as sess:
                ids = await sources_awaiting(
                    sess, basis.fingerprint, limit=self._batch_size, after=after
                )
                if not ids:
                    break
                vectors = await source_vectors(sess, ids)
                rows = {
                    row.source_id: row
                    for row in await sess.scalars(select(Source).where(Source.source_id.in_(ids)))
                }
                for source_id in ids:
                    if source_id not in vectors:
                        continue
                    source = rows[source_id]
                    scores = basis.scores(vectors[source_id])
                    labels = decide(scores)
                    before = list(source.topic_labels) if source.topic_labels is not None else None
                    crawled = list(source.crawled_for or ())
                    self._tally(stats, source, scores, labels, before, crawled, seen)
                    seen += 1
                    if apply:
                        await record_labels(
                            sess,
                            source_id,
                            scores=scores,
                            fingerprint=basis.fingerprint,
                            now=now,
                        )
                if apply:
                    await sess.commit()
            after = ids[-1]
            stats.batches += 1
            log.info(
                "topic batch examined",
                extra={"sources": len(ids), "after_id": after, "applied": apply},
            )

        if demote_offtopic:
            async with self._session_factory() as sess:
                if apply:
                    # Everything labelled under this basis, not only this
                    # pass's rows: a source examined last week is exactly as
                    # off-topic today, and its stored scores are current.
                    candidates = await offtopic_candidates(
                        sess, basis.fingerprint, floor=offtopic_floor
                    )
                    stats.offtopic = candidates
                    stats.demoted = await demote(sess, [sid for sid, _ in candidates])
                    await sess.commit()
                    log.info(
                        "off-topic sources demoted to junk",
                        extra={"demoted": stats.demoted, "floor": offtopic_floor},
                    )
                else:
                    stored = await offtopic_candidates(
                        sess, basis.fingerprint, floor=offtopic_floor
                    )
                    fresh = {sid for sid, _ in stats.offtopic}
                    stats.offtopic.extend(c for c in stored if c[0] not in fresh)
                    stats.offtopic.sort()

        stats.seconds = time.monotonic() - started
        return stats

    def _tally(self, stats, source, scores, labels, before, crawled, seen) -> None:
        stats.examined += 1
        stats.by_count[min(len(labels), 3)] += 1
        stats.per_topic.update(labels)
        best = best_score(scores)
        if best is not None:
            stats.best_hist[_bin(best)] += 1
        if before is None or sorted(before) != sorted(labels):
            stats.changed += 1
        if set(labels) - set(crawled):
            stats.gained_topic += 1
        if set(crawled) - set(labels):
            stats.lost_crawled_topic += 1
        example = Example(
            source.source_id, source.title, source.url, crawled or None, before, labels, best
        )
        # Reservoir sampling, so the examples are a fair draw from the whole
        # pass rather than the first sources by id — which are whichever site
        # the crawl reached first.
        if len(stats.samples) < EXAMPLES:
            stats.samples.append(example)
        else:
            slot = self._rng.randint(0, seen)
            if slot < EXAMPLES:
                stats.samples[slot] = example
        if best is not None and best < self._offtopic_floor and source.retention_tier != "junk":
            stats.offtopic.append((source.source_id, best))
            if len(stats.offtopic_samples) < EXAMPLES:
                stats.offtopic_samples.append(example)
            else:
                slot = self._rng.randint(0, len(stats.offtopic) - 1)
                if slot < EXAMPLES:
                    stats.offtopic_samples[slot] = example


def _fmt(example: Example) -> str:
    title = (example.title or "(untitled)")[:60]
    best = "—" if example.best is None else f"{example.best:.3f}"
    return (
        f"    {title:60}  best={best}\n"
        f"      {example.url[:100]}\n"
        f"      crawled for {example.crawled_for or []}  was {example.before}  now {example.after}"
    )


def render(stats: LabelStats, *, apply: bool, demote_offtopic: bool, offtopic_floor: float) -> None:
    print("=== Topics from content (§12.5, P2-21) ===")
    print(f"  floor {LABEL_FLOOR}  margin {LABEL_MARGIN}  off-topic floor {offtopic_floor}")
    print(f"  examined         {stats.examined}")
    print(f"  labels changed   {stats.changed}")
    counts = stats.by_count
    print(f"  0 / 1 / 2 / 3+   {counts[0]} / {counts[1]} / {counts[2]} / {counts[3]}")
    for topic, n in sorted(stats.per_topic.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {topic:28} {n}")
    print(f"  labelled with a topic it was not crawled for   {stats.gained_topic}")
    print(f"  crawled for a topic its content does not carry {stats.lost_crawled_topic}")
    print("  best-topic score:")
    for key in sorted(stats.best_hist):
        print(f"    {key}  {stats.best_hist[key]}")
    if stats.samples:
        print("\n  A random sample:")
        for example in stats.samples:
            print(_fmt(example))
    print(f"\n  Below the off-topic floor: {len(stats.offtopic)}")
    for example in stats.offtopic_samples:
        print(_fmt(example))
    if demote_offtopic:
        if apply:
            print(f"\n  Demoted to junk: {stats.demoted}")
        else:
            print("\n  --demote-offtopic without --apply: nothing was demoted.")
    if not apply:
        print("\n  Report only. Pass --apply to write the labels.")


def main() -> None:
    """Entry point: ``python -m worker.retopic``."""
    parser = argparse.ArgumentParser(
        description="Label each source with the topics its content is about (§12.5, P2-21).",
    )
    parser.add_argument("--apply", action="store_true", help="write the labels")
    parser.add_argument(
        "--demote-offtopic",
        action="store_true",
        help=f"mark sources whose best topic scores under {OFFTOPIC_FLOOR} as junk. "
        "Off unless given, and a report only without --apply. Junk is left out of "
        "search, the map and synthesis, and the retention sweep drops its raw file.",
    )
    parser.add_argument(
        "--offtopic-floor",
        type=float,
        default=OFFTOPIC_FLOOR,
        help=f"the best-topic score under which --demote-offtopic demotes (default "
        f"{OFFTOPIC_FLOOR}; must be below the labelling floor, {LABEL_FLOOR})",
    )
    parser.add_argument("--max-batches", type=int, default=None)
    args = parser.parse_args()
    if not 0.0 <= args.offtopic_floor < LABEL_FLOOR:
        parser.error(f"--offtopic-floor must be in [0, {LABEL_FLOOR})")

    configure_logging("retopic")
    with bind_run_id(f"retopic-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        code = asyncio.run(
            _run(args.apply, args.demote_offtopic, args.offtopic_floor, args.max_batches)
        )
    sys.exit(code)


async def _run(
    apply: bool, demote_offtopic: bool, offtopic_floor: float, max_batches: int | None
) -> int:
    embedder = RemoteEmbedder.from_env()
    if embedder is None:
        # A failure, not a skip: a scheduled run that quietly did nothing
        # every hour would leave every new source unlabelled with no trace.
        print("MERIDIAN_EMBEDDER_URL is not set; topics come from vectors, and nothing embeds.")
        log.error("no embedder configured; no source was examined")
        await dispose_engines()
        return 2
    try:
        stats = await Labeller(embedder, max_batches=max_batches).run(
            apply=apply, demote_offtopic=demote_offtopic, offtopic_floor=offtopic_floor
        )
    except (NoTopics, EmbeddingUnavailable) as exc:
        print(str(exc))
        log.error("topic labelling did not run", extra={"reason": str(exc)})
        return 2
    finally:
        await embedder.aclose()
        await dispose_engines()
    log.info("topic labelling complete", extra={**stats.as_dict(), "applied": apply})
    render(stats, apply=apply, demote_offtopic=demote_offtopic, offtopic_floor=offtopic_floor)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
