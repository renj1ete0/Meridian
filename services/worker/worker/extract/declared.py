"""What an HTML page declares about itself: author, publisher, date, title, language (B-234).

Read from the head's meta tags (Highwire `citation_*`, Dublin Core, Open Graph `article:*`,
plain `<meta name="author">`), from the JSON-LD the page gives for itself, and from
`itemprop="datePublished"`. Each value carries the name of where it came from, which the
caller records as provenance. Nothing is inferred from the text here; trafilatura's
heuristics are the fallback, judged more strictly. See
docs/features/extraction.md#source-metadata.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from typing import Any

from lxml import etree
from lxml import html as lxml_html

from meridian_core.bibliography import join_authors, judge_publisher, parse_day

#: How much of a page to search for JSON-LD and meta tags, so a huge page costs a bounded scan.
SCAN_CHARS = 400_000

#: Meta names for each field, by source, in the order they are trusted. A scholarly
#: publisher's `citation_*` tags are written for indexers and are the most reliable.
DATE_META = (
    ("citation", ("citation_publication_date", "citation_date", "citation_online_date")),
    ("article", ("article:published_time", "og:article:published_time", "og:published_time")),
    (
        "dc",
        (
            "dcterms.issued",
            "dc.date.issued",
            "dcterms.date",
            "dc.date",
            "dcterms.created",
            "dc.date.created",
            "prism.publicationdate",
        ),
    ),
    (
        "meta",
        (
            "date",
            "pubdate",
            "publishdate",
            "publish-date",
            "publish_date",
            "publication_date",
            "sailthru.date",
            "parsely-pub-date",
        ),
    ),
)
AUTHOR_META = (
    ("citation", ("citation_author",)),
    ("dc", ("dc.creator", "dcterms.creator")),
    ("meta", ("author", "article:author", "parsely-author", "sailthru.author")),
)
PUBLISHER_META = (
    ("citation", ("citation_publisher", "citation_technical_report_institution")),
    ("dc", ("dc.publisher", "dcterms.publisher")),
    ("meta", ("og:site_name",)),
)
TITLE_META = (
    ("citation", ("citation_title",)),
    ("dc", ("dc.title", "dcterms.title")),
)
LANGUAGE_META = (
    ("dc", ("dc.language", "dcterms.language")),
    ("meta", ("content-language", "language", "og:locale")),
)

#: JSON-LD types that describe something other than the page: the site, its owner, a
#: person, an image, the breadcrumb trail.
_NOT_THE_PAGE = frozenset(
    {
        "website",
        "organization",
        "newsmediaorganization",
        "governmentorganization",
        "educationalorganization",
        "corporation",
        "person",
        "imageobject",
        "breadcrumblist",
        "listitem",
        "searchaction",
        "sitenavigationelement",
        "wpheader",
        "wpfooter",
        "videoobject",
        "place",
        "postaladdress",
        "contactpoint",
    }
)
#: JSON-LD types that describe a page and nothing more specific; an article inside one wins.
_GENERIC_PAGE = frozenset({"webpage", "collectionpage", "itempage", "searchresultspage"})

_JSON_LD = re.compile(
    r"<script\b[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_LANG = re.compile(r"^([a-zA-Z]{2,3})(?:[-_][a-zA-Z0-9]+)*$")


@dataclasses.dataclass(frozen=True)
class Found[T]:
    """A declared value and where on the page it was declared."""

    value: T
    source: str


@dataclasses.dataclass(frozen=True)
class Declared:
    """Everything a page declares about itself; None where it declares nothing usable."""

    title: Found[str] | None = None
    author: Found[str] | None = None
    publisher: Found[str] | None = None
    publication_date: Found[dt.date] | None = None
    language: Found[str] | None = None
    generator: str | None = None


def read_declared(html_text: str) -> Declared:
    """What ``html_text`` declares about itself. Never raises on a malformed page."""
    text = html_text[:SCAN_CHARS]
    meta = _meta(text)
    items = _page_items(text)
    generator = (meta.get("generator") or [None])[0]
    return Declared(
        title=_first_text(meta, TITLE_META) or _from_items(items, ("headline",), "jsonld"),
        author=_author(meta, items),
        publisher=_publisher(meta, items, generator),
        publication_date=_date(meta, items, text),
        language=_language(meta, items),
        generator=generator,
    )


# --------------------------------------------------------------------------
# Meta tags
# --------------------------------------------------------------------------


def _meta(text: str) -> dict[str, list[str]]:
    """Head meta tags by lower-cased name, property or http-equiv; values in order.

    Only the head: a `citation_author` in the body describes a reference. Microdata
    `datePublished` may sit anywhere, so it is kept only when every one on the page
    agrees; a listing marks up each item it lists.
    """
    try:
        tree = lxml_html.fromstring(text)
    except etree.ParserError, ValueError:
        return {}
    found: dict[str, list[str]] = {}
    head = tree.find("head")
    for tag in head.iter("meta") if head is not None else ():
        content = " ".join((tag.get("content") or "").split())
        if not content:
            continue
        for attr in ("name", "property", "http-equiv"):
            key = (tag.get(attr) or "").strip().lower()
            if key:
                found.setdefault(key, []).append(content)
    published = {
        (tag.get("datetime") or tag.get("content") or "").strip()
        for tag in tree.iter("time", "meta")
        if (tag.get("itemprop") or "").lower() == "datepublished"
    }
    published.discard("")
    if len(published) == 1:
        found["itemprop:datepublished"] = list(published)
    return found


def _first_text(meta: dict[str, list[str]], table) -> Found[str] | None:
    for source, names in table:
        for name in names:
            for value in meta.get(name, ()):
                return Found(value, source)
    return None


# --------------------------------------------------------------------------
# JSON-LD
# --------------------------------------------------------------------------


def _types(item: dict[str, Any]) -> set[str]:
    declared = item.get("@type")
    values = declared if isinstance(declared, list) else [declared]
    return {
        v.strip().rstrip("/").rsplit("/", 1)[-1].lower()
        for v in values
        if isinstance(v, str) and v.strip()
    }


def _page_items(text: str) -> list[tuple[dict[str, Any], dict[str, dict[str, Any]]]]:
    """The JSON-LD items describing the page, most specific first, each with its id map.

    An `@id` reference (an author given as `{"@id": "#person-1"}`) is resolved against
    the other items of the same block.
    """
    specific: list[tuple[dict[str, Any], dict[str, dict[str, Any]]]] = []
    generic: list[tuple[dict[str, Any], dict[str, dict[str, Any]]]] = []
    for block in _JSON_LD.finditer(text):
        try:
            data = json.loads(block.group(1), strict=False)
        except ValueError:
            continue
        items = _flatten(data)
        ids = {i["@id"]: i for i in items if isinstance(i.get("@id"), str)}
        for item in items:
            types = _types(item)
            if not types or types & _NOT_THE_PAGE:
                continue
            (generic if types <= _GENERIC_PAGE else specific).append((item, ids))
    return specific + generic


def _flatten(data: Any) -> list[dict[str, Any]]:
    """Top-level items, list members and `@graph` members; not nested values."""
    if isinstance(data, list):
        return [item for entry in data for item in _flatten(entry)]
    if not isinstance(data, dict):
        return []
    graph = data.get("@graph")
    return [data, *(_flatten(graph) if graph is not None else [])]


def _names(value: Any, ids: dict[str, dict[str, Any]], depth: int = 0) -> list[str]:
    """The names a JSON-LD author or publisher value gives: strings, objects, references."""
    if depth > 3 or value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [n for v in value for n in _names(v, ids, depth + 1)]
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return [name]
        ref = value.get("@id")
        if isinstance(ref, str) and ref in ids and ids[ref] is not value:
            return _names(ids[ref], ids, depth + 1)
    return []


def _from_items(items, keys: tuple[str, ...], source: str) -> Found[str] | None:
    for item, _ids in items:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return Found(" ".join(value.split()), source)
    return None


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------


def _date(meta: dict[str, list[str]], items, text: str) -> Found[dt.date] | None:
    order = [DATE_META[0], ("jsonld", ()), *DATE_META[1:], ("microdata", ())]
    for source, names in order:
        if source == "jsonld":
            for item, _ids in items:
                if (day := parse_day(item.get("datePublished"))) is not None:
                    return Found(day, "jsonld")
            continue
        if source == "microdata":
            for value in meta.get("itemprop:datepublished", ()):
                if (day := parse_day(value)) is not None:
                    return Found(day, "microdata")
            continue
        for name in names:
            for value in meta.get(name, ()):
                if (day := parse_day(value)) is not None:
                    return Found(day, source)
    return None


def _author(meta: dict[str, list[str]], items) -> Found[str] | None:
    citation = join_authors(meta.get("citation_author", []))
    if citation:
        return Found(citation, "citation")
    for item, ids in items:
        if names := join_authors(_names(item.get("author"), ids)):
            return Found(names, "jsonld")
    for source, names in AUTHOR_META[1:]:
        for name in names:
            if joined := join_authors(meta.get(name, [])):
                return Found(joined, source)
    return None


def _publisher(meta: dict[str, list[str]], items, generator: str | None) -> Found[str] | None:
    candidates: list[tuple[str, str]] = []
    for source, names in PUBLISHER_META[:2]:
        candidates += [(v, source) for name in names for v in meta.get(name, [])]
    for item, ids in items:
        candidates += [(n, "jsonld") for n in _names(item.get("publisher"), ids)]
    for name in PUBLISHER_META[2][1]:
        candidates += [(v, "meta") for v in meta.get(name, [])]
    for value, source in candidates:
        if (kept := judge_publisher(value, generator=generator).value) is not None:
            return Found(kept, source)
    return None


def _language(meta: dict[str, list[str]], items) -> Found[str] | None:
    candidates: list[tuple[str, str]] = []
    for source, names in LANGUAGE_META:
        candidates += [(v, source) for name in names for v in meta.get(name, [])]
    for item, _ids in items:
        value = item.get("inLanguage")
        if isinstance(value, str):
            candidates.append((value, "jsonld"))
    for value, source in candidates:
        # "en-GB, fr" declares two; the first is the page's.
        match = _LANG.match(value.split(",")[0].strip())
        if match:
            return Found(match.group(1).lower(), source)
    return None
