"""Rebuild per-host relevance scores (task `B-48`).

``python -m worker.hostscore --once`` rewrites `host_scores` from the content
labels `worker.retopic` writes and from the pending queue. The fetch loop reads
the table every housekeeping tick to decide which links are worth queueing.
Scheduled hourly, after `topics`; derived, so a missed run only means the loop
decides on the previous hour's numbers. ``--report`` prints without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from meridian_core.db import dispose_engines, session
from meridian_core.hostscores import MIN_EXAMINED, Standing, load, recompute
from meridian_core.logging import bind_run_id, configure_logging, get_logger

log = get_logger(__name__)


async def run_once(*, write: bool) -> list[tuple[str, int, int, int, str]]:
    async with session() as sess:
        await recompute(sess)
        scores = await load(sess)
        rows = [
            (host, s.examined, s.on_topic, s.pending, s.standing.value)
            for host, s in scores.items()
            if s.examined >= MIN_EXAMINED or s.pending >= 100
        ]
        if write:
            await sess.commit()
        else:
            await sess.rollback()
    return sorted(rows, key=lambda r: (-r[3], -r[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild per-host relevance scores (B-48).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="rebuild and write")
    mode.add_argument("--report", action="store_true", help="rebuild and print; write nothing")
    args = parser.parse_args()
    configure_logging("hostscore")

    async def go():
        try:
            return await run_once(write=args.once)
        finally:
            await dispose_engines()

    with bind_run_id(f"hostscore-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        rows = asyncio.run(go())
    counts = {s.value: sum(1 for r in rows if r[4] == s.value) for s in Standing}
    print(f"hosts judged or busy: {len(rows)}  {counts}")
    for host, examined, on_topic, pending, standing in rows[:40]:
        print(f"  {standing:9}  {on_topic:4}/{examined:<5} examined  {pending:6} pending  {host}")


if __name__ == "__main__":
    main()
