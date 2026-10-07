"""Office and document formats through MarkItDown (task P1-08, spec §6.6).

Bytes only, through `convert_stream` and an allowlist of four converters, never
`convert()` on a URL or path (AGENTS.md invariant). Runs in a thread with no wall-clock
timeout. Never raises: an unreadable document comes back as ``markitdown-failed``. No
page numbers. See docs/features/extraction.md#office-documents.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import re
import zipfile

from lxml import etree
from markitdown import MarkItDown, StreamInfo
from markitdown.converters import CsvConverter, DocxConverter, PptxConverter, XlsxConverter

from meridian_core.logging import get_logger

from .base import ExtractedDocument

log = get_logger(__name__)

EXTRACTOR = "markitdown"

#: What an unconvertible document carries instead; distinct from `markitdown` with
#: empty text, which means the document had nothing in it.
FAILED_EXTRACTOR = "markitdown-failed"

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: What this module claims, each verified against a real file. Deliberately short;
#: see docs/features/extraction.md#office-documents for what is left out and why.
SUPPORTED_MEDIA_TYPES = frozenset({DOCX, XLSX, PPTX, "text/csv", "application/csv"})

#: The extension MarkItDown routes on, from the media type, never the server's
#: untrusted filename.
_EXTENSIONS = {
    DOCX: ".docx",
    XLSX: ".xlsx",
    PPTX: ".pptx",
    "text/csv": ".csv",
    "application/csv": ".csv",
}

#: The OOXML formats, which are zip containers and therefore carry the metadata
#: part below. CSV has no metadata to carry.
_OOXML_MEDIA_TYPES = frozenset({DOCX, XLSX, PPTX})

#: Where OOXML keeps the fields a citation needs, in every one of the three.
_CORE_PROPERTIES_PART = "docProps/core.xml"

#: A real core.xml is a couple of kilobytes. The cap is here because the part is
#: read out of an untrusted archive, and a member that claims to be a gigabyte
#: is an attack rather than a document's title.
_MAX_CORE_PROPERTIES_BYTES = 1024 * 1024

_DC = "{http://purl.org/dc/elements/1.1/}"
_DCTERMS = "{http://purl.org/dc/terms/}"

#: W3CDTF, which is what OOXML puts in `dcterms:created` — `2026-03-14T09:00:00Z`.
#: Only the leading date is taken, and only when it is complete.
_W3CDTF_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def supports(media_type: str) -> bool:
    """Whether this module can convert that media type.

    The caller routes on this rather than on a guess about file extensions, so
    a format added to :data:`SUPPORTED_MEDIA_TYPES` becomes routable in one
    place, and a format that is not listed there is never handed any bytes.
    """
    return _normalise(media_type) in SUPPORTED_MEDIA_TYPES


async def extract_document(
    content: bytes, *, media_type: str, filename_hint: str | None = None
) -> ExtractedDocument:
    """Convert one Office or tabular document to markdown text (§6.6).

    ``filename_hint`` is context only; the converter is chosen from ``media_type``,
    already gated through :func:`supports`. Never raises: an unreadable document is
    returned with ``extractor="markitdown-failed"`` and no text.
    """
    normalised = _normalise(media_type)
    if normalised not in SUPPORTED_MEDIA_TYPES:
        # A routing mistake in the caller, not a property of the document: the
        # bytes were never given a chance. Louder than a conversion failure
        # because it means some other format is silently going unextracted.
        log.warning(
            "document extractor called with an unsupported media type",
            extra={"media_type": media_type, "bytes": len(content)},
        )
        return ExtractedDocument(extractor=FAILED_EXTRACTOR)

    if not content:
        # A thread hop and a magika inference to discover that zero bytes are
        # not a document.
        log.info("document is empty", extra={"media_type": normalised})
        return ExtractedDocument(extractor=FAILED_EXTRACTOR)

    return await asyncio.to_thread(_convert, content, normalised, filename_hint)


def _convert(content: bytes, media_type: str, filename_hint: str | None) -> ExtractedDocument:
    """The synchronous half, which runs off the event loop.

    Metadata is read here too rather than back on the loop: it means one thread
    hop per document, and unzipping a 20 MB archive to reach `core.xml` is not
    work the loop should be doing either.
    """
    try:
        result = _markitdown().convert_stream(
            io.BytesIO(content),
            stream_info=StreamInfo(
                mimetype=media_type,
                extension=_EXTENSIONS[media_type],
                filename=filename_hint,
            ),
        )
    except Exception as exc:
        # Every failure lands here and none is exceptional. The class name is
        # logged for a later re-extraction sweep.
        log.info(
            "markitdown could not convert the document",
            extra={
                "media_type": media_type,
                "bytes": len(content),
                "error": type(exc).__name__,
                "detail": str(exc)[:300],
            },
        )
        return ExtractedDocument(extractor=FAILED_EXTRACTOR)

    metadata = _core_properties(content, media_type)
    # MarkItDown's own `title` is None for Office formats, but beats nothing when
    # the document carried no core properties.
    if "title" not in metadata and (title := _clean(result.title)):
        metadata["title"] = title

    return ExtractedDocument(
        text=(result.markdown or "").strip(),
        # Empty on purpose: MarkItDown exposes no pagination for these formats,
        # so `page_or_offset` is a character offset for them (§5.3).
        pages=(),
        extractor=EXTRACTOR,
        **metadata,
    )


def _markitdown() -> MarkItDown:
    """A converter carrying only the formats this module claims.

    `enable_builtins=False` and no plugins; the four converters registered are the
    ones :data:`SUPPORTED_MEDIA_TYPES` promises. Built per document so nothing is
    shared between threads.
    """
    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    for registration in (CsvConverter(), PptxConverter(), XlsxConverter(), DocxConverter()):
        converter.register_converter(registration)
    return converter


def _core_properties(content: bytes, media_type: str) -> dict[str, object]:
    """Title, author, date and language from the document's own metadata.

    Read from OOXML's `core.xml`, since MarkItDown drops all of it. Nothing is guessed
    and nothing raised: anything missing or unreadable yields no metadata.
    """
    if media_type not in _OOXML_MEDIA_TYPES:
        return {}

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entry = archive.getinfo(_CORE_PROPERTIES_PART)
            if entry.file_size > _MAX_CORE_PROPERTIES_BYTES:
                log.info(
                    "skipping oversized document metadata part",
                    extra={"bytes": entry.file_size, "limit": _MAX_CORE_PROPERTIES_BYTES},
                )
                return {}
            root = etree.fromstring(archive.read(_CORE_PROPERTIES_PART), parser=_xml_parser())
    except Exception:
        # The document converted; only its metadata did not. Silent on purpose:
        # a record per document would drown the crawl log for fields that are
        # allowed to be absent, and the extraction itself already logged.
        return {}

    metadata: dict[str, object] = {}
    if title := _clean(root.findtext(f"{_DC}title")):
        metadata["title"] = title
    if author := _clean(root.findtext(f"{_DC}creator")):
        metadata["author"] = author
    if description := _clean(root.findtext(f"{_DC}description")):
        metadata["excerpt"] = description
    if language := _clean(root.findtext(f"{_DC}language")):
        metadata["language"] = language
    # `created`, never `modified`: a report re-saved in 2026 was still published
    # when it was published, and `publication_date` is what a citation shows.
    if created := _w3c_date(root.findtext(f"{_DCTERMS}created")):
        metadata["publication_date"] = created
    return metadata


def _xml_parser() -> etree.XMLParser:
    """A parser that resolves no entities and touches no network.

    `core.xml` is untrusted, and a default parser would read files or fetch URLs on
    its request. Built per call: lxml parsers must not be shared between threads.
    """
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


def _w3c_date(value: str | None) -> dt.date | None:
    """The date part of a W3CDTF timestamp, or None. Never a partial date.

    OOXML permits `2026` and `2026-03`; anything short of a complete date is
    discarded rather than padded.
    """
    if not value:
        return None
    match = _W3CDTF_RE.match(value.strip())
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """A stripped string, or None when it was absent or only whitespace.

    A `<dc:title/>` element is present and says nothing; storing "" would make a
    document with no title look like one with a blank one.
    """
    if value is None:
        return None
    return value.strip() or None


def _normalise(media_type: str) -> str:
    """`Text/CSV; charset=utf-8` and `text/csv` are the same media type.

    Servers send parameters, casing and padding freely, and a routing table that
    misses `text/csv; charset=utf-8` would drop those documents with no symptom
    beyond a corpus that is quietly missing them.
    """
    return media_type.split(";", 1)[0].strip().lower()
