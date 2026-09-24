"""Fold the duplicate edges that merges before `B-41` left side by side.

A merge used to re-point the absorbed entity's edges without asking whether the
target already held the same claim, so one subject, relation and object could
end up as two rows. `merge` now folds them as it goes; this pass folds the ones
already in the graph, the same way, and logs each fold on the merge that caused
it so reversing that merge splits them again (`resolution.fold_repeated_edges`).

Report by default; ``--apply`` writes. A duplicate no merge explains is listed
and left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.resolution import RepeatedEdges, fold_repeated_edges

log = get_logger(__name__)


async def run_pass(*, apply: bool, session_factory=session) -> RepeatedEdges:
    async with session_factory() as sess:
        report = await fold_repeated_edges(sess, apply=apply)
        if apply:
            await sess.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fold duplicate edges left by merges (B-41).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("edgedupes")

    async def go() -> RepeatedEdges:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"edgedupes-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        report = asyncio.run(go())
    verb = "folded" if args.apply else "would fold"
    print(
        f"repeated triples {report.groups}; {verb} {report.folded} edge(s); "
        f"unexplained by any merge {len(report.unattributed)}"
    )
    for merge_id, survivor, folded in report.attributed:
        print(f"  merge {merge_id}: {folded} -> {survivor}")
    for ids in report.unattributed:
        print(f"  left alone: {ids}")


if __name__ == "__main__":
    main()
