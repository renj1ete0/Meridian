"""Turning fetched bytes into text the graph can be built from (spec §6.6).

Format routing is §6.6's table: HTML through Crawl4AI where the browser ran and
through a local extractor where it did not, everything else through MarkItDown
on already-fetched bytes — never `convert()` on a URL (AGENTS.md invariant).

Nothing here calls a language model. §2.1's fast-loop invariant is that
ingestion keeps working with every reasoning model offline, and extraction is
the last stage where that is easy to break.
"""

from .html import Citation, ExtractedDocument, extract_html

__all__ = ["Citation", "ExtractedDocument", "extract_html"]
