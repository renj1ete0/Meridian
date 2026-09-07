"""Office and document formats through MarkItDown (task P1-08, spec §6.6).

§6.6's routing table sends everything that is neither HTML nor PDF to
MarkItDown, and says why: "government and consultancy sources arrive as Office
documents far more often than expected". A corpus that cannot read a .docx is a
corpus missing the planning reports.

**Bytes only, and only `convert_stream`.** §6.6 is explicit, and AGENTS.md
carries it as an invariant: MarkItDown "performs I/O with the privileges of the
calling process, and its `convert()` is intentionally permissive across local
files, remote URIs and byte streams". `convert()` on an attacker-supplied string
is an SSRF and a local-file read wearing the same method name — `file:///etc/`,
an internal `http://169.254.169.254/`, a path this process can reach and the
crawler never should. This module is handed bytes that `fetch.py` already
retrieved through the pinned-address path (`P1-24`), and it never learns a URL
or a path. `convert_local()` is equally out: the only path it could be given
would be one derived from a remote name.

**The converter set is an allowlist, not the default registry.** A built-in
`MarkItDown()` also carries converters that fetch URLs (YouTube, Wikipedia,
Bing), shell out to `exiftool` on untrusted bytes, and — `ZipConverter` —
extract an archive to a temporary directory and re-dispatch its members by
extension. A .docx is a zip, so a hostile "document" that is really an archive
reaches that converter through content sniffing whatever the declared type says,
and a zip bomb costs a disk rather than a request. Registering only the four
converters this module claims to support keeps that whole surface out of the
process, and makes a document that is not what it claimed fail loudly (returned
as a failure) rather than quietly convert as something else.

**MarkItDown sniffs; the declared media type is a hint.** It runs magika over
the stream and tries every converter that accepts any guess, so a `text/csv`
that is really a spreadsheet still converts as one. That is why routing is
gated *here*, on :data:`SUPPORTED_MEDIA_TYPES`, before any bytes are handed
over: the allowlist above is what bounds where sniffing can land.

**It runs in a thread.** MarkItDown is synchronous and CPU-bound — magika
inference, then mammoth or pandas — and a 30-second .xlsx would otherwise stall
every one of the worker's concurrent fetch lanes, not just its own (§6.1). There
is deliberately no wall-clock timeout: `asyncio.to_thread` cannot cancel a
running thread, so a timeout would free the lane while leaking a worker from a
pool of `min(32, cpu + 4)` — and a handful of pathological documents would then
stall the loop permanently, which is worse than the stall it was meant to fix.
The fetcher's `max_page_bytes` is the bound that can actually be enforced.

**A bad document is a returned document, never an exception.** This worker runs
unattended for weeks (§13.4), and one corrupt .docx must not end the crawl. A
document that cannot be converted comes back with `extractor="markitdown-failed"`
and no text, which §6.5 already has a resting state for: the source is stored,
citable and metadata-only, and `P1-19`'s re-extraction sweep can try again when
the extractor improves (§11.12).

**No pages.** MarkItDown exposes nothing about pagination for Office formats —
a .docx's page breaks are a rendering decision Word makes, not a property of the
file — so `pages` stays empty and §5.3's `page_or_offset` is a character offset
for these documents. Inventing page numbers would produce citations that open at
the wrong place and look right.
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

#: What an unconvertible document carries instead. Distinct from `markitdown`
#: with empty text, which means the converter ran and the document had nothing
#: in it — the two want different follow-ups and the same value for both would
#: make a broken file indistinguishable from an empty one.
FAILED_EXTRACTOR = "markitdown-failed"

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: What this module claims, verified against a real file of each kind rather
#: than against MarkItDown's documentation. Deliberately short:
#:
#: * `.doc`/`.xls` (the pre-2007 binary formats) have no converter here at all —
#:   `xls` needs `xlrd`, which the worker does not install, and MarkItDown has
#:   no `.doc` converter in any configuration.
#: * EPub needs `ebooklib`, also not installed. §6.6 lists the format, and the
#:   moment the dependency is added it belongs here — silently converting an
#:   EPub as a zip of HTML files instead is the "silent degradation" §6.6 calls
#:   unacceptable.
#: * ZIP is left out on purpose: see the module docstring, and note that a zip
#:   of twelve documents would enter the corpus as one source with one URL and
#:   one provenance record, which is not what §2.3 means by provenance.
#: * HTML belongs to `html.py`, where trafilatura strips the boilerplate that
#:   MarkItDown would faithfully convert into the corpus.
SUPPORTED_MEDIA_TYPES = frozenset({DOCX, XLSX, PPTX, "text/csv", "application/csv"})

#: MarkItDown routes on mimetype *or* extension, and the extension is what
#: several converters check first. It comes from the media type rather than from
#: the server's filename, which is untrusted and would otherwise be a second,
#: attacker-chosen way to pick a converter.
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
    a format that is added below becomes routable in one place.
    """
    return _normalise(media_type) in SUPPORTED_MEDIA_TYPES


async def extract_document(
    content: bytes, *, media_type: str, filename_hint: str | None = None
) -> ExtractedDocument:
    """Convert one Office or tabular document to markdown text (§6.6).

    ``filename_hint`` is passed to MarkItDown as context only — the converter is
    chosen from ``media_type``, which the caller has already gated through
    :func:`supports`. Never raises: an unreadable document is returned with
    ``extractor="markitdown-failed"`` and no text, because a worker that dies on
    one corrupt .docx stops crawling for a week (§13.4).
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
        # Every failure mode lands here and none of them is exceptional: a
        # truncated download, an archive that is not OOXML, a .docx that is
        # really an HTML error page served with the wrong Content-Type. The
        # class name is logged because "which converter refused, and why" is
        # what a re-extraction sweep would want to know.
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
    # MarkItDown's own `title` is set only by its HTML-derived converters, and
    # then only from a `<title>` element — for a .docx converted through mammoth
    # it is always None. It is still preferred over nothing when the document
    # carried no core properties.
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

    `enable_builtins=False` drops the URL-fetching, subprocess-spawning and
    archive-extracting converters described in the module docstring; the four
    registered here are the ones :data:`SUPPORTED_MEDIA_TYPES` promises. Plugins
    are off as well — §6.6 warns that `markitdown-ocr` loads and then silently
    skips OCR when no vision client is configured, and a plugin that changes
    what this module does without appearing in it is exactly that failure.

    Built per document rather than shared. It costs ~15 ms of magika model
    loading, and it buys the guarantee that nothing is shared between the
    concurrent threads these conversions run in.
    """
    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    for registration in (CsvConverter(), PptxConverter(), XlsxConverter(), DocxConverter()):
        converter.register_converter(registration)
    return converter


def _core_properties(content: bytes, media_type: str) -> dict[str, object]:
    """Title, author, date and language from the document's own metadata.

    MarkItDown drops all of it — it returns markdown and, for HTML-derived
    converters only, a title — so the fields a citation is built from would
    otherwise be NULL for every Office source in the corpus. OOXML keeps them in
    a small XML part inside the zip, and the three formats share it.

    Nothing here is guessed and no failure is raised: a document with no
    `core.xml`, a partial date, an archive that will not open — all of them
    yield no metadata, which is what §5.2 wants recorded when the document did
    not say (see :func:`_w3c_date`).
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

    `core.xml` arrives inside a document some server sent us, and an XML parser
    with its defaults on will happily read a local file — or fetch a URL — when
    the document asks it to, which is the same class of hole this module exists
    to keep MarkItDown out of.

    Built per call rather than kept as a module constant: lxml parsers hold
    parse state and must not be shared between the threads these conversions run
    in.
    """
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


def _w3c_date(value: str | None) -> dt.date | None:
    """The date part of a W3CDTF timestamp, or None. Never a partial date.

    OOXML permits `2026` and `2026-03` as well as a full timestamp, and a
    fabricated day is worse than a missing one for a corpus whose whole claim is
    being checkable — so anything short of a complete, real date is discarded
    rather than padded to the first of the month.
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
