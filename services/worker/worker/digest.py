"""The digest, the runs report and the alert pass (task P5-07, spec §13.3, §12.5).

`python -m worker.digest`, run on a timer. The digest is §12.5's health line, sent every
run; the alerts are §13.3's sustained conditions, suppressed by cooldown. `--runs` is the
synthesis report (`B-262`): what the runs finished since the last report wrote, and why the
rest waited. Findings are recorded in `notifications` before they are sent.
See docs/features/operations.md#alerts-and-the-digest.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import datetime as dt
import os
import time

from sqlalchemy import func, select

from meridian_core.alerts import DEFAULT_COOLDOWN_HOURS, due_alerts, record_alert
from meridian_core.attempts import SUCCESS_OUTCOMES, fetch_health
from meridian_core.db import dispose_engines, session, session_ro
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Notification, Run, Source
from meridian_core.novelty import novelty_health
from meridian_core.queueing import queue_depth

from .rawstore import raw_root
from .telegram import Telegram

log = get_logger(__name__)


async def build_digest() -> str:
    """§12.5's health line, as something a person reads on a phone.

    Every field is read directly, never through `getattr(..., default)`.
    See docs/features/operations.md#alerts-and-the-digest.
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

    lines += await _corpus_lines()
    return "\n".join(lines)


async def _corpus_lines(now: dt.datetime | None = None) -> list[str]:
    """The day's corpus growth, embedding backlog and synthesis output (`B-262`).

    The three questions the morning message is read for.
    """
    now = now or dt.datetime.now(dt.UTC)
    day = now - dt.timedelta(hours=24)
    async with session_ro() as sess:
        sources = await sess.scalar(select(func.count()).where(Source.created_at > day))
        passages = await sess.scalar(
            select(func.count()).where(Chunk.created_at > day, Chunk.superseded_at.is_(None))
        )
        waiting = await sess.scalar(
            select(func.count()).where(Chunk.embedding.is_(None), Chunk.superseded_at.is_(None))
        )
        runs = (
            await sess.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(Run.edges_added), 0),
                    func.coalesce(func.sum(Run.tags_added), 0),
                    func.coalesce(func.sum(Run.tokens_used), 0),
                ).where(Run.completed_at > day, Run.status == "done")
            )
        ).one()
    lines = [f"corpus: +{sources or 0} sources, +{passages or 0} passages"]
    # The GPU machine may sleep; a backlog that grows for a day is the sign it has not woken.
    lines.append(
        f"embedding: {waiting or 0} passages waiting" if waiting else "embedding: none waiting"
    )
    count, edges, tags, tokens = runs
    lines.append(
        f"synthesis: {count} runs, {edges} relations, {tags} attributes, {tokens:,} tokens"
        if count
        else "synthesis: no run finished"
    )
    return lines


#: The first runs report looks back this far; each later one starts where the last stopped.
FIRST_REPORT_HOURS = 24
#: Failed runs named with their reason, at most this many, so a phone shows them whole.
REASONS_SHOWN = 3


async def build_runs_report(
    sess, *, since: dt.datetime, now: dt.datetime
) -> tuple[str | None, dict]:
    """The synthesis report for runs that ended after `since` (`B-262`), and its payload.

    None when there is nothing to say: no run ended and none waits. The waiting run (one
    at most: a deferred run is resumed, never joined by another) is said with its reason,
    since "the GPU machine is off" is the commonest answer to "why did nothing happen".
    """
    ended = list(
        await sess.scalars(
            select(Run)
            .where(Run.completed_at > since, Run.completed_at <= now)
            .order_by(Run.completed_at)
        )
    )
    waiting = await sess.scalar(select(Run).where(Run.status == "deferred"))
    payload = {"since": since.isoformat(), "through": now.isoformat(), "runs": len(ended)}
    if not ended and waiting is None:
        return None, payload

    done = [r for r in ended if r.status == "done"]
    failed = [r for r in ended if r.status == "failed"]
    edges = sum(r.edges_added for r in done)
    tags = sum(r.tags_added for r in done)
    seeds = sum(r.seeds_emitted for r in done)
    tokens = sum(r.tokens_used for r in ended)
    agents = collections.Counter(r.agent_id or "unknown" for r in done)
    payload |= {"done": len(done), "failed": len(failed), "edges": edges, "tags": tags}

    hours = max(1, round((now - since).total_seconds() / 3600))
    lines = [f"Meridian synthesis — last {hours}h", ""]
    lines.append(f"runs: {len(done)} done" + (f", {len(failed)} failed" if failed else ""))
    if done:
        lines.append(f"wrote: {edges} relations, {tags} attributes, {seeds} seeds")
        lines.append(f"tokens: {tokens:,}")
        lines.append("by: " + ", ".join(f"{a} ×{n}" for a, n in agents.most_common()))
    for r in failed[:REASONS_SHOWN]:
        lines.append(
            f"✗ run {r.run_id} at {r.stage or '?'}: {(r.error or 'no reason recorded')[:160]}"
        )
    if waiting is not None:
        reason = (waiting.error or "no reason recorded")[:160]
        lines.append(f"waiting: run {waiting.run_id} at {waiting.stage or '?'}: {reason}")
    return "\n".join(lines), payload


async def run_runs_report(*, send: bool = True, now: dt.datetime | None = None) -> bool:
    """Report the runs since the last report, record it, and send it. Whether one was made."""
    now = now or dt.datetime.now(dt.UTC)
    async with session() as sess:
        last = await sess.scalar(
            select(func.max(Notification.created_at)).where(
                Notification.notification_type == "run_summary"
            )
        )
        since = last or now - dt.timedelta(hours=FIRST_REPORT_HOURS)
        text, payload = await build_runs_report(sess, since=since, now=now)
        if text is None:
            log.info("runs report: nothing to say", extra=payload)
            await dispose_engines()
            return False
        sess.add(
            Notification(
                notification_type="run_summary",
                title=text.splitlines()[0],
                body=text,
                payload=payload,
                surface="admin",
            )
        )
        await sess.commit()

    print(text)
    if send:
        bot = Telegram.from_env()
        if bot is not None:
            delivered = await bot.send(text)
            await bot.aclose()
            log.info("runs report sent" if delivered else "runs report not delivered")
    await dispose_engines()
    return True


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
        "--runs",
        action="store_true",
        help="the synthesis report since the last one, instead of the daily digest",
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
        if args.runs:
            asyncio.run(run_runs_report(send=not args.no_send))
        else:
            asyncio.run(run_digest(send=not args.no_send, cooldown_hours=args.cooldown_hours))


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
