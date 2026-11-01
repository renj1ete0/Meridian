"""HTML to text, metadata and citations (task P1-07, spec §6.6, §5.2, §5.3).

Text comes from trafilatura at ``favor_precision``, from the rendered HTML when a browser
ran (its `fit_markdown` is only a fallback). Empty is a valid answer (metadata-only),
citations are extracted mechanically, and nothing produced here is trusted. See
docs/features/extraction.md#html.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import trafilatura
from lxml import etree
from lxml import html as lxml_html

from meridian_core.bibliography import judge_author, judge_date, judge_publisher
from meridian_core.logging import get_logger

from .base import TEXT_FLOOR, Citation, ExtractedDocument
from .declared import read_declared
from .figures import figures_from_html

log = get_logger(__name__)

#: Link schemes worth following or recording. Everything else — `mailto:`,
#: `javascript:`, `tel:`, `data:` — is either not a document or not fetchable,
#: and putting it in the frontier means a queue row that can only ever fail.
LINK_SCHEMES = frozenset({"http", "https"})

#: How many links to carry off one page. Frontier expansion is bounded
#: elsewhere, but a link farm with 40,000 anchors should not become 40,000
#: strings held in memory and written to a JSONB column on the way past.
MAX_LINKS = 500

MAX_CITATIONS = 200

# DOIs: the ISO/Crossref shape. The trailing class excludes sentence-ending
# punctuation: "10.1234/foo." is far more often a DOI followed by a full stop.
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]*[a-z0-9]", re.IGNORECASE)

# arXiv, both schemes: 2401.12345 since 2007, and math.GT/0309136 before it.
_ARXIV_RE = re.compile(
    r"\barxiv[:\s/]*((?:\d{4}\.\d{4,5}(?:v\d+)?)|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?))",
    re.IGNORECASE,
)
_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/((?:\d{4}\.\d{4,5}(?:v\d+)?)|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))",
    re.IGNORECASE,
)
_PMID_RE = re.compile(r"\bPMID[:\s]*(\d{4,9})\b", re.IGNORECASE)
_HANDLE_RE = re.compile(r"\bhdl\.handle\.net/([0-9.]+/\d+)")

#: Meta tags in which a page declares *its own* DOI (`sources.doi`), in rough order
#: of how reliably publishers populate them.
_DOI_META_NAMES = ("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi", "doi")

#: arXiv mints a DataCite DOI of this shape for every paper and publishes no
#: `citation_doi` tag. See docs/features/extraction.md#identifiers.
_ARXIV_DOI_PREFIX = "10.48550/arxiv."

_WS_RE = re.compile(r"[ \t]*\n[ \t]*")
_HOST_NAME = re.compile(r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def extract_html(
    content: bytes | str,
    url: str,
    *,
    browser_payload: dict[str, Any] | None = None,
    fetched: dt.date | None = None,
) -> ExtractedDocument:
    """Extract text, metadata and citations from one HTML document.

    ``browser_payload`` is Crawl4AI's result dict when the browser path ran.
    Both paths extract with the same tool at the same precision — see
    :func:`_from_browser` for why that is not what this used to do. ``fetched`` is
    the day the page was fetched (today when None), against which dates are judged.
    """
    html_text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content

    if browser_payload:
        document = _from_browser(browser_payload, url, content)
    else:
        document = _from_html(content, url)

    links = document.links or _links_from_html(html_text, url)
    own_doi = _own_doi(html_text, url)
    citations = _citations(document.text, links, exclude=_self_identifiers(own_doi, url))

    document = with_declared(document, html_text, fetched=fetched)

    return dataclasses.replace(
        document,
        links=links,
        citations=citations,
        doi=own_doi,
        # From the raw HTML: precision filtering strips `<figure>` wrappers (`P1-10`).
        figures=figures_from_html(content, url),
        # A page can name its own language more reliably than a guess from a
        # paragraph of it, and the extractors often decline to say.
        language=document.language or _language_from_html(html_text),
        english_alternate=english_alternate(html_text, url),
        og_type=og_type(html_text),
        schema_types=schema_types(html_text),
        scholarly_meta=has_scholarly_meta(html_text),
    )


def with_declared(
    document: ExtractedDocument, html_text: str, *, fetched: dt.date | None = None
) -> ExtractedDocument:
    """The document with its author, publisher and date chosen by precedence (`B-234`).

    What the page declares wins over what trafilatura inferred; every value is judged,
    and where each came from is recorded in ``metadata_from``. A title or language the
    page declares fills one trafilatura did not find. See
    docs/features/extraction.md#source-metadata.
    """
    declared = read_declared(html_text)
    chosen: dict[str, str] = {}
    rejected: dict[str, str] = {}

    date = None
    if declared.publication_date is not None:
        found = declared.publication_date
        verdict = judge_date(found.value, source=found.source, fetched=fetched)
        if verdict.value is not None:
            date, chosen["publication_date"] = verdict.value, found.source
        else:
            rejected["publication_date"] = found.value.isoformat()
    if date is None and document.publication_date is not None:
        verdict = judge_date(document.publication_date, source="inferred", fetched=fetched)
        if verdict.value is not None:
            date, chosen["publication_date"] = verdict.value, "inferred"
        else:
            rejected.setdefault("publication_date", document.publication_date.isoformat())

    author = None
    if declared.author is not None:
        author, chosen["author"] = declared.author.value, declared.author.source
    elif document.author is not None:
        author = judge_author(document.author).value
        if author is not None:
            chosen["author"] = "trafilatura"
        else:
            rejected["author"] = document.author

    publisher = None
    if declared.publisher is not None:
        publisher, chosen["publisher"] = declared.publisher.value, declared.publisher.source
    elif document.publisher is not None:
        publisher = judge_publisher(document.publisher, generator=declared.generator).value
        if publisher is not None:
            chosen["publisher"] = document.metadata_from.get("publisher", "trafilatura")
        else:
            rejected["publisher"] = document.publisher

    title = document.title
    if title is None and declared.title is not None:
        title, chosen["title"] = declared.title.value, declared.title.source
    language = document.language
    if language is None and declared.language is not None:
        language = declared.language.value

    return dataclasses.replace(
        document,
        title=title,
        author=author,
        publisher=publisher,
        publication_date=date,
        language=language,
        metadata_from=chosen,
        metadata_rejected=rejected,
    )


# --------------------------------------------------------------------------
# The two sources of text
# --------------------------------------------------------------------------


def _pruned_markdown(payload: dict[str, Any]) -> str:
    """Crawl4AI's `PruningContentFilter` output, whichever shape it shipped in.

    `fit_markdown` is the filtered text; `raw_markdown` is the whole page
    including its chrome. Crawl4AI has shipped `markdown` as both a string and a
    dict across versions, so both shapes are read rather than assumed.
    """
    markdown = payload.get("markdown")
    if isinstance(markdown, dict):
        return (markdown.get("fit_markdown") or markdown.get("raw_markdown") or "").strip()
    if isinstance(markdown, str):
        return markdown.strip()
    return ""


def _from_browser(payload: dict[str, Any], url: str, content: bytes | str) -> ExtractedDocument:
    """A rendered page, filtered to the same standard as every other page (`P1-43`).

    trafilatura extracts the text from the rendered HTML (``content``); the payload
    contributes metadata and links from the rendered DOM, and `fit_markdown` is the
    fallback when trafilatura finds nothing. See docs/features/extraction.md#html.
    """
    metadata = payload.get("metadata") or {}
    local = _from_html(content, url)
    links = _links_from_payload(payload, url) or local.links

    if local.has_text:
        return dataclasses.replace(
            local,
            links=links,
            # Names both halves, because "which extractor ran" now has two
            # answers on this path and `P1-44` writes it to the source.
            extractor="trafilatura+rendered",
            title=local.title or _clean(metadata.get("title")),
            author=local.author or _clean(metadata.get("author")),
            excerpt=local.excerpt or _clean(metadata.get("description")),
            language=local.language or _clean(metadata.get("language")),
        )

    pruned = _pruned_markdown(payload)
    if len(pruned) >= TEXT_FLOOR:
        # trafilatura found nothing and the browser did. Below the floor a
        # `fit_markdown` is a cookie banner, not content.
        return ExtractedDocument(
            text=_normalise(pruned),
            title=_clean(metadata.get("title")),
            author=_clean(metadata.get("author")),
            excerpt=_clean(metadata.get("description")),
            language=_clean(metadata.get("language")),
            links=links,
            extractor="crawl4ai",
        )

    # Neither found text. Return the local result rather than nothing: it
    # carries whatever metadata trafilatura did find, and `has_text` already
    # says the document is metadata-only (§6.5).
    return dataclasses.replace(local, links=links)


def _from_html(content: bytes | str, url: str) -> ExtractedDocument:
    """Local extraction, for everything that never went through a browser.

    ``favor_precision`` deliberately. Bytes are passed through undecoded, since
    trafilatura does its own encoding detection.
    """
    try:
        extracted = trafilatura.extract(
            content,
            url=url,
            output_format="json",
            with_metadata=True,
            include_links=True,
            include_tables=True,
            favor_precision=True,
            no_fallback=False,
        )
    except Exception:
        # trafilatura raising is a malformed document, not a broken worker. The
        # source still enters the graph as metadata-only (§6.5).
        log.exception("html extraction failed", extra={"url": url})
        return ExtractedDocument(extractor="failed")

    if not extracted:
        # trafilatura ran and found nothing worth keeping. Named, not blank:
        # "nothing to extract" and "the extractor fell over" need different
        # follow-ups and `extractor` is where the difference is recorded.
        return ExtractedDocument(extractor="trafilatura")

    try:
        fields = json.loads(extracted)
    except ValueError:
        log.warning("html extraction returned unparseable output", extra={"url": url})
        return ExtractedDocument(extractor="failed")

    sitename, hostname = _clean(fields.get("source-hostname")), _clean(fields.get("hostname"))
    if sitename and _HOST_NAME.fullmatch(sitename.lower()):
        # trafilatura falls back to the full host name when the page names no site.
        sitename = None
    publisher = sitename or hostname
    return ExtractedDocument(
        extractor="trafilatura",
        text=_normalise(fields.get("text") or ""),
        title=_clean(fields.get("title")),
        author=_clean(fields.get("author")),
        # trafilatura's JSON names the site `source-hostname`; `hostname` is the address.
        publisher=publisher,
        metadata_from={"publisher": "trafilatura" if sitename else "host"} if publisher else {},
        publication_date=_as_date(fields.get("date")),
        language=_clean(fields.get("language")),
        excerpt=_clean(fields.get("excerpt")),
    )


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------


def _links_from_payload(payload: dict[str, Any], base: str) -> tuple[str, ...]:
    """Links Crawl4AI already collected, in its `{internal, external}` shape."""
    links = payload.get("links") or {}
    if not isinstance(links, dict):
        return ()
    found: list[str] = []
    for group in ("internal", "external"):
        for entry in links.get(group) or []:
            href = entry.get("href") if isinstance(entry, dict) else entry
            if isinstance(href, str):
                found.append(href)
    return _tidy_links(found, base)


def _links_from_html(html_text: str, base: str) -> tuple[str, ...]:
    """Anchor targets on the page, absolute and filtered.

    Anchors only, not `iterlinks()`, which also yields every asset URL.
    """
    try:
        tree = lxml_html.fromstring(html_text)
    except etree.ParserError, ValueError:
        return ()
    hrefs = tree.xpath("//a/@href | //area/@href")
    return _tidy_links([h for h in hrefs if isinstance(h, str)], base)


def _tidy_links(hrefs: list[str], base: str) -> tuple[str, ...]:
    """Absolute, http(s)-only, fragment-stripped, deduplicated, capped.

    Order is preserved through the deduplication: a page's first links are its
    most likely to matter, and truncating an arbitrary order would drop them as
    readily as the footer.
    """
    seen: dict[str, None] = {}
    for href in hrefs:
        candidate = href.strip()
        if not candidate or candidate.startswith("#"):
            continue
        try:
            absolute = urljoin(base, candidate)
        except ValueError:
            continue
        split = urlsplit(absolute)
        if split.scheme.lower() not in LINK_SCHEMES or not split.netloc:
            continue
        # The fragment identifies a place within a document, not a different
        # document, so keeping it would enqueue the same page once per heading.
        seen.setdefault(split._replace(fragment="").geturl(), None)
        if len(seen) >= MAX_LINKS:
            break
    return tuple(seen)


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------


def _own_doi(html_text: str, url: str = "") -> str | None:
    """The DOI this page claims for *itself*, from its meta tags or its URL.

    Only the head is searched. The URL is the fallback, for arXiv. See
    docs/features/extraction.md#identifiers.
    """
    head = html_text[:20000]
    for name in _DOI_META_NAMES:
        pattern = re.compile(
            rf"<meta[^>]+(?:name|property)\s*=\s*[\"']{re.escape(name)}[\"'][^>]*>",
            re.IGNORECASE,
        )
        for tag in pattern.finditer(head):
            match = _DOI_RE.search(tag.group(0))
            if match:
                return match.group(0).lower()

    arxiv_id = _ARXIV_URL_RE.search(url)
    if arxiv_id:
        # Version-stripped: `…v2` identifies one revision, and the DOI arXiv
        # mints for the work covers all of them.
        return f"{_ARXIV_DOI_PREFIX}{re.sub(r'v\d+$', '', arxiv_id.group(1)).lower()}"
    return None


def _self_identifiers(own_doi: str | None, url: str) -> frozenset[str]:
    """Identifiers that name *this* document, in every form they appear in.

    Removed from the citations, or every paper would cite itself.
    """
    selves: set[str] = set()
    if own_doi:
        selves.add(own_doi)
        if own_doi.startswith(_ARXIV_DOI_PREFIX):
            selves.add(own_doi[len(_ARXIV_DOI_PREFIX) :])
    match = _ARXIV_URL_RE.search(url)
    if match:
        selves.add(_unversioned(match.group(1)))
    return frozenset(selves)


def _unversioned(identifier: str) -> str:
    """An arXiv id without its `v2` suffix. Versions are revisions of one work."""
    return re.sub(r"v\d+$", "", identifier)


def _citations(
    text: str, links: tuple[str, ...], *, exclude: frozenset[str] = frozenset()
) -> tuple[Citation, ...]:
    """Identifiers this document references, from its text and its links.

    Both, because a reference list writes DOIs as text while a button carries one
    only in an `href`.
    """
    haystack = f"{text}\n{' '.join(links)}"
    found: dict[tuple[str, str], None] = {}

    def add(kind: str, value: str) -> None:
        # A document is not a citation of itself. Compared version-stripped so
        # `2401.02777v2` on an abstract page for `2401.02777` is recognised as
        # the same work rather than as one it references.
        if value in exclude or _unversioned(value).lower() in exclude:
            return
        if len(found) < MAX_CITATIONS:
            found.setdefault((kind, value), None)

    for match in _DOI_RE.finditer(haystack):
        add("doi", match.group(0).lower())
    for match in _ARXIV_URL_RE.finditer(haystack):
        add("arxiv", match.group(1))
    for match in _ARXIV_RE.finditer(haystack):
        add("arxiv", match.group(1))
    for match in _PMID_RE.finditer(haystack):
        add("pmid", match.group(1))
    for match in _HANDLE_RE.finditer(haystack):
        add("handle", match.group(1))

    return tuple(Citation(kind=kind, value=value) for kind, value in found)


# --------------------------------------------------------------------------
# Odds and ends
# --------------------------------------------------------------------------


_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


def english_alternate(html_text: str, url: str) -> str | None:
    """The page's declared English version, as an absolute URL, or None (`B-57`).

    From `<link rel="alternate" hreflang="en…">` in the head. Any English region
    counts; `x-default` and a link to the page itself do not.
    """
    head = html_text[
        : html_text.lower().find("</head>") if "</head>" in html_text.lower() else 20000
    ]
    for tag in _LINK_TAG.findall(head):
        attrs = {
            m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None)
            for m in _ATTR.finditer(tag)
        }
        rel = attrs.get("rel", "").lower().split()
        lang = attrs.get("hreflang", "").lower()
        href = attrs.get("href", "").strip()
        if "alternate" not in rel or not href or not (lang == "en" or lang.startswith("en-")):
            continue
        absolute = urljoin(url, href)
        if absolute.split("#")[0].rstrip("/") == url.split("#")[0].rstrip("/"):
            return None
        if absolute.startswith(("http://", "https://")):
            return absolute
    return None


#: Highwire-style tags a publisher writes for a scholarly work — the ones
#: indexers read. Their presence says the page describes one work of
#: scholarship; a reference list never produces them, because they are head
#: metadata about the page itself.
_SCHOLARLY_META = (
    "citation_title",
    "citation_journal_title",
    "citation_conference_title",
    "citation_doi",
    "citation_pdf_url",
)


def _head(html_text: str) -> str:
    lower = html_text.lower()
    end = lower.find("</head>")
    return html_text[: end if end != -1 else 20000]


def _meta_tags(html_text: str) -> list[dict[str, str]]:
    tags = []
    for tag in re.finditer(r"<meta\b[^>]*>", _head(html_text), re.IGNORECASE):
        tags.append(
            {
                m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None)
                for m in _ATTR.finditer(tag.group(0))
            }
        )
    return tags


def og_type(html_text: str) -> str | None:
    """The page's declared `og:type` (`article`, `website`, …), lower-cased (`B-59`)."""
    for attrs in _meta_tags(html_text):
        if (attrs.get("property") or attrs.get("name") or "").lower() == "og:type":
            value = (attrs.get("content") or "").strip().lower()
            return value or None
    return None


#: A JSON-LD block. Not only in `<head>`: many sites emit it at the end of `<body>`.
_JSON_LD = re.compile(
    r"<script\b[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
#: How much of a page to search for JSON-LD, so a huge page costs a bounded scan.
JSON_LD_CHARS = 400_000


def _own_items(data: Any) -> list[dict[str, Any]]:
    """The items a JSON-LD block says the page *is*: top level, list members, `@graph`.

    Nested values (a publisher, a list of related articles) describe other things.
    """
    if isinstance(data, list):
        return [item for entry in data for item in _own_items(entry)]
    if not isinstance(data, dict):
        return []
    graph = data.get("@graph")
    return [data, *(_own_items(graph) if graph is not None else [])]


def json_ld_items(html_text: str) -> list[dict[str, Any]]:
    """The JSON-LD items the page declares *itself* to be, in page order.

    A block that is not valid JSON is skipped, never repaired.
    """
    items: list[dict[str, Any]] = []
    for block in _JSON_LD.finditer(html_text[:JSON_LD_CHARS]):
        try:
            data = json.loads(block.group(1), strict=False)
        except ValueError:
            continue
        items.extend(_own_items(data))
    return items


def schema_types(html_text: str) -> tuple[str, ...]:
    """The schema.org `@type`s the page declares for itself, lower-cased (`B-253`).

    `https://schema.org/PressRelease` reads as `pressrelease`. Sorted, without repeats.
    """
    found: set[str] = set()
    for item in json_ld_items(html_text):
        declared = item.get("@type")
        for value in declared if isinstance(declared, list) else [declared]:
            if isinstance(value, str) and value.strip():
                found.add(value.strip().rstrip("/").rsplit("/", 1)[-1].lower())
    return tuple(sorted(found))


def meta_content(html_text: str, *names: str) -> str | None:
    """The first non-empty `content` of a head meta tag named any of ``names``."""
    wanted = {name.lower() for name in names}
    for attrs in _meta_tags(html_text):
        if (attrs.get("property") or attrs.get("name") or "").lower() in wanted:
            value = _clean(attrs.get("content"))
            if value:
                return value
    return None


def has_scholarly_meta(html_text: str) -> bool:
    """Whether the head carries `citation_*` tags describing one work (`B-59`)."""
    return any(
        (attrs.get("name") or attrs.get("property") or "").lower() in _SCHOLARLY_META
        and (attrs.get("content") or "").strip()
        for attrs in _meta_tags(html_text)
    )


def _language_from_html(html_text: str) -> str | None:
    """`<html lang="...">`, trimmed to the primary subtag.

    Regional variants are more precision than anything downstream uses, and
    splitting `en` from `en-GB` would fragment a language filter for no gain.
    """
    match = re.search(r"<html[^>]*\blang\s*=\s*[\"']([a-zA-Z]{2,3})", html_text[:4000])
    return match.group(1).lower() if match else None


def _as_date(value: str | None) -> dt.date | None:
    """A publication date, or None. Never a guess.

    Anything that is not a full ISO day is discarded rather than completed.
    """
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    """A trimmed string, or None for anything empty or not a string."""
    if not isinstance(value, str):
        return None
    trimmed = " ".join(value.split())
    return trimmed or None


def _normalise(text: str) -> str:
    """Collapse the whitespace that survives markdown conversion.

    Trailing spaces come from HTML indentation far more often than from an author,
    and would make chunk boundaries depend on formatting.
    """
    return _WS_RE.sub("\n", text).strip()
