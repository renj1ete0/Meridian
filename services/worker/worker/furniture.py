"""Demoting site furniture already in the corpus (task `B-42`).

``python -m worker.furniture`` — reports by default, writes only with ``--apply``.

`B-23` and `B-42` stop the frontier queueing pages that are about a website
rather than about anything: privacy policies, terms of use, contact pages,
accessibility statements, a search engine's result pages. Pages crawled before
the rule was wide enough are still in the corpus, and they cost twice: they
answer searches and they take whole synthesis batches with nothing to extract.

**Demoted, not deleted.** Each matching source moves to the `junk` retention
tier, which search, the corpus map and synthesis already leave out and which a
later retention sweep is the one thing allowed to drop. A misjudged page is one
`UPDATE` away from coming back, and its raw file and chunks are untouched until
then.

**Evidence outranks the address.** The rule reads a page's URL, never its
text, so an article with a "privacy policy" link in its footer is untouched.
The opposite case — real content published at a furniture-shaped address — is
why a source any graph edge or entity cites is never demoted: once a passage is
evidence for something, how its URL looks no longer decides anything.

**Judged by the same function the frontier uses** (`prefilter.is_site_furniture`),
on the URL the source was stored under and on the one it was served from, so a
page cannot be furniture at the door and a document once inside.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import time

from sqlalchemy import select

from meridian_core import chunks
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Source

from .extract.errorpage import title_says_missing
from .prefilter import is_site_furniture

log = get_logger(__name__)

JUNK = "junk"


@dataclasses.dataclass
class FurnitureStats:
    examined: int = 0
    furniture: int = 0
    kept_as_evidence: int = 0
    examples: list[str] = dataclasses.field(default_factory=list)


def is_furniture_source(source: Source) -> bool:
    """About the website itself rather than its subject.

    Its furniture by URL (`B-42`), or a page whose title says it is not there (`B-45`).
    """
    final = (source.extra or {}).get("final_url")
    return (
        is_site_furniture(source.url)
        or bool(final and is_site_furniture(final))
        or title_says_missing(source.title)
    )


async def cited_source_ids(sess) -> set[int]:
    """Sources with at least one cited chunk — one definition, in `meridian_core.chunks`."""
    return await chunks.cited_source_ids(sess)


async def run_pass(*, apply: bool, domain: str | None = None, examples: int = 20) -> FurnitureStats:
    stats = FurnitureStats()
    async with session("rw") as sess:
        evidence = await cited_source_ids(sess)
        query = select(Source).where(Source.retention_tier != JUNK)
        if domain:
            # One site at a time: `https://host/...` and any subdomain of it.
            query = query.where(
                Source.url.like(f"%://{domain}/%") | Source.url.like(f"%.{domain}/%")
            )
        rows = await sess.scalars(query.order_by(Source.source_id))
        for source in rows:
            stats.examined += 1
            if not is_furniture_source(source):
                continue
            if source.source_id in evidence:
                stats.kept_as_evidence += 1
                continue
            stats.furniture += 1
            if len(stats.examples) < examples:
                stats.examples.append(source.url)
            if apply:
                source.retention_tier = JUNK
        if apply:
            await sess.commit()
    log.info(
        "furniture sweep complete",
        extra={"examined": stats.examined, "furniture": stats.furniture, "applied": apply},
    )
    return stats


def render(stats: FurnitureStats, apply: bool) -> None:
    print("=== Site furniture in the corpus (B-42) ===")
    print(f"  examined    {stats.examined}")
    print(f"  furniture   {stats.furniture}")
    print(f"  kept        {stats.kept_as_evidence}  (furniture-shaped, but cited as evidence)")
    for url in stats.examples:
        print(f"    {url}")
    if not apply:
        print("\n  Dry run. Pass --apply to move these to the junk tier (nothing is deleted).")


async def _run(*, apply: bool, domain: str | None) -> FurnitureStats:
    try:
        return await run_pass(apply=apply, domain=domain)
    finally:
        await dispose_engines()


def main() -> None:
    """Entry point: ``python -m worker.furniture``."""
    parser = argparse.ArgumentParser(
        description="Move pages about the website itself to the junk tier (B-42).",
    )
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    parser.add_argument("--domain", help="only sources on this host and its subdomains")
    args = parser.parse_args()

    configure_logging("furniture")
    with bind_run_id(f"furniture-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(_run(apply=args.apply, domain=args.domain))
    render(stats, args.apply)


if __name__ == "__main__":
    main()
