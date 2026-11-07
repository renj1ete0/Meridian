"""The monthly graph audit draw (task `P7-10`, spec §14.1).

`python -m worker.graphaudit`, on the timetable. Draws a reproducible sample of the relations,
merges and matches at resolution made since the last draw, records it for a person to grade in
Admin › Graph audit, and says so in the bell (and on Telegram, when configured). It samples and
measures; it never changes or deletes anything in the graph. See
docs/features/knowledge-graph.md#the-graph-audit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import time

from meridian_core import graphaudit
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Notification

from .telegram import Telegram

log = get_logger(__name__)

#: Where the bell's row leads; `NotificationsPanel` reads `payload.job`.
JOB = "graph-audit"


async def run_draw(
    *,
    edges: int = graphaudit.EDGES_PER_DRAW,
    merges: int = graphaudit.MERGES_PER_DRAW,
    matches: int = graphaudit.MATCHES_PER_DRAW,
    seed: str | None = None,
    send: bool = True,
    now: dt.datetime | None = None,
) -> int | None:
    """Draw, record a notification, and send it. The draw's id, or None when nothing was drawn.

    A refusal (too soon after the last draw) is logged and is not a failure: the scheduler
    would otherwise back the job off for doing what it should.
    """
    now = now or dt.datetime.now(dt.UTC)
    async with session() as sess:
        try:
            row = await graphaudit.draw(
                sess, now=now, edges=edges, merges=merges, matches=matches, seed=seed
            )
        except graphaudit.AuditRefused as refused:
            log.info("graph audit not drawn", extra={"reason": str(refused)})
            await dispose_engines()
            return None
        if row is None:
            await dispose_engines()
            return None
        current = await graphaudit.draw_detail(sess, row.draw_id)
        earlier = [
            d
            for d in (await graphaudit.report(sess, limit=6)).draws
            if d.draw_id != row.draw_id and (d.edges.graded or d.merges.graded or d.matches.graded)
        ]
        text = graphaudit.summary(current.draw, earlier[0] if earlier else None)
        sess.add(
            Notification(
                notification_type="job_complete",
                title=text.splitlines()[0],
                body=text,
                payload={"job": JOB, "draw_id": row.draw_id},
                surface="admin",
            )
        )
        await sess.commit()
        draw_id = row.draw_id

    print(text)
    if send:
        bot = Telegram.from_env()
        if bot is not None:
            delivered = await bot.send(text)
            await bot.aclose()
            log.info("graph audit sent" if delivered else "graph audit not delivered")
    await dispose_engines()
    return draw_id


def main() -> None:
    """Entry point: ``python -m worker.graphaudit``."""
    parser = argparse.ArgumentParser(
        description="Sample new relations and merges for a person to grade (§14.1)."
    )
    parser.add_argument("--edges", type=int, default=graphaudit.EDGES_PER_DRAW)
    parser.add_argument("--merges", type=int, default=graphaudit.MERGES_PER_DRAW)
    parser.add_argument("--matches", type=int, default=graphaudit.MATCHES_PER_DRAW)
    parser.add_argument(
        "--seed", help="order the sample by this seed instead of a fresh random one"
    )
    parser.add_argument("--no-send", action="store_true", help="record, but do not deliver")
    args = parser.parse_args()

    configure_logging("graphaudit")
    with bind_run_id(f"graphaudit-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_draw(
                edges=args.edges,
                merges=args.merges,
                matches=args.matches,
                seed=args.seed,
                send=not args.no_send,
            )
        )


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
