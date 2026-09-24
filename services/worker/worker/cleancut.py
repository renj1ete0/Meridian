"""Clean, then chunk: the one path from extracted text to chunk writes (task `B-43`).

Two callers — the fetch path (`main._chunk`) and the re-chunk pass
(`worker.rechunk`) — and one function, because a page cleaned one way when it
is fetched and another way when it is re-chunked would give the same text two
different sets of chunks depending on when it arrived.

In order: the page's candidate-line hashes are recorded from the *uncleaned*
text (`page_lines`, so repetition is always counted from what a site actually
serves), the host's boilerplate set is read, the cleaners pick lines to drop,
and the chunker cuts around them. Every chunk is still a verbatim slice of the
text it was cut from.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.boilerplate import boilerplate_for, record_page_lines

from .extract.base import Page
from .extract.chunk import TextChunk, chunk_pages, chunk_text
from .extract.clean import clean_pages, clean_text, document_share, page_line_hashes


@dataclasses.dataclass(frozen=True)
class Cut:
    chunks: list[TextChunk]
    #: Lines dropped, readable characters dropped and in all, and whether the
    #: guard kept the document whole.
    lines_removed: int
    removed_chars: int
    total_chars: int
    kept_original: bool
    reasons: dict[str, int]


async def clean_cut(
    sess: AsyncSession,
    source_id: int,
    host: str | None,
    *,
    text: str | None = None,
    pages: Sequence[Page] | None = None,
    record_lines: bool = True,
) -> Cut:
    """Chunk one document with its furniture left out.

    Exactly one of ``text`` and ``pages``. ``record_lines`` is off only for a
    re-chunk of text that may already have been cleaned — recording its hashes
    then would count a banner as absent from pages it was removed from.
    """
    if (text is None) == (pages is None):
        raise ValueError("pass exactly one of text and pages")

    raw = [p.text for p in pages] if pages is not None else [text or ""]
    if record_lines and host:
        await record_page_lines(sess, source_id, host, page_line_hashes(raw))
    boilerplate = await boilerplate_for(sess, host)

    reasons: dict[str, int] = {}
    if pages is not None:
        cleanings = clean_pages(pages, boilerplate=boilerplate)
        chunks = chunk_pages(pages, drop={n: c.spans for n, c in cleanings.items()})
        removed, total, kept = document_share(cleanings)
        removals = [r for c in cleanings.values() for r in c.removals]
    else:
        cleaning = clean_text(text or "", boilerplate=boilerplate)
        chunks = chunk_text(text or "", drop=cleaning.spans)
        removed, total, kept = (
            cleaning.removed_chars,
            cleaning.total_chars,
            cleaning.kept_original,
        )
        removals = list(cleaning.removals)
    for removal in removals:
        reasons[removal.reason] = reasons.get(removal.reason, 0) + 1
    return Cut(
        chunks=chunks,
        lines_removed=len(removals),
        removed_chars=removed,
        total_chars=total,
        kept_original=kept,
        reasons=reasons,
    )
