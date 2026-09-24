"""Recompute which lines each host repeats (task `B-43`).

``python -m worker.boilerplate --once`` rebuilds `boilerplate_lines` from
`page_lines`, which the fetch path writes from each page's uncleaned text. The
fetch path then cleans every page against its host's set. Scheduled daily; the
table is derived, so running it again is always safe and a missed run only
means pages are cleaned against the previous set.

``--report`` prints what the rebuild found, per host, without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from sqlalchemy import func, select, text

from meridian_core.boilerplate import MIN_PAGES, MIN_SHARE, recompute
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import BoilerplateLine

log = get_logger(__name__)


async def run_once(*, write: bool, min_pages: int = MIN_PAGES, min_share: float = MIN_SHARE):
    """One rebuild. Returns ``(hosts, lines, per_host)``; commits only with ``write``."""
    async with session() as sess:
        result = await recompute(sess, min_pages=min_pages, min_share=min_share)
        per_host = (
            await sess.execute(
                select(BoilerplateLine.host, func.count(), func.max(BoilerplateLine.host_pages))
                .group_by(BoilerplateLine.host)
                .order_by(func.count().desc())
                .limit(30)
            )
        ).all()
        if write:
            await sess.commit()
        else:
            await sess.rollback()
    return result.hosts, result.lines, per_host


async def pages_recorded() -> int:
    async with session() as sess:
        return int(await sess.scalar(text("SELECT count(DISTINCT source_id) FROM page_lines")) or 0)


def main() -> None:
    """Entry point: ``python -m worker.boilerplate``."""
    parser = argparse.ArgumentParser(description="Rebuild per-host boilerplate lines (B-43).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="rebuild and write")
    mode.add_argument("--report", action="store_true", help="rebuild, print, write nothing")
    parser.add_argument("--min-pages", type=int, default=MIN_PAGES)
    parser.add_argument("--min-share", type=float, default=MIN_SHARE)
    args = parser.parse_args()

    configure_logging("boilerplate")

    async def go():
        try:
            recorded = await pages_recorded()
            found = await run_once(
                write=args.once, min_pages=args.min_pages, min_share=args.min_share
            )
            return recorded, found
        finally:
            await dispose_engines()

    with bind_run_id(f"boilerplate-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        recorded, (hosts, lines, per_host) = asyncio.run(go())
    print(f"pages with recorded lines  {recorded}")
    print(f"repeated lines             {lines} on {hosts} hosts")
    for host, count, host_pages in per_host:
        print(f"  {count:4d}  {host}  ({host_pages} pages)")
    if args.report:
        print("\nReport only. --once writes.")


if __name__ == "__main__":
    main()
