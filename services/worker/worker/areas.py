"""Rebuild the corpus's areas (task `P6-30`).

``python -m worker.areas --once`` clusters every searchable, embedded passage
into regions, areas and sub-areas (`meridian_core.areabuild`) and writes them
as one build, which the map reads. Scheduled daily; derived data, so running
it again is always safe and a missed run only means the map shows yesterday's.

``--report`` builds, prints the areas, and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from sqlalchemy import select

from meridian_core.areabuild import BuildReport, build_areas
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Area

log = get_logger(__name__)


async def run_once(*, write: bool) -> tuple[BuildReport, list[tuple[int, int, int, list[str]]]]:
    """One build. Returns the report and ``(level, passages, sources, terms)`` rows."""
    async with session() as sess:
        report = await build_areas(sess)
        rows: list[tuple[int, int, int, list[str]]] = []
        if report.build_id is not None:
            rows = [
                (level, passages, sources, list(terms))
                for level, passages, sources, terms in await sess.execute(
                    select(Area.level, Area.passages, Area.sources, Area.terms)
                    .where(Area.build_id == report.build_id, Area.level < 3)
                    .order_by(Area.level, Area.passages.desc())
                )
            ]
        if write:
            await sess.commit()
        else:
            await sess.rollback()
    log.info(
        "areas built",
        extra={
            "build_id": report.build_id,
            "passages": report.passages,
            "regions": report.regions,
            "areas": report.areas,
            "leaves": report.leaves,
            "inherited": report.inherited,
            "bridges": report.bridges,
            "seconds": report.seconds,
            "written": write,
        },
    )
    return report, rows


def main() -> None:
    """Entry point: ``python -m worker.areas``."""
    parser = argparse.ArgumentParser(description="Rebuild the corpus's areas (P6-30).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="build and write")
    mode.add_argument("--report", action="store_true", help="build, print, write nothing")
    args = parser.parse_args()

    configure_logging("areas")

    async def go():
        try:
            return await run_once(write=args.once)
        finally:
            await dispose_engines()

    with bind_run_id(f"areas-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        report, rows = asyncio.run(go())
    if report.build_id is None:
        print(f"{report.passages} searchable embedded passages: too few to cluster.")
        return
    print(
        f"{report.passages} passages → {report.regions} regions, {report.areas} areas, "
        f"{report.leaves} sub-areas, {report.bridges} bridges in {report.seconds}s; "
        f"{report.inherited} kept their position"
    )
    for level, passages, sources, terms in rows:
        indent = "  " if level == 2 else ""
        print(f"{indent}{passages:6d} passages {sources:5d} sources  {' · '.join(terms[:4])}")
    if args.report:
        print("\nReport only. --once writes.")


if __name__ == "__main__":
    main()
