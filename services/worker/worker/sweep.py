"""The retention sweep as a pass (task P1-31, spec §5.4).

``python -m worker.sweep`` — reports by default, deletes only with ``--apply``.

A fourth pass beside `main`, `embed` and `novelty`, and the only one that
destroys anything. That is why it is a separate process rather than housekeeping
inside the fetch loop: a sweep that ran automatically every hour would eventually
run at the same moment as the mistake that made something droppable, and the
window between "wrongly demoted" and "file gone" would be an hour rather than
however long it takes someone to read a report.

It also cannot be resumed the way the other passes can. Their queues are
predicates over a column, so a half-finished pass is indistinguishable from one
that has not started; a half-finished sweep has deleted some files and not
others, and the plan it was working from is stale.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import time

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.retention import RetentionPlan, apply_sweep, plan_sweep

from .rawstore import raw_root

log = get_logger(__name__)


def render(plan: RetentionPlan, root: str) -> None:
    """The report. Written to be read by a person deciding whether to `--apply`."""
    print(f"=== Retention sweep — {root} ===")
    print(f"  files on disk      {plan.files_seen}")
    print(f"  protected          {plan.protected}  (primary, or background and cited)")
    print(f"  droppable          {len(plan.droppable)}")
    print(f"  orphaned           {len(plan.orphaned)}")
    print(f"  dangling           {len(plan.dangling)}  (rows with no file — never deleted)")
    print(f"  elsewhere          {len(plan.elsewhere)}  (files under a different recorded root)")

    if plan.reclaimable_bytes:
        print(f"\n  reclaimable: {plan.reclaimable_bytes / 1024:.1f} KiB")
    for candidate in (*plan.droppable, *plan.orphaned)[:20]:
        print(f"    - {candidate.path}  ({candidate.reason})")

    if plan.dangling:
        # The arm that found something on the first real corpus, and the reason
        # this pass is worth running even when there is nothing to reclaim. A
        # source claiming a file it does not have is a citation that will not
        # open, and nothing else in the system notices: the row is complete, the
        # chunks are real, and only the disk disagrees.
        print("\n  DANGLING — these sources claim a raw file that is not there:")
        for row in plan.dangling[:20]:
            print(f"    ! source {row.source_id}  {row.path}")
            print(f"      {row.url[:90]}")
        print("\n  These rows record no raw root, or record this one. A row with no")
        print("  root predates `P1-45` and cannot be told apart from a real loss;")
        print("  check the other stores by hand before concluding anything.")

    if plan.elsewhere:
        # Not a problem, and listed separately so it stops looking like one.
        roots = sorted({d.raw_root for d in plan.elsewhere if d.raw_root})
        print(f"\n  ELSEWHERE — {len(plan.elsewhere)} sources were written into another store:")
        for other in roots:
            count = sum(1 for d in plan.elsewhere if d.raw_root == other)
            print(f"    {other}  ({count})")
        print("  Nothing is missing. Sweep those roots separately, and note that")
        print("  `make snapshot-corpus` archives one root at a time.")

    if plan.is_empty:
        print("\n  Nothing to reclaim.")
        print("  Expected, and structural rather than lucky: `P1-11` never writes")
        print("  the files §5.4 says to drop, and `retention_for` only ever moves a")
        print("  tier up — so a file that exists was written under a tier that keeps")
        print("  files and cannot since have fallen below it.")


async def run_sweep(*, apply: bool = False, root: str | None = None) -> RetentionPlan:
    resolved = str(raw_root(root))
    async with session() as sess:
        plan = await plan_sweep(sess, resolved)

    render(plan, resolved)

    if apply and not plan.is_empty:
        reclaimed = apply_sweep(plan, resolved, dry_run=False)
        print(f"\n  APPLIED — {reclaimed / 1024:.1f} KiB reclaimed.")
        log.info(
            "retention sweep applied",
            extra={
                "raw_root": resolved,
                "bytes": reclaimed,
                "dropped": len(plan.droppable),
                "orphaned": len(plan.orphaned),
            },
        )
    elif not apply and not plan.is_empty:
        print("\n  Dry run. Pass --apply to delete.")

    await dispose_engines()
    return plan


def main() -> None:
    """Entry point: ``python -m worker.sweep``."""
    parser = argparse.ArgumentParser(
        description="Reconcile the raw store against §5.4's retention tiers.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete. Without it, nothing is removed — the default is a report, "
        "because a re-crawl returns today's web rather than the page that was fetched.",
    )
    parser.add_argument(
        "--raw-root",
        default=os.environ.get("MERIDIAN_RAW_ROOT"),
        help="the raw store to sweep. Defaults to MERIDIAN_RAW_ROOT.",
    )
    args = parser.parse_args()

    configure_logging("sweep")
    with bind_run_id(f"sweep-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_sweep(apply=args.apply, root=args.raw_root))


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
