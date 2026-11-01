"""Followed links follow their page's verdict (tasks `B-92`, `B-150`).

``python -m worker.requeue_links`` moves the pending links of a page found about none of
the topics (``queue.parent_source_id``, `B-91`) down to :data:`DEMOTED_PRIORITY`, then
raises those of a page found on a topic to :data:`PROMOTED_PRIORITY` and those into a
vouched-for host to :data:`VOUCHED_PRIORITY`, and lets go of links still waiting for a
verdict their page now has (`B-242`). Never deleted, idempotent; reports by default, writes
only with ``--apply``. See docs/features/discovery.md#re-ranking.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import time

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.boilerplate import host_key
from meridian_core.db import dispose_engines, session
from meridian_core.hostscores import (
    DOWNRANKED_PRIORITY,
    PROVEN_BOOST,
    VOUCHED_BOOST,
    HostPolicy,
    Standing,
    load,
)
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, Source
from meridian_core.queueing import held

log = get_logger(__name__)

#: Where a link from a page about none of the topics waits: the host gate's floor.
DEMOTED_PRIORITY = DOWNRANKED_PRIORITY

#: Where a link from a page on a topic is raised to: the bottom of the proven band (a
#: proven host's lowest tier plus its boost). Measured, such a link lands on a topic about
#: as often as a proven host's next page. See docs/features/discovery.md#vouched-hosts.
PROMOTED_PRIORITY = PROVEN_BOOST + 5

#: Where a pending link into a vouched-for, unjudged host is raised to: the lowest a newly
#: queued one gets (`VOUCHED_BOOST` over the lowest tier).
VOUCHED_PRIORITY = VOUCHED_BOOST + 5

#: Rows updated per statement.
BATCH = 1000


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


def _held_by_host(policy: HostPolicy, url: str) -> bool:
    """Whether the host gate would hold this link down, so no page's verdict lifts it."""
    host = host_key(url)
    score = policy.score(host)
    if score.standing is Standing.OFF_TOPIC:
        return True
    if score.standing is Standing.UNKNOWN and not score.is_vouched:
        return policy.site_score(host).standing is Standing.OFF_TOPIC
    return False


async def promotable(sess: AsyncSession, policy: HostPolicy) -> tuple[list[int], list[int]]:
    """Ids to raise: links from on-topic pages, and links into vouched-for hosts.

    A link the host gate holds down stays there: a host's own record outweighs one page.
    """
    parent_on_topic = (
        select(Source.source_id)
        .where(
            Source.source_id == QueueTask.parent_source_id,
            Source.topic_labels.is_not(None),
            Source.topic_labels != [],
        )
        .exists()
    )
    rows = await sess.execute(
        select(QueueTask.task_id, QueueTask.url_or_query, QueueTask.priority, parent_on_topic)
        .where(
            QueueTask.status == "pending",
            QueueTask.task_type == "url",
            QueueTask.seed_source.in_(("frontier", "sitemap")),
            QueueTask.priority < PROMOTED_PRIORITY,
        )
        .order_by(QueueTask.task_id)
    )
    children: list[int] = []
    vouched: list[int] = []
    for task_id, url, priority, from_on_topic in rows:
        if _held_by_host(policy, url):
            continue
        if from_on_topic:
            children.append(task_id)
        elif priority < VOUCHED_PRIORITY and policy.score(host_key(url)).is_vouched:
            vouched.append(task_id)
    return children, vouched


async def _raise(sess: AsyncSession, ids: list[int], priority: int) -> int:
    moved = 0
    for start in range(0, len(ids), BATCH):
        result = await sess.execute(
            update(QueueTask)
            .where(QueueTask.task_id.in_(ids[start : start + BATCH]), QueueTask.priority < priority)
            .values(priority=priority)
            .execution_options(synchronize_session=False)
        )
        moved += int(result.rowcount or 0)
    return moved


def _releasable(now: dt.datetime):
    """Links waiting for a verdict (`B-242`) whose page has now been read, either way."""
    parent_read = (
        select(Source.source_id)
        .where(Source.source_id == QueueTask.parent_source_id, Source.topic_labels.is_not(None))
        .exists()
    )
    return and_(QueueTask.status == "pending", held(now), parent_read)


async def count_releasable(sess: AsyncSession, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC)
    return int(
        await sess.scalar(select(func.count()).select_from(QueueTask).where(_releasable(now))) or 0
    )


async def release_judged(sess: AsyncSession, now: dt.datetime | None = None) -> int:
    """End the wait of links whose page has its verdict. Flushes; returns how many.

    Run after the re-ranking, so a released link is claimable at the rank its page earned.
    A retry's backoff is never touched: :func:`~meridian_core.queueing.held` is only rows
    never attempted.
    """
    now = now or dt.datetime.now(dt.UTC)
    result = await sess.execute(
        update(QueueTask)
        .where(_releasable(now))
        .values(next_attempt_at=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


@dataclasses.dataclass(frozen=True)
class Moved:
    demoted: int
    promoted: int
    vouched: int
    released: int = 0


async def run_pass(*, apply: bool) -> Moved:
    async with session() as sess:
        if not apply:
            demoted = await count_demotable(sess)
        else:
            demoted = await demote(sess)
        children, vouched = await promotable(sess, HostPolicy(await load(sess)))
        if not apply:
            return Moved(demoted, len(children), len(vouched), await count_releasable(sess))
        promoted = await _raise(sess, children, PROMOTED_PRIORITY)
        raised = await _raise(sess, vouched, VOUCHED_PRIORITY)
        moved = Moved(demoted, promoted, raised, await release_judged(sess))
        await sess.commit()
        return moved


def main() -> None:
    """Entry point: ``python -m worker.requeue_links``."""
    parser = argparse.ArgumentParser(
        description="Re-rank pending followed links by their page's verdict (B-92, B-150)."
    )
    parser.add_argument("--apply", action="store_true", help="write; without it, only report")
    args = parser.parse_args()
    configure_logging("requeue_links")

    async def go() -> Moved:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"requeue-links-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        n = asyncio.run(go())
    done = "" if args.apply else "would be "
    print(
        f"{n.demoted} pending followed links from pages about none of the topics {done}"
        f"moved down to priority {DEMOTED_PRIORITY}; {n.promoted} from pages on a topic "
        f"{done}raised to {PROMOTED_PRIORITY}; {n.vouched} into vouched-for hosts {done}"
        f"raised to {VOUCHED_PRIORITY}; {n.released} waiting for their page's verdict "
        f"{done}let go."
    )
    log.info(
        "followed links requeued",
        extra={
            "demoted": n.demoted,
            "promoted": n.promoted,
            "vouched": n.vouched,
            "released": n.released,
            "applied": args.apply,
        },
    )


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
