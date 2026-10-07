"""Turning fetched bytes into text the graph can be built from (spec §6.6).

HTML through trafilatura (on the rendered page when the browser ran), PDFs through
`pdftotext`, everything else through MarkItDown on already-fetched bytes, never
`convert()` on a URL (AGENTS.md invariant). Nothing here calls a language model (§2.1).
"""

from .base import Citation, ExtractedDocument, Page
from .document import extract_document
from .html import extract_html
from .pdf import extract_pdf

__all__ = [
    "Citation",
    "ExtractedDocument",
    "Page",
    "extract_document",
    "extract_html",
    "extract_pdf",
]
