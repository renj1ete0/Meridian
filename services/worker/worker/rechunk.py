"""Re-chunk stored sources with their furniture left out (task `B-43`).

The fetch path cleans every page it chunks from now on; this pass does the same
for what was chunked before it. It works from the live chunks, not from the raw
store, because a `background` source keeps no raw file (§5.4) — and the chunks
are enough: every chunk is a verbatim slice at a known place, so the text can be
rebuilt exactly where it matters.

**Rebuilding the text.** Unpaginated: every chunk is laid back at its offset and
the gaps between chunks — which the chunker only ever leaves as whitespace — are
filled with newlines, so new chunks cut from the rebuilt text carry offsets into
the *original* extraction and stay valid citations. Paginated: a page's chunks
are joined with a blank line, and the page number is the citation.

**Three phases**, because the per-host set needs every page's lines before any
page can be cleaned against it:

1. record `page_lines` for every source that has none — sources chunked before
   this task, whose live chunks are therefore still uncleaned. A source that
   already has lines is never re-recorded from its chunks, which may be cleaned.
2. rebuild `boilerplate_lines`.
3. re-cut each source and compare with what it holds.

Phases 1 and 2 write derived tables in every mode. Phase 3 changes chunks only
with ``--apply``, and never for a source anything cites
(`chunks.cited_source_ids`): a claim's evidence is not re-cut under it.
Re-chunked sources get new chunk ids, so they are embedded, novelty-checked and
topic-labelled again by the passes that already watch for that.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import time
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.boilerplate import has_page_lines, host_key, recompute, record_page_lines
from meridian_core.chunks import as_writes, cited_source_ids, replace_chunks
from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Chunk, Source
from meridian_core.search import PAGINATED_MEDIA_TYPES

from .cleancut import Cut, clean_cut
from .extract.base import Page
from .extract.clean import page_line_hashes
from .extract.pdf import GARBLED_PAGES_FOR_OCR, is_garbled
from .ocr_queue import enqueue_ocr, mark_scanned

log = get_logger(__name__)

EXAMPLES = 15


@dataclasses.dataclass
class RechunkStats:
    examined: int = 0
    recorded: int = 0
    changed: int = 0
    unchanged: int = 0
    kept_whole: int = 0
    cited: int = 0
    #: Stored PDFs whose text layer is garbled (`B-47`): sent to OCR, and
    #: those whose garbled pages are blanked while the rest are kept.
    garbled_to_ocr: int = 0
    garbled_pages_blanked: int = 0
    lines_removed: int = 0
    removed_chars: int = 0
    total_chars: int = 0
    reasons: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    examples: list[tuple[str, list[str]]] = dataclasses.field(default_factory=list)


def rebuild_text(chunks: Sequence[tuple[int, str]]) -> str:
    """Unpaginated text from ``(offset, text)`` pairs; gaps become newlines.

    Refuses overlapping chunks: they would mean offsets that are not what this
    pass assumes, and guessing would re-cut someone's paragraph.
    """
    if not chunks:
        return ""
    ordered = sorted(chunks)
    length = max(offset + len(text) for offset, text in ordered)
    buffer = ["\n"] * length
    cursor = 0
    for offset, text in ordered:
        if offset < cursor:
            raise ValueError(f"chunks overlap at offset {offset}")
        buffer[offset : offset + len(text)] = text
        cursor = offset + len(text)
    return "".join(buffer)


def rebuild_pages(chunks: Sequence[tuple[int, int, str]]) -> list[Page]:
    """Pages from ``(page, index, text)`` triples, a page's chunks joined by a blank line."""
    by_page: dict[int, list[tuple[int, str]]] = collections.defaultdict(list)
    for page, index, text in chunks:
        by_page[page].append((index, text))
    return [
        Page(number=number, text="\n\n".join(t for _, t in sorted(parts)))
        for number, parts in sorted(by_page.items())
    ]


def _paginated(source: Source) -> bool:
    return (source.extra or {}).get("media_type") in PAGINATED_MEDIA_TYPES


async def _live(sess: AsyncSession, source_id: int) -> list[Chunk]:
    return list(
        await sess.scalars(
            select(Chunk)
            .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
            .order_by(Chunk.chunk_index)
        )
    )


def _document(source: Source, live: Sequence[Chunk]) -> dict:
    if _paginated(source):
        return {
            "pages": rebuild_pages([(c.page_or_offset or 1, c.chunk_index, c.text) for c in live])
        }
    return {"text": rebuild_text([(c.page_or_offset or 0, c.text) for c in live])}


def _raw_texts(document: dict) -> list[str]:
    return [p.text for p in document["pages"]] if "pages" in document else [document["text"]]


def _sources(domain: str | None, limit: int | None):
    query = select(Source).where(Source.retention_tier != "junk").order_by(Source.source_id)
    if domain:
        query = query.where(
            Source.url.like(f"%://{domain}/%")
            | Source.url.like(f"%://www.{domain}/%")
            | Source.url.like(f"%.{domain}/%")
        )
    if limit:
        query = query.limit(limit)
    return query


async def run_pass(
    *, apply: bool, domain: str | None = None, limit: int | None = None, session_factory=session
) -> RechunkStats:
    stats = RechunkStats()

    # Phase 1: lines for every source that has none, from its uncleaned chunks.
    async with session_factory() as sess:
        for source in (await sess.scalars(_sources(domain, limit))).all():
            if await has_page_lines(sess, source.source_id):
                continue
            host = host_key((source.extra or {}).get("final_url") or source.url)
            live = await _live(sess, source.source_id)
            if not host or not live:
                continue
            document = _document(source, live)
            await record_page_lines(
                sess, source.source_id, host, page_line_hashes(_raw_texts(document))
            )
            stats.recorded += 1
        # Phase 2: the per-host sets, from every page recorded so far.
        await recompute(sess)
        await sess.commit()

    # Phase 3: re-cut, compare, and replace only with --apply.
    async with session_factory() as sess:
        cited = await cited_source_ids(sess)
        for source in (await sess.scalars(_sources(domain, limit))).all():
            live = await _live(sess, source.source_id)
            if not live:
                continue
            stats.examined += 1
            host = host_key((source.extra or {}).get("final_url") or source.url)
            document = _document(source, live)
            if "pages" in document:
                # `B-47`: a stored PDF whose text layer is garbled gets what a
                # fresh fetch of it now gets.
                pages = document["pages"]
                garbled = {p.number for p in pages if is_garbled(p.text)}
                if garbled and len(garbled) / len(pages) > GARBLED_PAGES_FOR_OCR:
                    if source.source_id in cited:
                        stats.cited += 1
                        continue
                    stats.garbled_to_ocr += 1
                    if len(stats.examples) < EXAMPLES:
                        stats.examples.append((source.url, ["(garbled text layer: sent to OCR)"]))
                    if apply:
                        await replace_chunks(sess, source.source_id, [])
                        await mark_scanned(sess, source)
                        await enqueue_ocr(sess, source.source_id, requested_by="rechunk")
                    continue
                if garbled:
                    stats.garbled_pages_blanked += len(garbled)
                    document = {
                        "pages": [
                            Page(number=p.number, text="") if p.number in garbled else p
                            for p in pages
                        ]
                    }
            cut: Cut = await clean_cut(sess, source.source_id, host, **document, record_lines=False)
            if cut.kept_original:
                stats.kept_whole += 1
            before = [c.text for c in live]
            after = [c.text for c in cut.chunks]
            if before == after:
                stats.unchanged += 1
                continue
            if source.source_id in cited:
                stats.cited += 1
                continue
            stats.changed += 1
            stats.lines_removed += cut.lines_removed
            stats.removed_chars += cut.removed_chars
            stats.total_chars += cut.total_chars
            stats.reasons.update(cut.reasons)
            if len(stats.examples) < EXAMPLES:
                gone = sorted(set(before) - set(after), key=len)[:3]
                stats.examples.append((source.url, gone))
            if apply:
                await replace_chunks(sess, source.source_id, as_writes(cut.chunks))
        # A report writes nothing in this phase, so there is nothing to roll
        # back; leaving the session uncommitted is the whole of "report only".
        if apply:
            await sess.commit()

    log.info(
        "rechunk pass complete",
        extra={
            "examined": stats.examined,
            "changed": stats.changed,
            "cited_skipped": stats.cited,
            "recorded": stats.recorded,
            "applied": apply,
        },
    )
    return stats


def render(stats: RechunkStats, apply: bool) -> None:
    print("=== Re-chunk without furniture (B-43) ===")
    print(f"  page lines recorded   {stats.recorded}")
    print(f"  sources examined      {stats.examined}")
    print(
        f"  would change          {stats.changed}"
        if not apply
        else f"  changed               {stats.changed}"
    )
    print(f"  unchanged             {stats.unchanged}")
    print(f"  cited, left alone     {stats.cited}")
    print(f"  kept whole (guard)    {stats.kept_whole}")
    print(f"  garbled, sent to OCR  {stats.garbled_to_ocr}")
    print(f"  garbled pages blanked {stats.garbled_pages_blanked}")
    print(f"  lines removed         {stats.lines_removed}")
    if stats.total_chars:
        share = stats.removed_chars / stats.total_chars
        print(
            f"  readable chars removed {stats.removed_chars} of {stats.total_chars} ({share:.1%})"
        )
    for reason, count in stats.reasons.most_common():
        print(f"    {reason:16} {count}")
    for url, gone in stats.examples:
        print(f"\n  {url[:110]}")
        for text in gone:
            print(f"    - {' '.join(text.split())[:140]}")
    if not apply:
        print("\n  Report only. --apply supersedes the old chunks (nothing is deleted).")


def main() -> None:
    """Entry point: ``python -m worker.rechunk``."""
    parser = argparse.ArgumentParser(description="Re-chunk sources without furniture (B-43).")
    parser.add_argument("--apply", action="store_true", help="write; without it, report only")
    parser.add_argument("--domain", help="only sources on this host and its subdomains")
    parser.add_argument("--limit", type=int, help="at most this many sources")
    args = parser.parse_args()

    configure_logging("rechunk")

    async def go() -> RechunkStats:
        try:
            return await run_pass(apply=args.apply, domain=args.domain, limit=args.limit)
        finally:
            await dispose_engines()

    with bind_run_id(f"rechunk-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        stats = asyncio.run(go())
    render(stats, args.apply)


if __name__ == "__main__":
    main()
