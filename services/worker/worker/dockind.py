"""Classify stored sources by what kind of document they are (task `B-59`).

The fetch path classifies every page it chunks from now on
(`worker.extract.dockind`); this pass does the same for what was stored
before, and retires the chunks of the ones that turn out to be listings.

**The same input as the fetch path.** The fetch path classifies the text it
would chunk, with furniture left out; here that text is read back from the
live chunks, which are exactly that. The page's head metadata — `og:type` and
scholarly `citation_*` tags — comes from the raw file when one is kept and
readable; without it those two signals are simply absent, as they would be
for a page that declared nothing.

**Report by default.** ``--apply`` writes each verdict and supersedes a
listing's live chunks. It never deletes, and never retires the chunks of a
source anything cites (`chunks.cited_source_ids`): such a listing gets its
kind and keeps its chunks, and the report counts it. Sources already
classified are skipped unless ``--all`` asks for a re-derivation, which is
what a change to the rules needs.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import os
import time
from collections.abc import Sequence

from sqlalchemy import select

from meridian_core.chunks import cited_source_ids, replace_chunks
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Source

from . import rawstore
from .extract.dockind import Evidence, Verdict, classify, record_doc_kind
from .extract.html import has_scholarly_meta, og_type

log = get_logger(__name__)

EXAMPLES = 10
#: How much of a raw file to read for its head. The tags live in `<head>`,
#: and reading a whole PDF to learn it has no `<head>` would be waste.
HEAD_BYTES = 65536
#: Sources per transaction under ``--apply``.
BATCH = 200


@dataclasses.dataclass
class KindStats:
    examined: int = 0
    kinds: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    rules: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Listings holding live chunks, and how many chunks those are.
    listings_with_chunks: int = 0
    listing_chunks: int = 0
    #: Listings whose chunks something cites: classified, chunks kept.
    cited: int = 0
    #: Sources whose raw head could be read for its metadata.
    heads_read: int = 0
    changed: int = 0
    examples: dict[str, list[str]] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(list)
    )


def _head(source: Source, raw_root: str | os.PathLike[str] | None) -> str | None:
    """The first part of the stored raw file, decoded, or None when there is none to read."""
    if not source.raw_file_path:
        return None
    try:
        path = rawstore.resolve(source.raw_file_path, raw_root)
        with open(path, "rb") as handle:
            return handle.read(HEAD_BYTES).decode("utf-8", "replace")
    except (OSError, rawstore.UnsafeRawPath):
        return None


def evidence_for(source: Source, texts: Sequence[str], head: str | None) -> Evidence:
    """What the rules read, from a stored source, its live chunk texts, and its raw head."""
    extra = source.extra or {}
    return Evidence(
        url=extra.get("final_url") or source.url,
        media_type=extra.get("media_type"),
        tier=source.source_tier,
        # The fetch path joins the chunks it cut the same way (`main._chunk`).
        text="\n".join(texts),
        title=source.title,
        doi=source.doi,
        publication_date=source.publication_date,
        og_type=og_type(head) if head else None,
        scholarly_meta=has_scholarly_meta(head) if head else False,
    )


def _candidates(everything: bool, domain: str | None, limit: int | None):
    # Junk is left alone: an error page or a refused fetch is not a document of
    # any kind, and its chunks were already retired when it was marked.
    query = (
        select(Source.source_id).where(Source.retention_tier != "junk").order_by(Source.source_id)
    )
    if not everything:
        query = query.where(Source.doc_kind.is_(None))
    if domain:
        query = query.where(
            Source.url.like(f"%://{domain}/%")
            | Source.url.like(f"%://www.{domain}/%")
            | Source.url.like(f"%.{domain}/%")
        )
    if limit:
        query = query.limit(limit)
    return query


async def _texts(sess, source_id: int) -> list[str]:
    return list(
        await sess.scalars(
            select(Chunk.text)
            .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
            .order_by(Chunk.chunk_index)
        )
    )


def _count(stats: KindStats, source: Source, verdict: Verdict) -> None:
    stats.kinds[verdict.kind] += 1
    stats.rules[(verdict.kind, verdict.rule)] += 1
    if source.doc_kind != verdict.kind:
        stats.changed += 1
    examples = stats.examples[verdict.kind]
    if len(examples) < EXAMPLES:
        examples.append(f"{verdict.rule:24} {source.url[:100]}")


async def run_pass(
    *,
    apply: bool,
    everything: bool = False,
    domain: str | None = None,
    limit: int | None = None,
    raw_root: str | os.PathLike[str] | None = None,
    session_factory=session,
) -> KindStats:
    stats = KindStats()
    async with session_factory() as sess:
        cited = await cited_source_ids(sess)
        ids = list(await sess.scalars(_candidates(everything, domain, limit)))
        for start in range(0, len(ids), BATCH):
            batch = ids[start : start + BATCH]
            sources = (
                await sess.scalars(
                    select(Source).where(Source.source_id.in_(batch)).order_by(Source.source_id)
                )
            ).all()
            for source in sources:
                texts = await _texts(sess, source.source_id)
                head = _head(source, raw_root)
                if head is not None:
                    stats.heads_read += 1
                verdict = classify(evidence_for(source, texts, head))
                stats.examined += 1
                _count(stats, source, verdict)
                retire = verdict.kind == "listing" and bool(texts)
                if retire:
                    stats.listings_with_chunks += 1
                    stats.listing_chunks += len(texts)
                    if source.source_id in cited:
                        # A claim's evidence is not retired under it.
                        stats.cited += 1
                        retire = False
                if apply:
                    record_doc_kind(source, verdict)
                    if retire:
                        await replace_chunks(sess, source.source_id, [])
            if apply:
                await sess.commit()
        # Report only: nothing was written, so there is nothing to roll back.

    log.info(
        "doc kind pass complete",
        extra={
            "examined": stats.examined,
            "listings_with_chunks": stats.listings_with_chunks,
            "listing_chunks": stats.listing_chunks,
            "cited_skipped": stats.cited,
            "applied": apply,
        },
    )
    return stats


def render(stats: KindStats, apply: bool) -> None:
    print("=== What kind of document each source is (B-59) ===")
    print(f"  sources examined        {stats.examined}")
    print(f"  raw heads read          {stats.heads_read}")
    print(f"  {'changed' if apply else 'would change'} kind         {stats.changed}")
    for kind, count in stats.kinds.most_common():
        print(f"    {kind:10} {count}")
        for (k, rule), n in sorted(stats.rules.items()):
            if k == kind:
                print(f"      {rule:24} {n}")
    verb = "retired" if apply else "would retire"
    print(
        f"  listings holding chunks {stats.listings_with_chunks}"
        f" ({stats.listing_chunks} chunks {verb}, less the cited)"
    )
    print(f"  cited, chunks kept      {stats.cited}")
    for kind, examples in sorted(stats.examples.items()):
        print(f"\n  {kind}")
        for line in examples:
            print(f"    {line}")
    if not apply:
        print("\n  Report only. --apply records each kind and supersedes listings' chunks.")


def main() -> None:
    """Entry point: ``python -m worker.dockind``."""
    parser = argparse.ArgumentParser(description="Classify stored sources by kind (B-59).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    parser.add_argument(
        "--all", action="store_true", help="re-derive every source, not only unclassified ones"
    )
    parser.add_argument("--domain", help="only sources on this host and its subdomains")
    parser.add_argument("--limit", type=int, help="at most this many sources")
    args = parser.parse_args()

    configure_logging("dockind")

    async def go() -> KindStats:
        try:
            return await run_pass(
                apply=args.apply, everything=args.all, domain=args.domain, limit=args.limit
            )
        finally:
            await dispose_engines()

    with bind_run_id(f"dockind-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    render(stats, args.apply)


if __name__ == "__main__":
    main()
