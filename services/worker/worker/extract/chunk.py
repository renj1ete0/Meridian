"""Splitting extracted text into chunks (task P2-02, spec §5.3, §6.2).

A chunk is the unit of three different things, and the split has to serve all
three: it is what gets embedded and retrieved, what the slow loop reads to
extract a relation from, and what an edge cites as its evidence. That last one
is why §5.3 insists the offset is captured *here* — "reconstructing it later is
painful and often impossible" — and it is also why a chunk has to make sense on
its own. An edge whose supporting chunk is half a sentence is an edge nobody can
check.

**Split on structure, not on a character count.** The text arriving here is
markdown, so it already carries its own boundaries: blank lines between
paragraphs, headings between sections. Cutting at those gives chunks that are
whole thoughts, which is the thing a fixed-width window is always approximating
and never quite achieving. A paragraph too long to fit is split at sentence
boundaries, and a sentence too long to fit is cut at the cap — each fallback
only reached when the one above it cannot help.

**No overlap, deliberately.** Overlap exists to compensate for blind splitting
cutting through the middle of an idea. Splitting on paragraph boundaries is a
direct fix for the same problem, and overlap on top of it would duplicate text
in the table, in the embedding index, and in every batch the slow loop reads —
paying three times to solve a problem already solved once.

**Offsets index the extracted text, not the raw file.** Extraction is
deterministic, so an offset plus the stored raw file locates the passage exactly
(§11.12's reprocessing depends on the same property). An offset into the raw
HTML would be meaningless the moment the extractor improved.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
from collections.abc import Sequence

from .base import Page

#: Target size in characters. Around 300 tokens for English prose — comfortably
#: inside bge-m3's window (§4) and small enough that a retrieved chunk is a
#: passage a reader can check rather than a page they have to search.
TARGET_CHARS = 1200

#: Hard ceiling. A paragraph longer than this is split at sentence boundaries;
#: nothing is ever emitted longer than this.
MAX_CHARS = 2000

#: Below this, a chunk is merged into its neighbour instead of standing alone.
#: A 40-character chunk is a heading or a stray caption — it retrieves badly on
#: its own, and as an edge's cited evidence it proves nothing.
MIN_CHARS = 120

#: Paragraph break: a blank line, however much whitespace it carries.
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n+")

#: Sentence end followed by a space and something that could start a sentence.
#: Deliberately conservative — this is a fallback for paragraphs that are already
#: too long, and a split that fires on "Fig. 3" costs more than one that misses.
_SENTENCE_RE = re.compile(r"(?<=[.!?])[ \n]+(?=[A-Z\"'(\[])")


@dataclasses.dataclass(frozen=True)
class TextChunk:
    """One chunk, with the offset that makes it citable.

    ``offset`` is the character index of this chunk's first character in the
    extracted text it came from — `chunks.page_or_offset` for anything that is
    not paginated (§5.3). ``index`` is its position in the document, which is
    what `chunks.chunk_index` and its uniqueness constraint hold.

    The chunk is a verbatim **slice**: ``source[offset:offset + len(text)]`` is
    the chunk, exactly. That is the whole point of carrying the offset at all —
    a citation resolver has to be able to go back to the document and find the
    passage, and a chunk assembled by rejoining pieces with separators of its
    own choosing could only ever be searched for, not located.
    """

    text: str
    offset: int
    index: int

    def __len__(self) -> int:
        return len(self.text)

    @property
    def end(self) -> int:
        return self.offset + len(self.text)


def chunk_text(
    text: str,
    *,
    target_chars: int = TARGET_CHARS,
    max_chars: int = MAX_CHARS,
    min_chars: int = MIN_CHARS,
) -> list[TextChunk]:
    """Split ``text`` into chunks, each carrying its offset in the original.

    Returns an empty list for text with nothing in it. A document that yields no
    chunks is a metadata-only source (§6.5), not a failure.
    """
    if max_chars < target_chars:
        raise ValueError("max_chars must not be smaller than target_chars")
    if min_chars > target_chars:
        raise ValueError("min_chars must not exceed target_chars")
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if not text or not text.strip():
        return []

    spans = _pack(_paragraph_spans(text), text, target_chars, max_chars)
    spans = _absorb_runts(spans, min_chars, max_chars)
    return [
        TextChunk(text=text[start:end], offset=start, index=index)
        for index, (start, end) in enumerate(spans)
    ]


# Everything below works in half-open ``(start, end)`` spans into the original
# text rather than in substrings. Merging two adjacent pieces is then
# ``(a.start, b.end)`` — which keeps whatever separator the document actually
# had between them, and keeps every chunk a slice rather than a reconstruction.


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Spans of the paragraphs in ``text``, blank-line separated and trimmed."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _PARAGRAPH_RE.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(text)))
    return [span for span in (_trim(text, *s) for s in spans) if span]


def _pack(
    spans: list[tuple[int, int]], text: str, target: int, maximum: int
) -> list[tuple[int, int]]:
    """Join consecutive paragraphs up to ``target``; split any over ``maximum``.

    Joining rather than one-chunk-per-paragraph: a page of one-line paragraphs
    would otherwise produce a chunk per line, and a two-sentence chunk retrieves
    badly and cites nothing worth reading.
    """
    out: list[tuple[int, int]] = []
    open_span: tuple[int, int] | None = None

    for start, end in spans:
        if end - start > maximum:
            if open_span:
                out.append(open_span)
                open_span = None
            out.extend(_split_sentences(text, start, end, target, maximum))
            continue
        if open_span and end - open_span[0] > target:
            out.append(open_span)
            open_span = None
        open_span = (open_span[0] if open_span else start, end)

    if open_span:
        out.append(open_span)
    return out


def _split_sentences(
    text: str, start: int, end: int, target: int, maximum: int
) -> list[tuple[int, int]]:
    """One oversized paragraph, split at sentence boundaries.

    Reached only when a single paragraph exceeds the hard cap — a legal recital,
    a table flattened into prose, a page with no paragraph breaks at all. A
    single sentence still over the cap is cut at the cap, because at that point
    there is no boundary left to respect and emitting it whole would overflow
    the embedding window.
    """
    body = text[start:end]
    boundaries = [start] + [start + m.end() for m in _SENTENCE_RE.finditer(body)] + [end]
    sentences = [
        span for span in (_trim(text, a, b) for a, b in itertools.pairwise(boundaries)) if span
    ]

    out: list[tuple[int, int]] = []
    open_span: tuple[int, int] | None = None
    for s_start, s_end in sentences:
        piece_start, piece_end = s_start, s_end
        while piece_end - piece_start > maximum:
            if open_span:
                out.append(open_span)
                open_span = None
            out.append((piece_start, piece_start + maximum))
            piece_start += maximum
        if piece_end <= piece_start:
            continue
        if open_span and piece_end - open_span[0] > target:
            out.append(open_span)
            open_span = None
        open_span = (open_span[0] if open_span else piece_start, piece_end)

    if open_span:
        out.append(open_span)
    return out


def _absorb_runts(
    spans: list[tuple[int, int]], minimum: int, maximum: int
) -> list[tuple[int, int]]:
    """Merge undersized chunks into their predecessor where there is room.

    A heading with no body under it, or a caption stranded after a long section,
    is a chunk that retrieves badly and proves nothing when cited. Merged
    backwards so offsets stay monotonically increasing across the document, and
    only when the two are actually adjacent — a runt separated from its
    predecessor by a discarded oversized paragraph is not its continuation.

    A runt in *first* position is merged forwards instead, because it has no
    predecessor and a document that opens with a `# Title` line is the common
    case rather than the odd one.
    """
    out: list[tuple[int, int]] = []
    for start, end in spans:
        if out and end - start < minimum and end - out[-1][0] <= maximum and out[-1][1] <= start:
            out[-1] = (out[-1][0], end)
            continue
        out.append((start, end))

    if len(out) > 1 and out[0][1] - out[0][0] < minimum and out[1][1] - out[0][0] <= maximum:
        out[:2] = [(out[0][0], out[1][1])]
    return out


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Narrow a span past its surrounding whitespace, or None if nothing is left.

    Narrowing the span rather than stripping the substring is what keeps the
    offset honest: stripping without moving the offset breaks every citation by
    however much leading whitespace the source happened to carry.
    """
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def chunk_pages(
    pages: Sequence[Page],
    *,
    target_chars: int = TARGET_CHARS,
    max_chars: int = MAX_CHARS,
    min_chars: int = MIN_CHARS,
) -> list[TextChunk]:
    """Chunk a paginated document, carrying page numbers instead of offsets.

    §5.3 makes `page_or_offset` mean one or the other, and for a PDF a citation
    that cannot be opened at the right page is barely a citation. So
    ``TextChunk.offset`` holds the page number here — the field is overloaded by
    the schema, and :attr:`ExtractedDocument.is_paginated` is what says which
    reading applies.

    **Chunks never span a page break.** A chunk covering pages 4 and 5 has to be
    cited as one of them, and it would send a reader to the wrong page for half
    its content. Short pages therefore make short chunks, which is the honest
    trade: a citation that lands is worth more than a chunk that is the ideal
    size. Runt-merging is per page for the same reason.

    ``chunk_index`` still runs across the whole document, because it is unique
    per source and orders the document for reading.
    """
    out: list[TextChunk] = []
    for page in pages:
        for chunk in chunk_text(
            page.text,
            target_chars=target_chars,
            max_chars=max_chars,
            min_chars=min_chars,
        ):
            out.append(TextChunk(text=chunk.text, offset=page.number, index=len(out)))
    return out
