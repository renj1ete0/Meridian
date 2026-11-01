"""Audit the attribute schema and judge its proposals (tasks `P7-01`, `P7-02`, §7.3).

``python -m worker.attributes --once`` audits every active attribute not audited in the last
four weeks, retires one that has failed enough consecutive audits, and runs the gate over every
proposed attribute. ``--report`` does the same inside a transaction it rolls back. ``--restore
NAME`` brings a retired attribute back with its values; ``--retire NAME`` retires one by hand.

Daily on the timetable; each attribute's audit is monthly. Counting only — no model is called
here (§2.1); the rules live in :mod:`meridian_core.attributeschema`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import sys
import time

from meridian_core.attributeschema import Verdict, restore, retire, run_pass
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.validation import ValidationError

log = get_logger(__name__)


async def run_once(*, write: bool, now: dt.datetime | None = None) -> list[Verdict]:
    """One pass: audits that are due, then every proposal. Written only when `write`."""
    moment = now or dt.datetime.now(dt.UTC)
    async with session() as sess:
        verdicts = await run_pass(sess, now=moment)
        if write:
            await sess.commit()
        else:
            await sess.rollback()
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.kind] = counts.get(verdict.kind, 0) + 1
    log.info("attribute pass", extra={"verdicts": counts, "written": write})
    return verdicts


async def change(name: str, *, restoring: bool, now: dt.datetime | None = None) -> Verdict:
    """Restore or retire one attribute by hand, and commit."""
    moment = now or dt.datetime.now(dt.UTC)
    async with session() as sess:
        if restoring:
            verdict = await restore(sess, name, now=moment)
        else:
            verdict = await retire(sess, name, now=moment)
        await sess.commit()
    log.info("attribute changed by hand", extra={"attribute": name, "kind": verdict.kind})
    return verdict


def render(verdicts: list[Verdict]) -> str:
    """One line per attribute the pass touched."""
    if not verdicts:
        return "nothing due: no attribute to audit and no proposal waiting"
    return "\n".join(f"{v.name}: {v.kind} — {v.reason}" for v in verdicts)


def main() -> None:
    parser = argparse.ArgumentParser(description="The attribute schema's gate and audit.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="audit what is due, judge, write")
    mode.add_argument("--report", action="store_true", help="the same, rolled back; print it")
    mode.add_argument("--restore", metavar="NAME", help="bring a retired attribute back")
    mode.add_argument("--retire", metavar="NAME", help="retire an attribute by hand")
    args = parser.parse_args()
    configure_logging("attributes")

    async def go() -> str:
        try:
            if args.restore or args.retire:
                verdict = await change(args.restore or args.retire, restoring=bool(args.restore))
                return render([verdict])
            return render(await run_once(write=args.once))
        finally:
            await dispose_engines()

    with bind_run_id(f"attributes-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        try:
            print(asyncio.run(go()))
        except ValidationError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
