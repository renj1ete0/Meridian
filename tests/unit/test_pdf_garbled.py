"""A PDF whose text layer is garbled (task B-47).

A PDF drawn with a custom font encoding and no Unicode map extracts to control
characters. It is not blank, so the scanned-page check passes it, and it was
chunked and embedded as noise. The extractor is faked here: the question is
what the judgement does with its output, and building such a PDF needs a font
toolchain the suite does not have.
"""

from __future__ import annotations

import pytest

from worker.extract.base import Page
from worker.extract.chunk import chunk_pages
from worker.extract.pdf import GARBLED_SHARE, PdfText, extract_pdf, is_garbled

READABLE = "The report measured how far residents walked to a stop. " * 40
GARBLED = "".join(chr(0x10 + (i % 12)) for i in range(1800))


def _pages(*texts: str) -> PdfText:
    return PdfText(
        pages=tuple(Page(number=i + 1, text=t) for i, t in enumerate(texts)),
        page_count=len(texts),
    )


def _fake_extractor(monkeypatch, extracted: PdfText) -> None:
    async def fake(_content, *, timeout_s):
        return extracted

    monkeypatch.setattr("worker.extract.pdf._run_pdftotext", fake)
    monkeypatch.setattr("worker.extract.pdf.available", lambda: True)


@pytest.mark.parametrize(
    ("text", "garbled"),
    [
        (GARBLED, True),
        (READABLE, False),
        (" abc", True),  # private use
        ("�" * 10 + " real words here", True),
        ("Tabs\tand\nnewlines\fare layout, not garbage.", False),
        ("", False),
    ],
)
def test_a_garbled_text_layer_is_recognised(text: str, garbled: bool) -> None:
    assert is_garbled(text) is garbled


def test_the_garbled_threshold_sits_between_what_was_measured() -> None:
    # Garbled documents measured at 60–66%, the worst ordinary one at 5%.
    assert 0.05 < GARBLED_SHARE < 0.6


async def test_a_mostly_garbled_pdf_is_sent_for_ocr_with_no_text(monkeypatch) -> None:
    _fake_extractor(monkeypatch, _pages(GARBLED, GARBLED, READABLE))

    document = await extract_pdf(b"%PDF", with_metadata=False)

    assert document.needs_ocr and not document.has_text and document.pages == ()


async def test_garbled_pages_in_a_readable_pdf_are_blanked_and_numbers_kept(monkeypatch) -> None:
    _fake_extractor(monkeypatch, _pages(READABLE, GARBLED, READABLE))

    document = await extract_pdf(b"%PDF", with_metadata=False)

    assert not document.needs_ocr
    assert [p.number for p in document.pages] == [1, 2, 3]
    assert document.pages[1].text == ""
    assert "\x10" not in document.text
    assert [c.offset for c in chunk_pages(document.pages)] and all(
        c.offset != 2 for c in chunk_pages(document.pages)
    )
