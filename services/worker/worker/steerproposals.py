"""Propose steering changes, and apply the ones nobody rejected (task `P6-38`).

``python -m worker.steerproposals --once`` measures each active topic's share of
the last day's new on-topic sources and fetches against its weight, writes a
proposal for a topic that is starved or over-served, and applies every pending
proposal whose window has passed with its basis intact. ``--report`` does the
same inside a transaction it rolls back, and prints what it would have done.

Hourly on the timetable. Counting only — no model is called here (§2.1); the
heuristic and its bounds live in :mod:`meridian_core.steering_proposals`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import time

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.steering_proposals import PassReport, run_pass

log = get_logger(__name__)


async def run_once(*, write: bool, now: dt.datetime | None = None) -> PassReport:
    moment = now or dt.datetime.now(dt.UTC)
    async with session() as sess:
        report = await run_pass(sess, now=moment)
        if write:
            await sess.commit()
        else:
            await sess.rollback()
    log.info(
        "steering proposals pass",
        extra={
            "proposed": len(report.created),
            "kept": len(report.kept),
            "superseded": len(report.superseded),
            "applied": len(report.applied),
            "failed": len(report.failed),
            "window_hours": report.window_hours,
            "written": write,
        },
    )
    return report


def summary(report: PassReport) -> str:
    return (
        f"proposed {len(report.created)}, still pending {len(report.kept)}, "
        f"superseded {len(report.superseded)}, applied {len(report.applied)}, "
        f"refused {len(report.failed)} (window {report.window_hours:g}h)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Steering proposals that apply by default.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="propose, apply what is due, write")
    mode.add_argument("--report", action="store_true", help="the same, rolled back; print it")
    args = parser.parse_args()
    configure_logging("steerproposals")

    async def go() -> PassReport:
        try:
            return await run_once(write=args.once)
        finally:
            await dispose_engines()

    with bind_run_id(f"steerproposals-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        report = asyncio.run(go())
        print(summary(report))


if __name__ == "__main__":
    main()
