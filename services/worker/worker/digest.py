"""The digest and the alert pass (task P5-07, spec §13.3, §12.5).

`python -m worker.digest` — run it on a timer.

Two jobs that share one channel. The **digest** is §12.5's health line, sent
rather than logged: queue depth, fetch success, novelty pass rate. The **alerts**
are §13.3's sustained conditions, and they are the half that matters when nobody
is looking.

**Findings are recorded before they are sent.** An alert is written to
`notifications` and only then handed to Telegram, so a send that fails loses the
delivery and not the evidence — and the in-app panel (`P6-08`) reads the same
rows. A deployment with no bot token is not a deployment with no monitoring.

**The digest is not an alert and does not repeat as one.** It is sent on every
run because "here is how it went" is worth a daily line even when it went fine;
the alerts underneath it are suppressed by cooldown, so a condition lasting all
night produces one message rather than a column of them.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import time

from meridian_core.alerts import DEFAULT_COOLDOWN_HOURS, due_alerts, record_alert
from meridian_core.attempts import SUCCESS_OUTCOMES, fetch_health
from meridian_core.db import dispose_engines, session, session_ro
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.novelty import novelty_health
from meridian_core.queueing import queue_depth

from .rawstore import raw_root
from .telegram import Telegram

log = get_logger(__name__)


async def build_digest() -> str:
    """§12.5's health line, as something a person reads on a phone.

    Every field is read directly rather than through `getattr(..., default)`.
    That is not style: a defaulted lookup in a monitoring tool turns a renamed
    attribute into a permanent zero, so the thing whose job is reporting
    problems reports none. This module got that wrong once — `fetch.total` and
    `novelty.duplicate_rate` are neither of them real, and the digest cheerfully
    said "nothing attempted, 0% duplicate" on a corpus that had both.
    """
    async with session_ro() as sess:
        depth = await queue_depth(sess)
        fetch = await fetch_health(sess, hours=24)
        novelty = await novelty_health(sess)

    lines = ["Meridian — 24h", ""]

    # Per status, not a single number: 4,000 pending and 4,000 failed are the
    # same depth and opposite situations.
    lines.append(f"queue: {depth.get('pending', 0)} pending")
    rest = ", ".join(f"{k} {v}" for k, v in sorted(depth.items()) if k != "pending" and v)
    if rest:
        lines.append(f"       {rest}")

    if fetch.attempts:
        rate = f"{fetch.success_rate:.0%}" if fetch.success_rate is not None else "unknown"
        lines.append(f"fetch: {fetch.attempts} attempts, {rate} ok")
        worst = sorted(
            (
                (count, outcome)
                for outcome, count in fetch.by_outcome.items()
                if outcome not in SUCCESS_OUTCOMES
            ),
            reverse=True,
        )[:3]
        if worst:
            # The breakdown, because §12.5's point is that a rate says something
            # is wrong and never what.
            lines.append("       " + ", ".join(f"{outcome} {count}" for count, outcome in worst))
    else:
        # Not "0% success". Nothing was attempted, which is a different fact —
        # and the one `check_no_recent_success` is about to alert on.
        lines.append("fetch: nothing attempted")

    if novelty.judged:
        duplicate = novelty.duplicates / novelty.judged
        lines.append(
            f"novelty: {novelty.judged} judged, {duplicate:.0%} duplicate"
            + (f", {novelty.pending} waiting" if novelty.pending else "")
        )
    else:
        lines.append("novelty: nothing judged yet")

    return "\n".join(lines)


async def run_digest(*, send: bool = True, cooldown_hours: int = DEFAULT_COOLDOWN_HOURS) -> int:
    """Build the digest, raise what is due, and send. Returns the alert count."""
    digest = await build_digest()

    # Recorded first, in its own transaction. If Telegram is unreachable the
    # findings survive; if the alert write failed there would be nothing to say.
    async with session() as sess:
        alerts = await due_alerts(sess, raw_root=str(raw_root()), cooldown_hours=cooldown_hours)
        for alert in alerts:
            await record_alert(sess, alert)

    body = digest
    if alerts:
        body += "\n\n" + "\n\n".join(f"⚠ {a.title}\n{a.body}" for a in alerts)

    print(body)

    if send:
        bot = Telegram.from_env()
        if bot is None:
            # Said once, at info. A deployment that shares nothing is a choice,
            # not a fault, and warning about it every run is the same mistake as
            # single-event alerting.
            log.info("no telegram configured; digest recorded and printed only")
        else:
            delivered = await bot.send(body)
            await bot.aclose()
            log.info("digest sent" if delivered else "digest not delivered")

    await dispose_engines()
    return len(alerts)


def main() -> None:
    """Entry point: ``python -m worker.digest``."""
    parser = argparse.ArgumentParser(description="Send §12.5's health line and §13.3's alerts.")
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="build and record, but do not deliver. For checking what would go out.",
    )
    parser.add_argument(
        "--cooldown-hours",
        type=int,
        default=int(os.environ.get("MERIDIAN_ALERT_COOLDOWN_H", DEFAULT_COOLDOWN_HOURS)),
        help="how long an alert stays quiet after firing",
    )
    args = parser.parse_args()

    configure_logging("digest")
    with bind_run_id(f"digest-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_digest(send=not args.no_send, cooldown_hours=args.cooldown_hours))


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
