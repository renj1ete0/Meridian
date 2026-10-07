"""Splitting extracted text into chunks (task P2-02, spec §5.3, §6.2).

Split on structure (paragraphs and headings), then sentences, then a hard cap. No
overlap. Every chunk is a verbatim slice of the extracted text, and its offset indexes
that text, not the raw file. See docs/features/extraction.md#chunking.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
from collections.abc import Mapping, Sequence

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

    The chunk is a verbatim **slice**: ``source[offset:offset + len(text)]``.
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
    drop: Sequence[tuple[int, int]] = (),
) -> list[TextChunk]:
    """Split ``text`` into chunks, each carrying its offset in the original.

    ``drop`` is a list of ``(start, end)`` spans no chunk may contain (`B-43`). The
    text is chunked in the stretches between them, never re-joined, so every chunk
    stays a slice.

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

    spans: list[tuple[int, int]] = []
    for start, end in _kept_stretches(text, drop):
        paragraphs = [(a + start, b + start) for a, b in _paragraph_spans(text[start:end])]
        packed = _pack(paragraphs, text, target_chars, max_chars)
        spans.extend(_absorb_runts(packed, min_chars, max_chars))
    return [
        TextChunk(text=text[start:end], offset=start, index=index)
        for index, (start, end) in enumerate(spans)
    ]


def _kept_stretches(text: str, drop: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """The spans of ``text`` left once ``drop`` is taken out, in order.

    Refuses spans that overlap or run outside the text rather than guessing
    what was meant: a wrong span here silently removes somebody's paragraph.
    """
    out: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(drop):
        if not 0 <= start <= end <= len(text) or start < cursor:
            raise ValueError(f"drop span {(start, end)} overlaps another or leaves the text")
        if start > cursor:
            out.append((cursor, start))
        cursor = end
    if cursor < len(text):
        out.append((cursor, len(text)))
    return [span for span in out if text[span[0] : span[1]].strip()]


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

    Reached only when one paragraph exceeds the hard cap. A sentence still over the
    cap is cut at the cap.
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

    Backwards, and only when adjacent; a runt in *first* position (a `# Title`) is
    merged forwards.
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
    drop: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> list[TextChunk]:
    """Chunk a paginated document, carrying page numbers instead of offsets.

    ``drop`` maps a page number to the spans on that page no chunk may contain
    (see :func:`chunk_text`).

    ``TextChunk.offset`` holds the page number here; :attr:`ExtractedDocument.is_paginated`
    says which reading applies. Chunks never span a page break, and runts merge per
    page. ``chunk_index`` runs across the whole document.
    """
    out: list[TextChunk] = []
    for page in pages:
        for chunk in chunk_text(
            page.text,
            target_chars=target_chars,
            max_chars=max_chars,
            min_chars=min_chars,
            drop=(drop or {}).get(page.number, ()),
        ):
            out.append(TextChunk(text=chunk.text, offset=page.number, index=len(out)))
    return out
