"""Mark the contradictions already in the graph (task `P7-05`, §9).

Writes mark contradictions as they arrive (`meridian_core.contested`); this pass finds the
ones written before that, and reports marks the rule does not explain. Report by default;
``--apply`` marks. It never removes a mark. See
docs/features/knowledge-graph.md#contradictions.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from meridian_core.contested import ContestedReport, reconcile
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger

log = get_logger(__name__)


async def run_pass(*, apply: bool, session_factory=session) -> ContestedReport:
    async with session_factory() as sess:
        report = await reconcile(sess, apply=apply)
        if apply:
            await sess.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark contradicting edges as contested (P7-05).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("contested")

    async def go() -> ContestedReport:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"contested-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        report = asyncio.run(go())
    verb = "marked" if args.apply else "would mark"
    print(
        f"contradicting pairs {len(report.pairs)}; {verb} {len(report.unmarked)}; "
        f"marks the rule does not explain {len(report.unexplained)}"
    )
    for low, high in report.unmarked:
        print(f"  {verb}: {low} <-> {high}")
    for low, high in report.unexplained:
        print(f"  left alone: {low} <-> {high}")


if __name__ == "__main__":
    main()
