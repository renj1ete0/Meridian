"""Apply the host verdict to links already queued (task `B-48`).

The fetch loop now asks `hostscores` before queueing a link, but the queue
already holds what it queued before it asked — on a real crawl, tens of
thousands of links into sites the corpus has since found to be off-topic. This
pass walks the pending link rows (frontier and sitemap only: a search result or
a person's seed was chosen, not followed) in the order they would be claimed,
admits each through the same policy the loop uses, and gives the ones it would
not have queued :data:`HELD_PRIORITY` — the bottom of the queue, where they wait
rather than vanish. Steering adjusts, never deletes (§2.5).

Report by default; ``--apply`` writes.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time
from urllib.parse import urlsplit

from sqlalchemy import select, update

from meridian_core.boilerplate import host_key
from meridian_core.db import dispose_engines, session
from meridian_core.hostscores import HostPolicy, Score, load
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask
from meridian_core.policy import source_tier_map
from meridian_core.tiering import resolve_tier

log = get_logger(__name__)

#: Where a link the policy would not have queued goes: claimed only when
#: nothing else is waiting.
HELD_PRIORITY = 0

#: Seed sources a person or a search chose. Left alone.
FOLLOWED = ("frontier", "sitemap")


@dataclasses.dataclass
class RequeueStats:
    examined: int = 0
    kept: int = 0
    held: int = 0
    downranked: int = 0
    reasons: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    held_hosts: collections.Counter = dataclasses.field(default_factory=collections.Counter)


async def run_pass(*, apply: bool, session_factory=session) -> RequeueStats:
    stats = RequeueStats()
    async with session_factory() as sess:
        scores = await load(sess)
        # Pending counts start from zero: this pass *is* the recount, in the
        # order the queue would be claimed, so each host keeps its best links.
        policy = HostPolicy({h: Score(s.examined, s.on_topic, 0) for h, s in scores.items()})
        tiers = await source_tier_map(sess)
        rows = await sess.execute(
            select(QueueTask.task_id, QueueTask.url_or_query, QueueTask.priority)
            .where(
                QueueTask.status == "pending",
                QueueTask.task_type == "url",
                QueueTask.seed_source.in_(FOLLOWED),
            )
            .order_by(QueueTask.priority.desc(), QueueTask.created_at.asc())
        )
        changes: dict[int, list[int]] = collections.defaultdict(list)
        for task_id, url, priority in rows:
            stats.examined += 1
            government = resolve_tier(urlsplit(url).hostname or "", tiers) == "government"
            decision = policy.admit(url, government=government)
            if not decision.queue:
                stats.held += 1
                stats.reasons[decision.reason] += 1
                stats.held_hosts[host_key(url) or "?"] += 1
                if priority != HELD_PRIORITY:
                    changes[HELD_PRIORITY].append(task_id)
            elif decision.priority is not None and decision.priority != priority:
                stats.downranked += 1
                stats.reasons[decision.reason] += 1
                changes[decision.priority].append(task_id)
            else:
                stats.kept += 1
        if apply:
            for new_priority, ids in changes.items():
                for start in range(0, len(ids), 5000):
                    await sess.execute(
                        update(QueueTask)
                        .where(QueueTask.task_id.in_(ids[start : start + 5000]))
                        .values(priority=new_priority)
                    )
            await sess.commit()
    log.info(
        "requeue pass complete",
        extra={"examined": stats.examined, "held": stats.held, "applied": apply},
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the host verdict to queued links (B-48).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("requeue")

    async def go() -> RequeueStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"requeue-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    print(
        f"examined {stats.examined}  kept {stats.kept}  "
        f"held {stats.held}  downranked {stats.downranked}"
    )
    for reason, n in stats.reasons.most_common():
        print(f"  {reason:22} {n}")
    print("  most held hosts:")
    for host, n in stats.held_hosts.most_common(15):
        print(f"    {n:6}  {host}")
    if not args.apply:
        print(
            "\nReport only. --apply moves held links to the bottom of the queue; "
            "nothing is deleted."
        )


if __name__ == "__main__":
    main()
