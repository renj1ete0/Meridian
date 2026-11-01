"""Watched news sitemaps, feeds and listing pages, read again on an interval (`B-249`).

A discovered sitemap is read once. A news sitemap or a feed lists only what was
published in the last day or two, so it is queued again whenever its interval has
passed; the worker reads it as any sitemap and queues the entries it has not seen. A
listing watch (`B-255`) is an outlet's latest-articles page, read for its article links.
See docs/features/discovery.md#watched-sources.

    python -m worker.watch --apply                         # the scheduled pass
    python -m worker.watch add URL [--feed|--listing] [--topic T] [--headlines] [--every 900]
    python -m worker.watch list
    python -m worker.watch enable|disable WATCH_ID
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import time
from urllib.parse import urlsplit

from sqlalchemy import select

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, TopicConfig, Watch
from meridian_core.models.config import MIN_WATCH_INTERVAL_S, WATCH_KIND
from meridian_core.queueing import enqueue

log = get_logger(__name__)

#: A watch's read goes ahead of the crawl's own work, level with an operator's seed:
#: what it reveals is hours old and ages by the hour.
WATCH_PRIORITY = 100

#: Statuses of a read still in flight; while one is, the next is not queued.
IN_FLIGHT = ("pending",)


@dataclasses.dataclass
class WatchStats:
    due: int = 0
    queued: list[str] = dataclasses.field(default_factory=list)
    in_flight: int = 0


def is_due(watch: Watch, now: dt.datetime) -> bool:
    """Whether ``watch`` should be read now: enabled, and never read or its interval passed."""
    if not watch.enabled:
        return False
    if watch.last_queued_at is None:
        return True
    return now - watch.last_queued_at >= dt.timedelta(seconds=watch.interval_seconds)


async def run_pass(
    *, apply: bool, now: dt.datetime | None = None, session_factory=session
) -> WatchStats:
    """Queue a read of every watch that is due, unless its last one is still waiting."""
    now = now or dt.datetime.now(dt.UTC)
    stats = WatchStats()
    async with session_factory() as sess:
        watches = (await sess.scalars(select(Watch).where(Watch.enabled))).all()
        due = [w for w in watches if is_due(w, now)]
        stats.due = len(due)
        waiting = set(
            (
                await sess.scalars(
                    select(QueueTask.url_or_query).where(
                        QueueTask.url_or_query.in_([w.url for w in due]),
                        QueueTask.task_type == "sitemap",
                        QueueTask.status.in_(IN_FLIGHT),
                    )
                )
            ).all()
        )
        for watch in due:
            if watch.url in waiting:
                # A crawl that is behind should not stack reads of the same list.
                stats.in_flight += 1
                continue
            stats.queued.append(watch.url)
            if apply:
                await enqueue(
                    sess,
                    watch.url,
                    topic=watch.topic,
                    seed_source="watch",
                    task_type="sitemap",
                    priority=WATCH_PRIORITY,
                )
                watch.last_queued_at = now
        if apply:
            await sess.commit()
    log.info(
        "watch pass complete",
        extra={
            "due": stats.due,
            "queued": len(stats.queued),
            "in_flight": stats.in_flight,
            "applied": apply,
        },
    )
    return stats


class WatchRefused(ValueError):
    """A watch that cannot be added as asked."""


async def add_watch(
    sess,
    url: str,
    *,
    kind: str = "sitemap",
    topic: str | None = None,
    headline_filter: bool = False,
    interval_seconds: int = 900,
    note: str | None = None,
) -> Watch:
    """Add one watch; flushes, does not commit. Refuses what the table would refuse, by name."""
    split = urlsplit(url.strip())
    if split.scheme not in ("http", "https") or not split.hostname:
        raise WatchRefused(f"not an http(s) address: {url!r}")
    if kind not in WATCH_KIND.enums:
        raise WatchRefused(f"no watch kind {kind!r}; expected one of {', '.join(WATCH_KIND.enums)}")
    if interval_seconds < MIN_WATCH_INTERVAL_S:
        raise WatchRefused(f"an interval under {MIN_WATCH_INTERVAL_S} s is refused")
    if topic is not None and await sess.get(TopicConfig, topic) is None:
        raise WatchRefused(f"no topic named {topic!r}")
    if topic is None and not headline_filter:
        # Everything an untopiced outlet publishes would be crawled, sport included.
        raise WatchRefused("a watch without a topic must filter by headline")
    if await sess.scalar(select(Watch).where(Watch.url == url.strip())) is not None:
        raise WatchRefused(f"already watched: {url}")
    watch = Watch(
        url=url.strip(),
        kind=kind,
        topic=topic,
        headline_filter=headline_filter,
        interval_seconds=interval_seconds,
        note=note,
    )
    sess.add(watch)
    await sess.flush()
    return watch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="queue the due reads")
    sub = parser.add_subparsers(dest="command")
    add = sub.add_parser("add", help="watch a news sitemap, a feed or a latest-articles page")
    add.add_argument("url")
    kinds = add.add_mutually_exclusive_group()
    kinds.add_argument("--feed", action="store_true", help="an RSS or Atom feed")
    kinds.add_argument(
        "--listing", action="store_true", help="a latest-articles page, read for its links"
    )
    add.add_argument("--topic")
    add.add_argument("--headlines", action="store_true", help="queue only matching headlines")
    add.add_argument("--every", type=int, default=900, help="seconds between reads")
    add.add_argument("--note")
    sub.add_parser("list", help="every watch")
    for name in ("enable", "disable"):
        sub.add_parser(name).add_argument("watch_id", type=int)
    args = parser.parse_args(argv)
    configure_logging("watch")

    async def go() -> int:
        try:
            return await _run(args)
        finally:
            await dispose_engines()

    with bind_run_id(f"watch-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(go())
    return 130


async def _run(args: argparse.Namespace) -> int:
    if args.command is None:
        stats = await run_pass(apply=args.apply)
        print(f"due {stats.due}, queued {len(stats.queued)}, still waiting {stats.in_flight}")
        return 0
    async with session() as sess:
        if args.command == "add":
            try:
                watch = await add_watch(
                    sess,
                    args.url,
                    kind="feed" if args.feed else "listing" if args.listing else "sitemap",
                    topic=args.topic,
                    headline_filter=args.headlines,
                    interval_seconds=args.every,
                    note=args.note,
                )
            except WatchRefused as exc:
                print(f"refused: {exc}")
                return 2
            await sess.commit()
            print(f"watch {watch.watch_id}: {watch.url}")
            return 0
        if args.command == "list":
            for w in (await sess.scalars(select(Watch).order_by(Watch.watch_id))).all():
                state = "on " if w.enabled else "off"
                print(
                    f"{w.watch_id:>4} {state} {w.kind:<7} every {w.interval_seconds:>5}s "
                    f"{w.topic or '(headlines)':<20} {w.url}"
                )
            return 0
        watch = await sess.get(Watch, args.watch_id)
        if watch is None:
            print(f"no watch {args.watch_id}")
            return 2
        watch.enabled = args.command == "enable"
        await sess.commit()
        print(f"watch {watch.watch_id} {'enabled' if watch.enabled else 'disabled'}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
