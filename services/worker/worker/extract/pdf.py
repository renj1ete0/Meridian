"""PDFs: native text, page boundaries, and knowing when it is a scan (P1-09).

§6.6 draws this as a fork, and the fork is the whole module:

```
fetch PDF → pdftotext → chars/page
  ├─ native  → extract → chunk → embed
  └─ scanned → enqueue to ocr_queue; source stays metadata-only
```

**Both branches matter and they fail differently.** A scan run through a text
extractor yields a handful of ligature artefacts, which clears no threshold and
looks exactly like an empty page — so without the chars-per-page check a scanned
planning report enters the corpus as "extracted, nothing found" and nobody ever
looks at it again. With the check it enters as a source that *needs* something,
and §6.6's OCR queue is where that something waits.

**OCR never runs inline.** It would stall the 23-hour loop for one document. The
source record enters the graph as metadata-only and is enriched later, so a scan
never blocks ingestion (§6.6, `P1-13`).

**Page numbers, not character offsets.** §5.3 makes `page_or_offset` mean one or
the other, and for a PDF a citation that cannot be opened at the right page is
barely a citation. `pdftotext` separates pages with a form feed, so the boundary
is free and exact rather than reconstructed — which §6.6 warns is the thing OCR
pipelines flatten first.

**`pdftotext` is a system binary, and its absence must be loud.** §6.6 has this
lesson already, about `markitdown-ocr` silently skipping OCR when no client is
configured: "silent degradation is unacceptable in an unattended system". A
worker that has quietly lost poppler would store PDFs and extract none of them,
and the only symptom would be a corpus that stopped growing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import re
import shutil

from meridian_core.logging import get_logger

from .base import ExtractedDocument, Page

log = get_logger(__name__)

PDFTOTEXT = "pdftotext"
PDFINFO = "pdfinfo"

#: §6.6: "Below ~100 chars/page means it is a scan." A page of body prose runs
#: to 1,500–3,000 characters, and a scanned page yields only whatever the
#: producer left in the text layer — a header, a page number, a stray ligature.
#: The gap between the two is wide enough that the exact threshold does not
#: matter much, which is why a cheap check can carry this decision.
SCAN_CHARS_PER_PAGE = 100

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


def available() -> bool:
    """Whether `pdftotext` is on the path."""
    return shutil.which(PDFTOTEXT) is not None


async def extract_pdf(
    content: bytes, *, timeout_s: int = DEFAULT_TIMEOUT_S, with_metadata: bool = True
) -> ExtractedDocument:
    """Extract one PDF's text, pages and metadata.

    Raises :class:`PdftotextMissing` if poppler is absent — that is a
    deployment fault affecting every PDF in the corpus, not a property of this
    document, and it must not read as "this PDF had no text".

    Everything else about a bad document is returned rather than raised: an
    encrypted file, a truncated download, a PDF that is really a HTML error page
    with the wrong `Content-Type`. Those are documents this crawler cannot read,
    and §6.5 already has a resting state for them.
    """
    if not available():
        raise PdftotextMissing(
            f"{PDFTOTEXT} is not on PATH; install poppler-utils or no PDF can be read"
        )

    extracted = await _run_pdftotext(content, timeout_s=timeout_s)
    if extracted is None:
        return ExtractedDocument(extractor="pdftotext-failed")

    if extracted.looks_scanned:
        log.info(
            "pdf looks scanned; queueing OCR rather than extracting",
            extra={
                "pages": extracted.page_count,
                "chars_per_page": round(extracted.chars_per_page, 1),
                "threshold": SCAN_CHARS_PER_PAGE,
            },
        )
        # The text layer's few characters are deliberately dropped. Keeping them
        # would put a page number and a running header into the corpus as if
        # they were the document's content, and the novelty gate would then have
        # to distinguish two scans by their headers.
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
        extractor="pdftotext",
        **metadata,
    )


async def _run_pdftotext(content: bytes, *, timeout_s: int) -> PdfText | None:
    """Run the extractor over stdin and split the output into pages.

    stdin rather than a temporary file: the bytes are already in memory, a temp
    file is one more thing to leak on an interrupted fetch, and `-` means
    nothing this crawler downloaded ever lands on disk under a name another
    process could reach.
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

    pages = tuple(
        Page(number=index, text=piece.strip()) for index, piece in enumerate(pieces, start=1)
    )
    return PdfText(pages=pages, page_count=len(pages))


async def _metadata(content: bytes, timeout_s: int) -> dict[str, object]:
    """Title, author and date from `pdfinfo`, for the fields a citation needs.

    A separate process from the text extraction, and worth it: a PDF with no
    title in `sources` is a citation that renders as a URL, and government
    reports set the field far more often than they are given credit for.
    Failure here is not failure of the document — the text is what matters.
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

    `D:20260314090000+08'00'` is what the spec calls a date. Only the day is
    taken, and only when all three components are there — `publication_date` is
    a DATE that citations are built from, and a fabricated day is worse than a
    missing one.
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
