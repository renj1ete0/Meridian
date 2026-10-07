"""Re-tier sources whose scholarly tier came only from their domain (task `B-50`).

A `peer_reviewed` source on a domain listed under `needs_scholarly_evidence`, with no
DOI of its own, becomes `institutional`. Tier only: retention, edges and claims are
untouched. Report by default; ``--apply`` writes.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time

from sqlalchemy import select, update

from meridian_core.boilerplate import host_key
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Source
from meridian_core.policy import source_tier_map
from meridian_core.tiering import WITHOUT_EVIDENCE, needs_evidence

log = get_logger(__name__)


@dataclasses.dataclass
class RetierStats:
    examined: int = 0
    retiered: int = 0
    kept_with_doi: int = 0
    by_host: collections.Counter = dataclasses.field(default_factory=collections.Counter)


async def run_pass(*, apply: bool, session_factory=session) -> RetierStats:
    stats = RetierStats()
    async with session_factory() as sess:
        tiers = await source_tier_map(sess)
        rows = await sess.execute(
            select(
                Source.source_id, Source.url, Source.extra["final_url"].astext, Source.doi
            ).where(Source.source_tier == "peer_reviewed")
        )
        ids: list[int] = []
        for source_id, url, final_url, doi in rows:
            stats.examined += 1
            host = host_key(final_url or url) or ""
            if not needs_evidence(host, tiers):
                continue
            if doi:
                stats.kept_with_doi += 1
                continue
            ids.append(source_id)
            stats.by_host[host] += 1
        stats.retiered = len(ids)
        if apply and ids:
            for start in range(0, len(ids), 5000):
                await sess.execute(
                    update(Source)
                    .where(Source.source_id.in_(ids[start : start + 5000]))
                    .values(source_tier=WITHOUT_EVIDENCE)
                )
            await sess.commit()
    log.info("retier pass complete", extra={"retiered": stats.retiered, "applied": apply})
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-tier domain-only scholarly sources (B-50).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    args = parser.parse_args()
    configure_logging("retier")

    async def go() -> RetierStats:
        try:
            return await run_pass(apply=args.apply)
        finally:
            await dispose_engines()

    with bind_run_id(f"retier-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    verb = "re-tiered" if args.apply else "would re-tier"
    print(
        f"peer_reviewed examined {stats.examined}; {verb} {stats.retiered} to "
        f"{WITHOUT_EVIDENCE}; kept on their own DOI {stats.kept_with_doi}"
    )
    for host, n in stats.by_host.most_common(15):
        print(f"  {n:6}  {host}")


if __name__ == "__main__":
    main()
