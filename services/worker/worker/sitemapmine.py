"""Mine proven hosts through their sitemaps (task `B-116`).

Search finds sites; a site that has proven itself is then cheaper to read from
its own list of pages than to rediscover one search result at a time. A sitemap
is that list: the site's statement of every URL it has, in one request. `P1-28`
built everything that reads one — the parser, its XML-bomb defences, the
same-site rule, the claim handler that turns entries into queue rows with the
topic their path implies — and nothing ever queued a sitemap, so none was read.

This pass is the missing trigger, and only for proven hosts (on a topic at or
above `FULL_SHARE`). Every host would be a breadth-first crawl of the web's
largest sites; a proven host is one whose next page was, measured, on a topic
about half the time. The entries then go through the same host policy as a
followed link — capped per host, boosted as proven — so a sitemap of fifty
thousand URLs queues at most the host's cap, topic-matched paths first and the
rest at the bottom.

**Filed under the host's commonest topic.** Every claim draws a topic and
takes only tasks filed under it, so a sitemap queued with none is claimable
only by the last-resort fallback, which never runs while any topic has work:
the first deployment queued a hundred and fetched none. The host's commonest
label is the topic its pages were proven on; a sitemap left pending with no
topic is given one on the next pass.

The sitemap URLs come from the robots.txt already cached for the crawl, so the
pass itself makes no request; a host that advertises none gets the conventional
``/sitemap.xml``, which costs one fetch to find out. A sitemap is queued once —
`already_queued` sees any status — so the pass is idempotent and cheap to run
on the timetable.

Report by default; ``--apply`` writes.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time

from sqlalchemy import or_, select, update

from meridian_core.ageing import HALF_LIFE_DAYS
from meridian_core.boilerplate import host_key
from meridian_core.db import dispose_engines, session
from meridian_core.hostscores import FULL_SHARE, PROVEN_BOOST, Score, Standing, load
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import QueueTask, Source
from meridian_core.models.robots import RobotsCacheEntry
from meridian_core.policy import resolve_policy, source_tier_map
from meridian_core.queueing import already_queued, enqueue
from meridian_core.tiering import priority_with_urgency

from .robots import parse

log = get_logger(__name__)

#: Where a host with no advertised sitemap is asked. The sitemaps.org default.
CONVENTIONAL = "/sitemap.xml"


def proven(score: Score) -> bool:
    """On a topic at the share that earns the boost — the same line as `B-115`."""
    return score.standing is Standing.ON_TOPIC and score.share >= FULL_SHARE


@dataclasses.dataclass
class MineStats:
    proven: int = 0
    queued: list[str] = dataclasses.field(default_factory=list)
    conventional: int = 0
    already: int = 0
    refiled: int = 0


def sitemaps_for(host: str, body: str | None, user_agent: str) -> list[str]:
    """The same-host sitemaps a robots.txt names, or the conventional one.

    Same host only: the claim handler refuses a sitemap naming another site,
    and one naming another host on the way in would be a request spent to be
    refused.
    """
    named = [
        url for url in (parse(body, user_agent).sitemaps if body else ()) if host_key(url) == host
    ]
    return named or [f"https://{host}{CONVENTIONAL}"]


#: How many of a host's labelled sources to read for its commonest topic.
TOPIC_SAMPLE = 500


async def commonest_topic(sess, host: str) -> str | None:
    """The label most of the host's on-topic pages carry — what it was proven on."""
    prefixes = [f"{scheme}://{www}{host}/" for scheme in ("https", "http") for www in ("", "www.")]
    rows = await sess.scalars(
        select(Source.topic_labels)
        .where(
            or_(*[Source.url.startswith(p) for p in prefixes]),
            Source.topic_labels.is_not(None),
        )
        .limit(TOPIC_SAMPLE)
    )
    counts = collections.Counter(t for labels in rows for t in labels or ())
    return counts.most_common(1)[0][0] if counts else None


async def run_pass(*, apply: bool, session_factory=session) -> MineStats:
    stats = MineStats()
    async with session_factory() as sess:
        hosts = sorted(h for h, s in (await load(sess)).items() if proven(s))
        stats.proven = len(hosts)
        tiers = await source_tier_map(sess)
        for host in hosts:
            origins = [f"https://{host}/robots.txt", f"https://www.{host}/robots.txt"]
            body = await sess.scalar(
                select(RobotsCacheEntry.body).where(
                    RobotsCacheEntry.origin.in_(origins), RobotsCacheEntry.outcome == "ok"
                )
            )
            policy = await resolve_policy(sess, host)
            urls = sitemaps_for(host, body, policy.user_agent)
            if urls[0].endswith(CONVENTIONAL) and len(urls) == 1:
                stats.conventional += 1
            topic = await commonest_topic(sess, host)
            known = await already_queued(sess, urls)
            stats.already += len(known)
            if apply and known and topic is not None:
                refiled = await sess.execute(
                    update(QueueTask)
                    .where(
                        QueueTask.url_or_query.in_(sorted(known)),
                        QueueTask.task_type == "sitemap",
                        QueueTask.status == "pending",
                        QueueTask.topic.is_(None),
                    )
                    .values(topic=topic)
                )
                stats.refiled += refiled.rowcount or 0
            for url in urls:
                if url in known:
                    continue
                stats.queued.append(url)
                if apply:
                    # Fetched promptly: an index or a sitemap is what reveals
                    # the pages, and the host has already earned its boost.
                    await enqueue(
                        sess,
                        url,
                        topic=topic,
                        seed_source="sitemap",
                        task_type="sitemap",
                        priority=priority_with_urgency(url, tiers, HALF_LIFE_DAYS) + PROVEN_BOOST,
                    )
        if apply:
            await sess.commit()
    log.info(
        "sitemap mining pass complete",
        extra={"proven": stats.proven, "queued": len(stats.queued), "applied": apply},
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue proven hosts' sitemaps (B-116).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("sitemapmine")

    async def go() -> MineStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"sitemapmine-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    print(
        f"proven hosts {stats.proven}  sitemaps queued {len(stats.queued)}  "
        f"already queued {stats.already}  refiled {stats.refiled}  "
        f"conventional guesses {stats.conventional}"
    )
    for url in stats.queued[:40]:
        print(f"  {url}")
    if not args.apply:
        print("\nReport only. --apply queues them.")


if __name__ == "__main__":
    main()
