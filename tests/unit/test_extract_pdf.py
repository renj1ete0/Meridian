"""PDF extraction and scan detection (task P1-09, spec §6.6, §5.3).

Against **real PDFs**, built here with ghostscript rather than checked in as
fixtures. A hand-written byte string that merely starts with `%PDF-` exercises
the error path and nothing else, and the two things worth testing — that page
boundaries survive, and that a scan is recognised as a scan — only exist inside
a file a real extractor can read.

The scan case is the one that fails silently otherwise. A scanned planning
report run through a text extractor yields a page number and a running header,
which clears no threshold and looks exactly like an empty document — so it
enters the corpus as "extracted, nothing found" and nobody looks again.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from worker.extract.base import Page
from worker.extract.chunk import chunk_pages
from worker.extract.pdf import (
    SCAN_CHARS_PER_PAGE,
    PdfText,
    PdftotextMissing,
    _pdf_date,
    available,
    extract_pdf,
)

pytestmark = pytest.mark.skipif(
    not available() or shutil.which("ps2pdf") is None,
    reason="needs poppler (pdftotext) and ghostscript (ps2pdf) to build real PDFs",
)

BODY = (
    "Ridership on the Downtown Line rose by eleven per cent over the period, "
    "against a network average of four per cent across the network as a whole."
)


def build_pdf(tmp_path: Path, pages: list[str], *, title: str = "") -> bytes:
    """A real PDF with a real text layer, one PostScript page per entry.

    The title goes in through a `pdfmark`, which is how PostScript names the
    document metadata ghostscript writes into the PDF's Info dictionary —
    `%%Title` is a DSC comment and never reaches the PDF at all.
    """
    body = ""
    for number, lines in enumerate(pages, start=1):
        drawn = "".join(
            f"72 {720 - 16 * i} moveto ({line}) show\n"
            for i, line in enumerate(lines.splitlines() or [""])
        )
        body += (
            f"%%Page: {number} {number}\n"
            f"/Helvetica findfont 11 scalefont setfont\n{drawn}showpage\n"
        )

    header = f"%!PS-Adobe-3.0\n%%Pages: {len(pages)}\n"
    if title:
        header += f"[ /Title ({title}) /DOCINFO pdfmark\n"
    source = tmp_path / "in.ps"
    source.write_text(header + body + "%%EOF\n")

    out = tmp_path / "out.pdf"
    subprocess.run(["ps2pdf", str(source), str(out)], check=True, capture_output=True)
    return out.read_bytes()


def prose_pdf(tmp_path: Path, page_count: int = 3) -> bytes:
    """Pages carrying enough text to clear the scan threshold comfortably."""
    return build_pdf(
        tmp_path,
        [f"Page {n}. {BODY}\n{BODY}\n{BODY}" for n in range(1, page_count + 1)],
    )


def scan_pdf(tmp_path: Path, page_count: int = 3) -> bytes:
    """What a scan's text layer actually looks like: a page number, and nothing."""
    return build_pdf(tmp_path, [str(n) for n in range(1, page_count + 1)])


# --------------------------------------------------------------------------
# Native text
# --------------------------------------------------------------------------


async def test_a_pdf_with_a_text_layer_is_extracted(tmp_path: Path) -> None:
    document = await extract_pdf(prose_pdf(tmp_path))

    assert document.extractor == "pdftotext"
    assert document.has_text
    assert not document.needs_ocr
    assert "Downtown Line" in document.text


async def test_pages_survive_as_pages(tmp_path: Path) -> None:
    """§5.3, and §6.6's warning that pipelines flatten pagination first.

    `pdftotext` separates pages with a form feed, so the boundary is exact
    rather than reconstructed — which is the whole reason to read it here rather
    than to recover page numbers later.
    """
    document = await extract_pdf(prose_pdf(tmp_path, page_count=4))

    assert document.is_paginated
    assert [page.number for page in document.pages] == [1, 2, 3, 4]
    for page in document.pages:
        assert f"Page {page.number}." in page.text


async def test_page_numbers_are_one_based(tmp_path: Path) -> None:
    """An off-by-one sends every citation to the wrong page and looks correct."""
    document = await extract_pdf(prose_pdf(tmp_path, page_count=2))

    assert document.pages[0].number == 1
    assert "Page 1." in document.pages[0].text


async def test_the_flat_text_is_every_page_in_order(tmp_path: Path) -> None:
    """Chunking a non-paginated view still has to read the document correctly."""
    document = await extract_pdf(prose_pdf(tmp_path, page_count=3))

    positions = [document.text.index(f"Page {n}.") for n in (1, 2, 3)]
    assert positions == sorted(positions)


# --------------------------------------------------------------------------
# Scan detection — the fork in §6.6
# --------------------------------------------------------------------------


async def test_a_scan_is_recognised_rather_than_read_as_empty(tmp_path: Path) -> None:
    """The failure this check exists to prevent.

    Without it a scanned report is indistinguishable from a page with no
    content, and the difference is that one of them can be recovered.
    """
    document = await extract_pdf(scan_pdf(tmp_path))

    assert document.needs_ocr
    assert not document.has_text


async def test_a_scans_stray_text_layer_is_not_kept_as_content(tmp_path: Path) -> None:
    """A page number and a running header are not the document.

    Keeping them would put chrome into the corpus as if it were content, and
    leave the novelty gate distinguishing two scans by their headers.
    """
    document = await extract_pdf(scan_pdf(tmp_path))

    assert document.text == ""
    assert document.pages == ()


async def test_a_document_with_text_is_not_sent_for_ocr(tmp_path: Path) -> None:
    """The expensive false positive: OCR queued for something already readable."""
    document = await extract_pdf(prose_pdf(tmp_path))

    assert not document.needs_ocr


@pytest.mark.parametrize(
    "pages,chars,expected",
    [
        (10, 10 * (SCAN_CHARS_PER_PAGE - 1), True),
        (10, 10 * (SCAN_CHARS_PER_PAGE + 1), False),
        (1, SCAN_CHARS_PER_PAGE - 1, True),
        # No pages at all is a file that could not be read, not a scan — and
        # queueing OCR for it would be queueing work on nothing.
        (0, 0, False),
    ],
)
def test_the_threshold_is_per_page_not_per_document(pages: int, chars: int, expected: bool) -> None:
    """A 400-page report with a thin appendix must not read as a scan."""
    per_page = chars // pages if pages else 0
    text = PdfText(
        pages=tuple(Page(number=n, text="x" * per_page) for n in range(1, pages + 1)),
        page_count=pages,
    )

    assert text.looks_scanned is expected


def test_chars_per_page_does_not_divide_by_zero() -> None:
    assert PdfText(pages=(), page_count=0).chars_per_page == 0.0


# --------------------------------------------------------------------------
# Documents that cannot be read
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html><body>404 Not Found</body></html>",  # wrong Content-Type
        b"%PDF-1.7\ntruncated",
        b"\x00\x01\x02\x03",
    ],
)
async def test_a_file_that_is_not_a_readable_pdf_returns_rather_than_raises(
    content: bytes,
) -> None:
    """A worker that runs for weeks must not die on one bad download.

    And none of these is a scan — queueing OCR for a HTML error page would put
    work nobody can do in a queue an operator pays from.
    """
    document = await extract_pdf(content)

    assert document.extractor == "pdftotext-failed"
    assert not document.has_text
    assert not document.needs_ocr


async def test_a_slow_document_is_abandoned_rather_than_stalling_a_lane(
    tmp_path: Path,
) -> None:
    """One document is never worth a stalled lane."""
    document = await extract_pdf(prose_pdf(tmp_path), timeout_s=0)

    assert document.extractor == "pdftotext-failed"


async def test_a_missing_poppler_raises_rather_than_reading_as_no_text(monkeypatch) -> None:
    """§6.6's own lesson, applied here.

    A worker that has quietly lost poppler would store every PDF and extract
    none of them, and the only symptom would be a corpus that stopped growing.
    Silent degradation is what the spec calls unacceptable in an unattended
    system, so this is an exception and not an empty document.
    """
    monkeypatch.setattr("worker.extract.pdf.shutil.which", lambda _: None)

    with pytest.raises(PdftotextMissing, match="poppler"):
        await extract_pdf(b"%PDF-1.7")


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


async def test_a_title_is_read_from_the_document(tmp_path: Path) -> None:
    """A PDF with no title in `sources` is a citation that renders as a URL."""
    content = build_pdf(tmp_path, [f"Page 1. {BODY}"], title="Rail Ridership 2026")

    document = await extract_pdf(content)

    assert document.title == "Rail Ridership 2026"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("D:20260314090000+08'00'", (2026, 3, 14)),
        ("D:20260314", (2026, 3, 14)),
        ("D:20260314090000Z", (2026, 3, 14)),
    ],
)
def test_the_pdf_date_format_is_parsed(raw: str, expected: tuple[int, int, int]) -> None:
    """`D:20260314090000+08'00'` is what the PDF spec calls a date."""
    import datetime as dt

    assert _pdf_date(raw) == dt.date(*expected)


@pytest.mark.parametrize(
    "raw", [None, "", "2026-03-14", "D:2026", "D:20261332", "nonsense", "D:00000000"]
)
def test_a_date_that_is_not_a_full_day_is_discarded(raw: str | None) -> None:
    """`publication_date` is a DATE citations are built from, so a fabricated
    day is worse than a missing one."""
    assert _pdf_date(raw) is None


async def test_metadata_can_be_skipped(tmp_path: Path) -> None:
    """It is a second subprocess; a caller that does not need it should not pay."""
    document = await extract_pdf(prose_pdf(tmp_path), with_metadata=False)

    assert document.has_text
    assert document.title is None


# --------------------------------------------------------------------------
# Chunking a paginated document
# --------------------------------------------------------------------------


async def test_chunks_carry_page_numbers_not_offsets(tmp_path: Path) -> None:
    """§5.3: page number for a paginated document, character offset otherwise."""
    document = await extract_pdf(prose_pdf(tmp_path, page_count=3))

    chunks = chunk_pages(document.pages)

    assert {c.offset for c in chunks} == {1, 2, 3}


async def test_a_chunk_never_spans_a_page_break(tmp_path: Path) -> None:
    """A chunk covering pages 4 and 5 has to be cited as one of them, and would
    send a reader to the wrong page for half its content."""
    document = await extract_pdf(prose_pdf(tmp_path, page_count=4))

    for chunk in chunk_pages(document.pages):
        page = next(p for p in document.pages if p.number == chunk.offset)
        assert chunk.text in page.text, "a chunk drew text from more than one page"


async def test_chunk_indices_run_across_the_whole_document(tmp_path: Path) -> None:
    """`chunk_index` is unique per source and orders the document for reading,
    so it cannot restart at each page."""
    document = await extract_pdf(prose_pdf(tmp_path, page_count=4))

    chunks = chunk_pages(document.pages)

    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunking_no_pages_yields_no_chunks() -> None:
    assert chunk_pages([]) == []


def test_a_blank_page_contributes_nothing(tmp_path: Path) -> None:
    """Section dividers and back covers are real, and are not chunks."""
    chunks = chunk_pages([Page(number=1, text=BODY * 3), Page(number=2, text="   ")])

    assert {c.offset for c in chunks} == {1}
