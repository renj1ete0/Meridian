"""What every extractor returns, whatever the format (spec §5.2, §5.3, §6.6).

§6.6 routes each input to a different tool — Crawl4AI or trafilatura for HTML,
`pdftotext` for PDFs, MarkItDown for Office formats — and the pipeline behind
them is the same for all of it: fill the `sources` columns, cut chunks, expand
the frontier. So the *shape* of the answer has to be the same too, or the loop
grows a branch per format and each one is the place a field gets forgotten.

The one real difference between formats is what a chunk's `page_or_offset`
means. §5.3 defines it as "page number for paginated documents, character offset
otherwise", so a paginated document carries its :class:`Page` list and an
unpaginated one carries flat text. Everything downstream reads
:attr:`ExtractedDocument.is_paginated` rather than asking which extractor ran.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

#: Below this many characters a document is treated as having no usable text.
#: A real article clears it in its first paragraph; a cookie banner and a nav
#: menu do not, and admitting those as "content" is how a corpus fills with
#: boilerplate.
TEXT_FLOOR = 200


@dataclasses.dataclass(frozen=True)
class Citation:
    """One reference this document makes to an identifiable work.

    Kept as a normalised identifier plus its kind rather than as a resolved URL,
    because the resolution chain (`P1-14`: Unpaywall → OpenAlex → CORE →
    preprint) has opinions about where to look that extraction should not
    pre-empt.
    """

    kind: str  # doi | arxiv | pmid | handle
    value: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind}:{self.value}"


@dataclasses.dataclass(frozen=True)
class Page:
    """One page of a paginated document, with the number a citation resolves to.

    1-based, because that is what a reader sees on the page and what a PDF
    viewer's page box expects. An off-by-one here sends every citation in the
    corpus to the wrong page, and it would look exactly like a correct one.
    """

    number: int
    text: str

    def __len__(self) -> int:
        return len(self.text)


@dataclasses.dataclass(frozen=True)
class ExtractedDocument:
    """What one document yielded, whichever extractor produced it.

    ``text`` is markdown or plain text. ``links`` are absolute and
    scheme-filtered, ready for the prefilter to judge. Metadata fields are None
    when the document did not say — never guessed, because a fabricated
    publication date is worse than a missing one for a corpus whose whole job is
    being checkable.
    """

    text: str = ""
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    publication_date: dt.date | None = None
    language: str | None = None
    excerpt: str | None = None
    #: This document's *own* identifier, from its metadata — not one it cites.
    doi: str | None = None
    links: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
    #: Populated only by paginated formats. Empty means `page_or_offset` on this
    #: document's chunks is a character offset (§5.3).
    pages: tuple[Page, ...] = ()
    extractor: str = "unknown"
    #: True when the document is a scan with no extractable text layer. The
    #: source stays metadata-only and OCR is queued rather than run (§6.6) —
    #: distinct from "extracted nothing", which needs no follow-up.
    needs_ocr: bool = False

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def is_paginated(self) -> bool:
        return bool(self.pages)

    @property
    def has_text(self) -> bool:
        """Whether this yielded enough text to be worth chunking.

        A threshold, not `text != ""`. A page whose only extractable content is
        a cookie banner has text in the strict sense and nothing a graph can be
        built from, and calling that `text_available` would make §6.5's
        metadata-only state indistinguishable from a successful extraction.
        """
        return len(self.text) >= TEXT_FLOOR
