"""Followed links wait behind their page's verdict (task `B-92`).

``python -m worker.requeue_links`` moves the pending links of a page found about
none of the topics (``queue.parent_source_id``, `B-91`) down to
:data:`DEMOTED_PRIORITY`. Only down, never deleted, idempotent; reports by default,
writes only with ``--apply``. See docs/features/discovery.md#re-ranking.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import dispose_engines, session
from meridian_core.hostscores import DOWNRANKED_PRIORITY
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, Source

log = get_logger(__name__)

#: Where a link from a page about none of the topics waits: the host gate's floor.
DEMOTED_PRIORITY = DOWNRANKED_PRIORITY


def _demotable():
    """Pending followed links above the floor whose page was read and is about nothing."""
    parent_off_topic = (
        select(Source.source_id)
        .where(Source.source_id == QueueTask.parent_source_id, Source.topic_labels == [])
        .exists()
    )
    return and_(
        QueueTask.status == "pending",
        QueueTask.task_type == "url",
        QueueTask.seed_source == "frontier",
        QueueTask.priority > DEMOTED_PRIORITY,
        parent_off_topic,
    )


async def count_demotable(sess: AsyncSession) -> int:
    return int(
        await sess.scalar(select(func.count()).select_from(QueueTask).where(_demotable())) or 0
    )


async def demote(sess: AsyncSession) -> int:
    """Move them to the floor. Flushes; the caller commits. Returns how many moved."""
    result = await sess.execute(
        update(QueueTask)
        .where(_demotable())
        .values(priority=DEMOTED_PRIORITY)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def run_pass(*, apply: bool) -> int:
    async with session() as sess:
        if not apply:
            return await count_demotable(sess)
        moved = await demote(sess)
        await sess.commit()
        return moved


def main() -> None:
    """Entry point: ``python -m worker.requeue_links``."""
    parser = argparse.ArgumentParser(
        description="Demote pending links carried by pages about none of the topics (B-92)."
    )
    parser.add_argument("--apply", action="store_true", help="write; without it, only report")
    args = parser.parse_args()
    configure_logging("requeue_links")

    async def go() -> int:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"requeue-links-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        n = asyncio.run(go())
    verb = "demoted" if args.apply else "would be demoted"
    print(
        f"{n} pending followed links from pages about none of the topics {verb} "
        f"to priority {DEMOTED_PRIORITY}."
    )
    log.info("followed links requeued", extra={"demoted": n, "applied": args.apply})


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
