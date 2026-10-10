"""Re-extract stored PDFs so their tables keep their rows (task `B-215`).

`B-214` keeps a table's rows for PDFs extracted from then on; the re-cut (`worker.rechunk`)
rebuilds from stored passages, which hold the table column by column, so it cannot. This
pass reads each stored PDF's raw file again and, only where that extraction places a table,
replaces the source's passages with what a fresh fetch would now store. A source no table
reaches is left exactly as it is, and so is one anything cites. Writes only with
``--apply``. See docs/features/extraction.md#re-extracting-stored-pdfs.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.boilerplate import host_key
from meridian_core.chunks import as_writes, carry_embeddings, cited_source_ids, replace_chunks
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Source

from . import rawstore
from .cleancut import clean_cut
from .extract.pdf import extract_pdf

log = get_logger(__name__)

PDF = "application/pdf"
EXAMPLES = 10

#: Sources written per commit, so a long run that stops keeps what it did.
COMMIT_EVERY = 25


@dataclasses.dataclass
class RetableStats:
    examined: int = 0
    changed: int = 0
    tables: int = 0
    passages_before: int = 0
    passages_after: int = 0
    #: New passages that kept a superseded one's vector rather than queue to embed.
    carried: int = 0
    #: New passages whose text a superseded one already had, so their vector can be kept.
    same_text: int = 0
    no_table: int = 0
    unchanged: int = 0
    cited: int = 0
    #: Why a source could not be read again; each is left as it was.
    skipped: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    examples: list[tuple[str, str]] = dataclasses.field(default_factory=list)


def _sources(domain: str | None, limit: int | None, source_id: int | None):
    query = (
        select(Source)
        .where(
            Source.retention_tier != "junk",
            Source.raw_file_path.is_not(None),
            Source.extra["media_type"].astext == PDF,
            # Text read by OCR did not come from the text layer this pass reads.
            Source.ocr_applied.is_not(True),
        )
        .order_by(Source.source_id)
    )
    if source_id is not None:
        query = query.where(Source.source_id == source_id)
    if domain:
        query = query.where(
            Source.url.like(f"%://{domain}/%")
            | Source.url.like(f"%://www.{domain}/%")
            | Source.url.like(f"%.{domain}/%")
        )
    if limit:
        query = query.limit(limit)
    return query


async def _live(sess: AsyncSession, source_id: int) -> list[str]:
    return list(
        await sess.scalars(
            select(Chunk.text)
            .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
            .order_by(Chunk.chunk_index)
        )
    )


def _read(source: Source, root: str | None) -> bytes | str:
    """The raw file's bytes, or why they cannot stand for the stored text."""
    try:
        content = rawstore.resolve(source.raw_file_path, root).read_bytes()
    except rawstore.UnsafeRawPath:
        return "unsafe path"
    except OSError:
        return "file missing"
    # The file is the one the stored passages were cut from only if it is the one fetched.
    if source.checksum and rawstore.checksum_for(content) != source.checksum:
        return "file differs from the fetch"
    return content


def _first_table(chunks) -> str:
    for chunk in chunks:
        start = chunk.text.find("| --- |")
        if start >= 0:
            head = chunk.text.rfind("\n", 0, start - 1) + 1
            return chunk.text[head:][:400]
    return ""


async def run_pass(
    *,
    apply: bool,
    domain: str | None = None,
    limit: int | None = None,
    source_id: int | None = None,
    root: str | None = None,
    session_factory=session,
) -> RetableStats:
    stats = RetableStats()
    async with session_factory() as sess:
        cited = await cited_source_ids(sess)
        ids = list(
            await sess.scalars(
                _sources(domain, limit, source_id).with_only_columns(Source.source_id)
            )
        )
        pending = 0
        for sid in ids:
            source = await sess.get(Source, sid)
            live = await _live(sess, sid)
            if source is None or not live:
                continue
            stats.examined += 1
            content = _read(source, root)
            if isinstance(content, str):
                stats.skipped[content] += 1
                continue
            document = await extract_pdf(content, with_metadata=False)
            if document.needs_ocr or not document.pages:
                stats.skipped["no text layer now"] += 1
                continue
            if not document.tables:
                stats.no_table += 1
                continue
            host = host_key((source.extra or {}).get("final_url") or source.url)
            # Lines were recorded when the source was fetched; recording them again would
            # count this page twice towards its host's furniture.
            cut = await clean_cut(sess, sid, host, pages=document.pages, record_lines=False)
            after = [chunk.text for chunk in cut.chunks]
            if after == live:
                stats.unchanged += 1
                continue
            if sid in cited:
                stats.cited += 1
                continue
            stats.changed += 1
            stats.tables += document.tables
            stats.passages_before += len(live)
            stats.passages_after += len(after)
            kept = set(live)
            stats.same_text += sum(
                1 for text in after if text in kept and not text.lstrip().startswith("|")
            )
            if len(stats.examples) < EXAMPLES:
                stats.examples.append((source.url, _first_table(cut.chunks)))
            if apply:
                await replace_chunks(sess, sid, as_writes(cut.chunks))
                stats.carried += await carry_embeddings(sess, sid)
                pending += 1
                if pending >= COMMIT_EVERY:
                    await sess.commit()
                    pending = 0
        # A report writes nothing, so leaving the session uncommitted is "report only".
        if apply:
            await sess.commit()

    log.info(
        "retable pass complete",
        extra={
            "examined": stats.examined,
            "changed": stats.changed,
            "tables": stats.tables,
            "cited_skipped": stats.cited,
            "applied": apply,
        },
    )
    return stats


def render(stats: RetableStats, apply: bool) -> None:
    print("=== Re-extract stored PDFs for their tables (B-215) ===")
    print(f"  PDFs examined          {stats.examined}")
    print(f"  {'changed' if apply else 'would change':22} {stats.changed}")
    print(f"  tables placed          {stats.tables}")
    print(f"  passages               {stats.passages_before} -> {stats.passages_after}")
    print(f"  same text, same vector {stats.same_text}")
    if apply:
        print(f"  vectors kept           {stats.carried}")
    print(f"  no table placed        {stats.no_table}")
    print(f"  same passages already  {stats.unchanged}")
    print(f"  cited, left alone      {stats.cited}")
    for reason, count in stats.skipped.most_common():
        print(f"  skipped: {reason:13} {count}")
    for url, table in stats.examples:
        print(f"\n  {url[:110]}")
        for line in table.split("\n")[:6]:
            print(f"    {line[:140]}")
    if not apply:
        print("\n  Report only. --apply supersedes the old passages (nothing is deleted).")


def main() -> None:
    """Entry point: ``python -m worker.retable``."""
    parser = argparse.ArgumentParser(description="Re-extract stored PDFs for their tables.")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    parser.add_argument("--domain", help="only sources on this host and its subdomains")
    parser.add_argument("--limit", type=int, help="at most this many sources")
    parser.add_argument("--source", type=int, help="only this source id")
    args = parser.parse_args()

    configure_logging("retable")

    async def go() -> RetableStats:
        try:
            return await run_pass(
                apply=args.apply, domain=args.domain, limit=args.limit, source_id=args.source
            )
        finally:
            await dispose_engines()

    with bind_run_id(f"retable-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    render(stats, args.apply)


if __name__ == "__main__":
    main()
