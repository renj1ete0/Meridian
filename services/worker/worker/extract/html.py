"""HTML to text, metadata and citations (task P1-07, spec §6.6, §5.2, §5.3).

§6.6's routing table sends HTML to Crawl4AI, and §6.4's first operational
constraint says not to render every page. Both are right, and together they mean
this module has two inputs rather than one:

**The browser already did the work, when the browser ran.** Crawl4AI returns
`fit_markdown` — the output of its `PruningContentFilter` — alongside the raw
HTML, and re-extracting from that HTML locally would throw away a filter that
already ran on a rendered DOM this process never had. So a browser payload is
used as-is.

**Everything else is extracted here.** Most of the corpus takes the static path
and never touches Chromium, so most pages arrive as bytes with no markdown
attached. `trafilatura` does the boilerplate removal, configured to favour
precision: a research corpus would rather lose a sentence of body text than gain
a navigation menu, because boilerplate becomes entities, entities become edges,
and a graph full of "Skip to main content" is expensive to unpick later.

**Empty is a valid answer.** A page that yields no usable text is not a failure
— §6.5 makes metadata-only an explicit resting state, and the source is still a
citable participant in the graph. `text_available` is what records the
difference, so a document with no text is findable rather than merely absent.

**Citations are extracted mechanically.** DOIs, arXiv identifiers, PubMed and
handle IDs, from both the text and the links. No model is involved and none can
be (§2.1), and `P1-14` is what resolves them to full text later.

Nothing produced here is trusted. Extracted text is attacker-controlled content
from an untrusted page, and `P1-23`'s injection pre-screen is what looks at it
before any of it reaches a model.
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

from meridian_core.logging import get_logger

from .base import TEXT_FLOOR, Citation, ExtractedDocument
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

# DOIs: the ISO/Crossref shape. The trailing-character class deliberately
# excludes the punctuation that ends a sentence, because "10.1234/foo." is a DOI
# followed by a full stop far more often than it is a DOI ending in one — and a
# resolver handed the wrong string reports "not found", which reads as missing
# rather than as malformed.
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

#: Meta tags in which a page declares *its own* DOI, in rough order of how
#: reliably publishers populate them. Distinct from the DOIs in its reference
#: list: this one makes the source itself resolvable, and `sources.doi` is the
#: column for it.
_DOI_META_NAMES = ("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi", "doi")

#: arXiv mints a DataCite DOI of this shape for every paper it hosts, and
#: publishes no `citation_doi` meta tag at all. Without this rule every arXiv
#: abstract page lists its own DOI among the works it cites, and `sources.doi`
#: stays null for the single largest population of papers in this corpus.
_ARXIV_DOI_PREFIX = "10.48550/arxiv."

_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def extract_html(
    content: bytes | str,
    url: str,
    *,
    browser_payload: dict[str, Any] | None = None,
) -> ExtractedDocument:
    """Extract text, metadata and citations from one HTML document.

    ``browser_payload`` is Crawl4AI's result dict when the browser path ran.
    Both paths extract with the same tool at the same precision — see
    :func:`_from_browser` for why that is not what this used to do.
    """
    html_text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content

    if browser_payload:
        document = _from_browser(browser_payload, url, content)
    else:
        document = _from_html(content, url)

    links = document.links or _links_from_html(html_text, url)
    own_doi = _own_doi(html_text, url)
    citations = _citations(document.text, links, exclude=_self_identifiers(own_doi, url))

    return dataclasses.replace(
        document,
        links=links,
        citations=citations,
        doi=own_doi,
        # From the raw HTML rather than the extracted text: precision filtering
        # strips `<figure>` wrappers along with the rest of the page furniture,
        # so by the time trafilatura is done the markup that identified a figure
        # as a figure is gone (`P1-10`).
        figures=figures_from_html(content, url),
        # A page can name its own language more reliably than a guess from a
        # paragraph of it, and the extractors often decline to say.
        language=document.language or _language_from_html(html_text),
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

    This used to take `fit_markdown` as-is, on the reasoning that
    `PruningContentFilter` had seen a rendered DOM this process never had. The
    premise was wrong: the rendered HTML comes back in the same response and is
    exactly what ``content`` already holds (see `fetch.py`, which stores it as
    the body), so trafilatura can see everything the filter saw.

    What the premise cost was an asymmetry nobody chose. `PruningContentFilter`
    is far more permissive than trafilatura at ``favor_precision``, so whether a
    page kept its navigation depended on whether the fetcher happened to
    escalate it to a browser — a decision made on how much visible text the
    static fetch found, which has nothing to do with how much boilerplate the
    page carries. It is visible in the corpus as chunks that are repeated
    station lists, promo banners and footer link blocks.

    That is expensive twice over: boilerplate becomes entities and entities
    become edges (§2.3), and it also inflates the novelty gate's duplicate count
    with text that was never content, because every page on a site repeats the
    same chrome.

    So trafilatura extracts the text from the rendered HTML, and the payload
    contributes what it is genuinely better at: metadata read from the rendered
    DOM, and links including the ones JavaScript inserted. `fit_markdown` stays
    as the fallback for pages trafilatura finds nothing in, which is a real case
    on JS-assembled pages with no semantic structure to detect — the browser was
    escalated to for a reason.
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
        # trafilatura found nothing usable and the browser did. Below the floor
        # this branch is not worth taking: a sub-200-character `fit_markdown` is
        # a cookie banner, and admitting it as content is what the floor exists
        # to prevent.
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

    ``favor_precision`` deliberately. The alternative trades a lower chance of
    dropping body text for a higher chance of keeping navigation, and in this
    corpus the second error is the expensive one: boilerplate becomes entities,
    entities become edges, and unpicking a graph full of menu items costs far
    more than the paragraph that precision lost.

    Bytes are passed through rather than decoded here — trafilatura does its own
    encoding detection, and a page that lied in its `Content-Type` is better
    handled by the library that expects to be lied to.
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

    return ExtractedDocument(
        extractor="trafilatura",
        text=_normalise(fields.get("text") or ""),
        title=_clean(fields.get("title")),
        author=_clean(fields.get("author")),
        publisher=_clean(fields.get("sitename")) or _clean(fields.get("hostname")),
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

    Anchors specifically, not `iterlinks()`. That yields every URL in the
    document — favicons, stylesheets, scripts, apple-touch-icons — and on a real
    government home page the assets outnumber the documents several times over.
    A frontier fed from it spends its budget fetching a 180-byte PNG per page.
    """
    try:
        tree = lxml_html.fromstring(html_text)
    except (etree.ParserError, ValueError):
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

    Separated from the reference list because they answer different questions
    and the schema keeps them apart: `sources.doi` is what makes this source
    resolvable, while a cited DOI is a pointer to something else to fetch
    (`P1-14`). Only the head is searched — a `citation_doi` tag halfway down a
    reference list is describing a reference, not the page.

    The URL is the fallback, and it exists for arXiv: it publishes no DOI meta
    tag, so the DataCite DOI it mints appears only in the body, where it reads
    as a citation. Deriving it from the identifier already in the URL is exact
    rather than a guess — the mapping is arXiv's own and mechanical.
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

    A paper's abstract page carries its own DOI and its own arXiv ID, often
    several times and in several versions. Left in, every source in the corpus
    cites itself — and `P1-14` spends a resolution attempt per paper fetching
    the document it already has.
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

    Both, because the two carry different halves: a reference list writes DOIs
    as text, while a "view on arXiv" button carries the identifier only in an
    `href`. Taking one and not the other misses a predictable population of
    papers rather than a random sample of them.
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


def _language_from_html(html_text: str) -> str | None:
    """`<html lang="...">`, trimmed to the primary subtag.

    Regional variants are more precision than anything downstream uses, and
    splitting `en` from `en-GB` would fragment a language filter for no gain.
    """
    match = re.search(r"<html[^>]*\blang\s*=\s*[\"']([a-zA-Z]{2,3})", html_text[:4000])
    return match.group(1).lower() if match else None


def _as_date(value: str | None) -> dt.date | None:
    """A publication date, or None. Never a guess.

    trafilatura is happy to return a partial date; anything that is not a full
    ISO day is discarded, because `sources.publication_date` is a DATE and
    inventing the missing components would put a fabricated day in a column
    citations are built from.
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

    Trailing spaces before a newline are meaningful in markdown — two of them
    are a hard break — but they arrive here from HTML indentation far more often
    than from an author, and they make chunk boundaries (`P2-02`) depend on the
    source's formatting rather than on its prose.
    """
    return _WS_RE.sub("\n", text).strip()
