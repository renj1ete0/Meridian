"""Sitemap discovery and parsing (`P1-28`, §6.4).

A sitemap is untrusted XML: a DTD is refused before parsing and entities are not
expanded. Only same-site URLs are accepted, and an index's entries become further
`sitemap` tasks rather than being followed inline. See docs/features/discovery.md#sitemaps.

RSS and Atom feeds are read by the same parser (`B-249`), and each entry's headline is kept
where the document gives one (a news sitemap's ``news:title``, a feed item's ``title``).
"""

from __future__ import annotations

import dataclasses
import io
import re
from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

from lxml import etree

from meridian_core.tiering import registrable_domain

__all__ = [
    "MAX_SITEMAP_ENTRIES",
    "ParsedSitemap",
    "SitemapError",
    "parse_sitemap",
    "same_site",
]

#: sitemaps.org's own limit. A document naming more than this is malformed by
#: its own specification, and the cap is what stops one queue row from becoming
#: an unbounded number of them.
MAX_SITEMAP_ENTRIES = 50_000

#: Refused outright, anywhere in the scanned window: no legitimate sitemap contains
#: either string.
_DTD_MARKERS = (b"<!doctype", b"<!entity")

#: Everything else about a fetch is bounded by policy — `max_page_bytes`, the
#: decompression ratio, the timeout. This is the one bound that is ours.
_MAX_LOC_LENGTH = 4096

_SCHEMES = frozenset({"http", "https"})

#: Matched by local name: sitemaps in the wild get the namespace wrong, omit it,
#: or use the 0.84 URL.
_LOCALNAME = re.compile(r"\{[^}]*\}")


class SitemapError(ValueError):
    """A sitemap that cannot be read.

    Carries a short machine-ish ``reason`` because the caller settles a queue
    task with it, and "why did this abandon" is answered from the queue row
    rather than from a log line nobody kept.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclasses.dataclass(frozen=True)
class ParsedSitemap:
    """What one sitemap document yielded.

    ``kind`` decides what the URLs are: a ``urlset``'s become ``url`` tasks, a
    ``sitemapindex``'s further ``sitemap`` tasks.
    """

    kind: str
    urls: tuple[str, ...] = ()
    truncated: bool = False
    dropped: Mapping[str, int] = dataclasses.field(default_factory=dict)
    #: Each entry's headline, by URL, where the document gave one (`B-249`).
    titles: Mapping[str, str] = dataclasses.field(default_factory=dict)

    @property
    def is_index(self) -> bool:
        return self.kind == "sitemapindex"


#: Roots that are read, and what their entries become. A feed's entries are pages.
_ROOT_KINDS = {
    "urlset": "urlset",
    "sitemapindex": "sitemapindex",
    "rss": "feed",
    "RDF": "feed",
    "feed": "feed",
}

#: The element wrapping one entry, in each kind of document.
_ENTRY_TAGS = frozenset({"url", "sitemap", "item", "entry"})

#: Headlines are for matching against a vocabulary; past this they are not headlines.
_MAX_TITLE_LENGTH = 500

#: Extensions whose ``loc``, ``link`` and ``title`` describe an image, a video or a
#: translation, not the entry: an ``image:title`` is a file name (`B-249`).
_FOREIGN = ("sitemap-image", "sitemap-video", "/mrss", "xhtml")

#: A news sitemap's headline, which wins over any other title in the entry.
_NEWS = "sitemap-news"


def _namespace(tag: object) -> str:
    """The namespace of an element's tag, or "" when it has none."""
    return tag[1 : tag.index("}")] if isinstance(tag, str) and tag.startswith("{") else ""


def local_name(tag: object) -> str:
    """The tag without its namespace, or "" for comments and PIs.

    lxml gives non-element nodes a callable ``tag``, which is why this checks
    the type rather than assuming a string.
    """
    if not isinstance(tag, str):
        return ""
    return _LOCALNAME.sub("", tag)


def same_site(a: str, b: str) -> bool:
    """Do these two URLs belong to the same site?

    Suffix matching in both directions: an apex may name its subdomains and a
    subdomain its apex; neither may name a stranger.
    """
    host_a, host_b = registrable_domain(a), registrable_domain(b)
    if not host_a or not host_b:
        return False
    return host_a == host_b or host_a.endswith(f".{host_b}") or host_b.endswith(f".{host_a}")


def parse_sitemap(
    data: bytes,
    *,
    base_url: str,
    max_entries: int = MAX_SITEMAP_ENTRIES,
    same_site_only: bool = True,
) -> ParsedSitemap:
    """Read one sitemap or sitemap index.

    Raises :class:`SitemapError` for anything unreadable: a DTD, a body that is not
    XML, a root that is neither ``urlset`` nor ``sitemapindex``. Bad entries are
    dropped and counted.
    """
    if not data.strip():
        raise SitemapError("empty", "no body")

    lowered = data[: _dtd_scan_window(data)].lower()
    for marker in _DTD_MARKERS:
        if marker in lowered:
            # Before the parse, not after: the allocation is the attack.
            raise SitemapError("dtd_refused", marker.decode())

    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    kind = ""
    urls: dict[str, None] = {}
    titles: dict[str, str] = {}
    truncated = False
    # The entry being read: its address and headline, set by its children and taken
    # when it closes. Outside an entry (a feed's channel title and link) nothing is.
    in_entry = False
    entry_url: str | None = None
    entry_title: str | None = None

    # iterparse with `.clear()` keeps the peak to one element. `resolve_entities=False`
    # is the second entity defence, behind the pre-scan.
    context = etree.iterparse(
        io.BytesIO(data),
        events=("start", "end"),
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
        collect_ids=False,
    )

    try:
        for event, element in context:
            name = local_name(element.tag)

            if event == "start":
                if not kind:
                    if name not in _ROOT_KINDS:
                        raise SitemapError("not_a_sitemap", name or "unknown root")
                    kind = _ROOT_KINDS[name]
                elif name in _ENTRY_TAGS:
                    in_entry, entry_url, entry_title = True, None, None
                continue

            if not in_entry:
                continue

            space = _namespace(element.tag)
            if any(marker in space for marker in _FOREIGN):
                continue

            if name in ("loc", "link") and entry_url is None and _NEWS not in space:
                # Atom's address is an attribute, preferring the page over its
                # enclosures and comments; RSS's and a sitemap's is the text.
                rel = element.get("rel")
                raw = element.get("href") if element.get("href") is not None else element.text
                if rel in (None, "alternate"):
                    entry_url = raw if raw is not None else ""
                element.clear()
                continue

            if name == "title":
                text = " ".join((element.text or "").split())
                if text and (entry_title is None or _NEWS in space):
                    entry_title = text[:_MAX_TITLE_LENGTH]
                element.clear()
                continue

            if name not in _ENTRY_TAGS:
                continue

            # The entry closed: take it.
            in_entry = False
            element.clear()

            if len(urls) >= max_entries:
                truncated = True
                break

            candidate = _clean_loc(entry_url, base_url=base_url)
            if candidate is None:
                drop("unusable")
                continue
            if same_site_only and not same_site(candidate, base_url):
                # Not an error on the site's part — sitemaps.org allows it. It
                # is a frontier we decline to let a stranger write to.
                drop("cross_site")
                continue
            if candidate in urls:
                drop("duplicate")
                continue
            urls[candidate] = None
            if entry_title:
                titles[candidate] = entry_title
    except etree.XMLSyntaxError as exc:
        # A truncated or malformed document. Anything already read is still
        # good — a sitemap cut off halfway is a partial frontier, not a lost
        # one — so this returns what it has rather than discarding it.
        if not kind:
            raise SitemapError("malformed_xml", str(exc).split("\n", 1)[0]) from exc
        drop("malformed_tail")
    finally:
        del context

    if not kind:
        raise SitemapError("not_a_sitemap", "no root element")

    return ParsedSitemap(
        kind=kind, urls=tuple(urls), truncated=truncated, dropped=dict(dropped), titles=titles
    )


def _dtd_scan_window(data: bytes) -> int:
    """How much of the document the DTD pre-scan reads.

    The whole thing unless it is large; then a generous window, since comments may pad
    ahead of a DOCTYPE.
    """
    return min(len(data), 65_536)


def _clean_loc(raw: str | None, *, base_url: str) -> str | None:
    """One ``<loc>`` as a fetchable absolute URL, or None if it is not one."""
    if not raw:
        return None
    text = raw.strip()
    if not text or len(text) > _MAX_LOC_LENGTH:
        return None

    # Relative locs are invalid per the specification and appear anyway. Joining
    # against the sitemap's own URL is what the site meant, and is safe because
    # the result is re-checked for scheme and site below.
    absolute = urljoin(base_url, text)

    split = urlsplit(absolute)
    if split.scheme not in _SCHEMES or not split.hostname:
        # javascript:, data:, file: and mailto: all reach here from a hostile
        # sitemap. netguard would refuse them later; refusing now keeps them out
        # of the queue rather than into it and out again.
        return None
    return absolute
