"""PDFs: native text, page boundaries, and knowing when it is a scan (P1-09).

`pdftotext` page by page; too few characters per page means a scan, which is queued
for OCR and stored metadata-only, never processed inline. Citations are by page. A
missing `pdftotext` raises; anything wrong with one document is returned. See
docs/features/extraction.md#pdf.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import re
import shutil
import unicodedata

from meridian_core.logging import get_logger
from meridian_core.titles import plain_letters

from .base import ExtractedDocument, Page
from .figures import figures_from_pages

log = get_logger(__name__)

PDFTOTEXT = "pdftotext"
PDFINFO = "pdfinfo"

#: §6.6: "Below ~100 chars/page means it is a scan." The gap to body prose is wide.
SCAN_CHARS_PER_PAGE = 100

#: A page whose text layer is more than this share control characters, private-use
#: code points and replacement characters has no readable text (a custom font
#: encoding with no Unicode map). See docs/features/extraction.md#pdf.
GARBLED_SHARE = 0.2

#: Above this share of garbled pages the document is treated as a scan: its
#: readable pages are too few to stand for it, and OCR is the only way to its
#: text. At or below it the readable pages are kept and the garbled ones blanked.
GARBLED_PAGES_FOR_OCR = 0.5

#: How long one document may take. A malformed PDF can send an extractor into a
#: very long walk, and one document is never worth a stalled lane.
DEFAULT_TIMEOUT_S = 60

#: Page separator `pdftotext` writes between pages.
FORM_FEED = "\f"

#: pdfinfo emits `Key:  value` lines. Only a few are worth keeping — the rest is
#: producer strings and page geometry.
_INFO_RE = re.compile(r"^([A-Za-z][A-Za-z ]*):\s+(.*)$")

#: `D:20260314090000+08'00'` — the PDF date format, which is nobody's ISO 8601.
_PDF_DATE_RE = re.compile(r"^D:(\d{4})(\d{2})(\d{2})")


class PdftotextMissing(RuntimeError):
    """poppler is not installed, so no PDF in this corpus can be read."""


@dataclasses.dataclass(frozen=True)
class PdfText:
    """The raw result of running the extractor, before it is judged."""

    pages: tuple[Page, ...]
    page_count: int

    @property
    def total_chars(self) -> int:
        return sum(len(page) for page in self.pages)

    @property
    def chars_per_page(self) -> float:
        """Zero pages is zero characters per page, not a division by zero."""
        return self.total_chars / self.page_count if self.page_count else 0.0

    @property
    def looks_scanned(self) -> bool:
        """A document with pages and almost no text in them.

        Zero pages is not a scan — it is a file that could not be read at all,
        and queueing OCR for it would be queueing work on nothing.
        """
        return self.page_count > 0 and self.chars_per_page < SCAN_CHARS_PER_PAGE


def garbled_share(text: str) -> float:
    """Share of a text's visible characters that no reader could read.

    Controls other than layout whitespace, private-use code points, and U+FFFD.
    Visible means not whitespace — a page of blank lines is the scan check's
    business, not this one's.
    """
    visible = 0
    bad = 0
    for char in text:
        if char.isspace():
            continue
        visible += 1
        category = unicodedata.category(char)
        if category in ("Cc", "Co", "Cs") or char == "\ufffd":
            bad += 1
    return bad / visible if visible else 0.0


def is_garbled(text: str) -> bool:
    return garbled_share(text) > GARBLED_SHARE


def available() -> bool:
    """Whether `pdftotext` is on the path."""
    return shutil.which(PDFTOTEXT) is not None


async def extract_pdf(
    content: bytes, *, timeout_s: int = DEFAULT_TIMEOUT_S, with_metadata: bool = True
) -> ExtractedDocument:
    """Extract one PDF's text, pages and metadata.

    Raises :class:`PdftotextMissing` if poppler is absent, a deployment fault.
    Everything wrong with one document (encrypted, truncated, really HTML) is
    returned rather than raised.
    """
    if not available():
        raise PdftotextMissing(
            f"{PDFTOTEXT} is not on PATH; install poppler-utils or no PDF can be read"
        )

    extracted = await _run_pdftotext(content, timeout_s=timeout_s)
    if extracted is None:
        return ExtractedDocument(extractor="pdftotext-failed")

    garbled = [page.number for page in extracted.pages if is_garbled(page.text)]
    if garbled:
        share = len(garbled) / max(extracted.page_count, 1)
        log.info(
            "pdf text layer is garbled on some pages",
            extra={
                "pages": extracted.page_count,
                "garbled": len(garbled),
                "share": round(share, 2),
            },
        )
        if share > GARBLED_PAGES_FOR_OCR:
            # The same resting state as a scan: no text, OCR queued. The garbled
            # layer is dropped for the scan path's reason — kept, it would be
            # the document's "content" in search and in the novelty gate.
            return ExtractedDocument(
                pages=(),
                extractor="pdftotext",
                needs_ocr=True,
                **(await _metadata(content, timeout_s) if with_metadata else {}),
            )
        # Blanked, not removed: page numbers are citations, so the readable
        # pages keep theirs.
        dropped = set(garbled)
        extracted = dataclasses.replace(
            extracted,
            pages=tuple(
                Page(number=page.number, text="") if page.number in dropped else page
                for page in extracted.pages
            ),
        )

    if extracted.looks_scanned:
        log.info(
            "pdf looks scanned; queueing OCR rather than extracting",
            extra={
                "pages": extracted.page_count,
                "chars_per_page": round(extracted.chars_per_page, 1),
                "threshold": SCAN_CHARS_PER_PAGE,
            },
        )
        # The text layer's few characters (a page number, a header) are dropped,
        # not stored as if they were the document's content.
        return ExtractedDocument(
            pages=(),
            extractor="pdftotext",
            needs_ocr=True,
            **(await _metadata(content, timeout_s) if with_metadata else {}),
        )

    metadata = await _metadata(content, timeout_s) if with_metadata else {}
    return ExtractedDocument(
        text="\n\n".join(page.text for page in extracted.pages),
        pages=extracted.pages,
        # Caption lines from the text layer (`P1-10`). No image and no bbox: the
        # page is known exactly, the position on it is not known at all, and a
        # fabricated bbox would put false precision on a citation.
        figures=figures_from_pages(extracted.pages),
        extractor="pdftotext",
        **metadata,
    )


async def _run_pdftotext(content: bytes, *, timeout_s: int) -> PdfText | None:
    """Run the extractor over stdin and split the output into pages.

    stdin rather than a temporary file, so nothing downloaded lands on disk under a
    name another process could reach.
    """
    # `-q` silences the syntax warnings a real-world PDF corpus produces
    # constantly; `-enc UTF-8` because the default is locale-dependent and a
    # worker's locale is not something to depend on.
    output = await _capture([PDFTOTEXT, "-q", "-enc", "UTF-8", "-", "-"], content, timeout_s)
    if output is None:
        return None

    # A trailing form feed after the last page is normal, so an empty final
    # piece is dropped rather than counted as a blank page.
    pieces = output.split(FORM_FEED)
    if pieces and not pieces[-1].strip():
        pieces.pop()

    # Small-caps fonts reach the text as private-use code points; read as letters, they are
    # neither unreadable to a person nor counted as garbled.
    pages = tuple(
        Page(number=index, text=plain_letters(piece).strip())
        for index, piece in enumerate(pieces, start=1)
    )
    return PdfText(pages=pages, page_count=len(pages))


async def _metadata(content: bytes, timeout_s: int) -> dict[str, object]:
    """Title, author and date from `pdfinfo`, for the fields a citation needs.

    A separate process; failure here is not failure of the document.
    """
    output = await _capture([PDFINFO, "-enc", "UTF-8", "-"], content, timeout_s)
    if output is None:
        return {}

    fields: dict[str, str] = {}
    for line in output.splitlines():
        match = _INFO_RE.match(line)
        if match:
            fields[match.group(1).strip().lower()] = match.group(2).strip()

    metadata: dict[str, object] = {}
    if title := fields.get("title"):
        metadata["title"] = title
    if author := fields.get("author"):
        metadata["author"] = author
    # CreationDate over ModDate: a report re-saved in 2026 was still published
    # when it was published, and `publication_date` is what citations show.
    if date := _pdf_date(fields.get("creationdate")):
        metadata["publication_date"] = date
    return metadata


async def _capture(argv: list[str], stdin: bytes, timeout_s: int) -> str | None:
    """Run ``argv`` with ``stdin``, returning stdout or None if it did not work.

    The process is killed on timeout rather than left running: a lane that
    returned while its subprocess kept chewing on a malformed PDF would leak one
    poppler process per bad document, and a crawl finds plenty.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        log.warning("could not start %s", argv[0], extra={"error": str(exc)})
        return None

    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), timeout=timeout_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        log.warning("%s timed out", argv[0], extra={"timeout_s": timeout_s, "bytes": len(stdin)})
        return None

    if process.returncode != 0:
        log.info(
            "%s refused the document",
            argv[0],
            extra={
                "returncode": process.returncode,
                "stderr": err.decode("utf-8", "replace")[:300],
            },
        )
        return None
    return out.decode("utf-8", "replace")


def _pdf_date(value: str | None) -> dt.date | None:
    """A date from the PDF's own format, or None. Never a guess.

    `D:20260314090000+08'00'`: only the day is taken, and only when all three
    components are there.
    """
    if not value:
        return None
    match = _PDF_DATE_RE.match(value.strip())
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
