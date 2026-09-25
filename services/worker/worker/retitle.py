"""Clean the titles already stored (task B-69).

``python -m worker.retitle`` — reports by default, writes only with
``--apply``, and never deletes: a title it replaces is kept in
``sources.extra['declared_title']``, and one it guessed from the text says so
in ``extra['title_from']``.

The fetch loop cleans titles as it writes them now (:mod:`meridian_core.titles`).
This pass does the same for the sources written before it, with the stored
passages standing in for the document's text.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Source
from meridian_core.titles import clean_title, title_from_text

log = get_logger(__name__)

#: Sources per transaction.
BATCH = 500

#: Passages read per source for the fallback: the heading is at the start.
LEAD_PASSAGES = 2


@dataclasses.dataclass
class RetitleStats:
    examined: int = 0
    unchanged: int = 0
    cleaned: int = 0
    from_text: int = 0
    cleared: int = 0
    samples: list[tuple[str | None, str | None]] = dataclasses.field(default_factory=list)
    by_old: collections.Counter = dataclasses.field(default_factory=collections.Counter)


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
    return {k: "\n".join(v) for k, v in out.items()}


def decide(
    title: str | None, *, publisher: str | None, url: str, lead: str | None
) -> tuple[str | None, bool]:
    """(the title to store, whether it came from the text)."""
    cleaned = clean_title(title, publisher=publisher, host=urlsplit(url).hostname)
    if cleaned is not None:
        return cleaned, False
    guessed = title_from_text(lead)
    return (guessed, True) if guessed is not None else (None, False)


async def run_pass(
    *, apply: bool, session_factory=session, sample: int = 25, start_after: int = 0
) -> RetitleStats:
    stats = RetitleStats()
    after = start_after
    while True:
        async with session_factory() as sess:
            rows = list(
                await sess.scalars(
                    select(Source)
                    .where(Source.source_id > after)
                    .order_by(Source.source_id)
                    .limit(BATCH)
                )
            )
            if not rows:
                break
            after = rows[-1].source_id
            leads = await _lead_text(sess, [r.source_id for r in rows])
            for source in rows:
                stats.examined += 1
                new, guessed = decide(
                    source.title,
                    publisher=source.publisher,
                    url=source.url,
                    lead=leads.get(source.source_id),
                )
                if new == source.title:
                    stats.unchanged += 1
                    continue
                if new is None:
                    stats.cleared += 1
                elif guessed:
                    stats.from_text += 1
                else:
                    stats.cleaned += 1
                stats.by_old[(source.title or "")[:40]] += 1
                if len(stats.samples) < sample:
                    stats.samples.append((source.title, new))
                if apply:
                    extra = dict(source.extra or {})
                    if source.title and "declared_title" not in extra:
                        extra["declared_title"] = source.title
                    if guessed:
                        extra["title_from"] = "text"
                    source.extra = extra
                    source.title = new
            if apply:
                await sess.commit()
        log.info("retitle batch", extra={"after_id": after, "examined": stats.examined})
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean stored source titles (B-69).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("retitle")

    async def go() -> RetitleStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"retitle-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    print(
        f"examined {stats.examined}  unchanged {stats.unchanged}  cleaned {stats.cleaned}  "
        f"from text {stats.from_text}  cleared to none {stats.cleared}"
    )
    print("  most common titles replaced:")
    for old, n in stats.by_old.most_common(15):
        print(f"    {n:6}  {old!r}")
    print("  samples (old -> new):")
    for old, new in stats.samples:
        print(f"    {old!r} -> {new!r}")
    if not args.apply:
        print("\nReport only. --apply writes; the old title is kept in extra.declared_title.")


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
