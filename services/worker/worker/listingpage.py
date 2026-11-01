"""Article links read off a site's latest-articles page, for a listing watch (task `B-255`).

An outlet with no news sitemap or feed still publishes a page of its newest articles. Read
again on the watch's interval, its links are the same answer a feed gives: same-site pages
that look like articles (a date in the path, a long slug, or one of a numbered series under a
shared parent), each with its anchor text as the headline the watch's filter reads.
Navigation, section and tag pages, media files and other sites are left out, and counted.
See docs/features/discovery.md#watched-sources.
"""

from __future__ import annotations

import collections
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import etree
from lxml import html as lxml_html

from meridian_core.boilerplate import host_key

from .sitemaps import ParsedSitemap, SitemapError

#: The most links one read queues; a page past this is an archive, not a front page.
MAX_LINKS = 200

#: A date in a path: `/2026/11/01/`, `/2026-11-01-`, `/20261101`.
_DATED = re.compile(
    r"(?:^|/)(?:19|20)\d{2}(?:[/-](?:0?[1-9]|1[0-2])(?:[/-]|$)|(?:0[1-9]|1[0-2])\d{2})"
)
#: A slug: at least this many words joined by hyphens or underscores, in the last segment
#: or the one before a trailing numeric id.
SLUG_WORDS = 4
_SLUG_SPLIT = re.compile(r"[-_]+")
_ID_SEGMENT = re.compile(r"^\d{4,}$")
#: Segments that make a page navigation however long its slug.
_NAVIGATION = frozenset(
    {
        "tag",
        "tags",
        "topic",
        "topics",
        "category",
        "categories",
        "author",
        "authors",
        "search",
        "page",
        "archive",
        "archives",
        "about",
        "about-us",
        "contact",
        "contact-us",
        "subscribe",
        "login",
        "register",
        "privacy-policy",
        "terms-of-use",
        "terms-and-conditions",
        "video",
        "videos",
        "podcast",
        "podcasts",
        "gallery",
        "photos",
    }
)
#: A numbered series: at least this many links under one parent path, each named with a
#: digit (`/news/media/pr26-67/`), as an agency numbers its releases.
SERIES_MIN = 5
_DIGIT = re.compile(r"\d")
_MEDIA = re.compile(r"\.(jpe?g|png|gif|webp|svg|mp3|mp4|m4a|zip|css|js|xml|json|ics)$", re.I)


def same_site(url: str, site: str) -> bool:
    """Whether ``url`` is on ``site`` (a `host_key`) or one of its subdomains."""
    host = host_key(url) or ""
    return host == site or host.endswith("." + site)


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def is_navigation(url: str) -> bool:
    """A link no rule may take for an article: the home page, a navigation segment, a file."""
    segments = _segments(url)
    return (
        not segments
        or bool({s.lower() for s in segments} & _NAVIGATION)
        or bool(_MEDIA.search(urlsplit(url).path))
    )


def article_reason(url: str) -> str | None:
    """Why a same-site link looks like an article on its own (`dated`, `slug`), or None."""
    if is_navigation(url):
        return None
    split = urlsplit(url)
    segments = _segments(url)
    if _DATED.search(split.path):
        return "dated"
    last = segments[-1]
    candidates = [last, segments[-2]] if _ID_SEGMENT.match(last) and len(segments) > 1 else [last]
    for segment in candidates:
        stem = re.sub(r"\.(html?|php|aspx?)$", "", segment, flags=re.I)
        words = [w for w in _SLUG_SPLIT.split(stem) if w]
        if len(words) >= SLUG_WORDS and sum(w.isalpha() for w in words) >= SLUG_WORDS - 1:
            return "slug"
    return None


def _series(urls) -> set[str]:
    """Links that are one of a numbered series under a shared parent (see `SERIES_MIN`)."""
    by_parent: dict[str, list[str]] = collections.defaultdict(list)
    for url in urls:
        segments = _segments(url)
        if len(segments) >= 2 and _DIGIT.search(segments[-1]):
            by_parent["/".join([urlsplit(url).netloc, *segments[:-1]]).lower()].append(url)
    return {url for group in by_parent.values() if len(group) >= SERIES_MIN for url in group}


def _anchor_text(element) -> str:
    text = " ".join(element.text_content().split())
    return text or (element.get("title") or "").strip()


def parse_listing(content: bytes | str, *, base_url: str) -> ParsedSitemap:
    """The article links on one listing page, as a sitemap read would yield them.

    Raises `SitemapError` for something that is not an HTML page, so the read is abandoned
    as an unreadable sitemap is. Links keep their first anchor text; fragments are dropped.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    if not content.strip() or "<" not in content[:2000]:
        raise SitemapError("not an HTML page")
    try:
        tree = lxml_html.fromstring(content)
    except (etree.ParserError, ValueError) as exc:
        raise SitemapError(f"not an HTML page: {exc}") from exc

    site = host_key(base_url)
    if not site:
        raise SitemapError(f"no host in {base_url!r}")
    dropped: collections.Counter[str] = collections.Counter()
    # Every same-site candidate in page order, with its longest anchor text: an outlet often
    # links a story several times (image, section label, headline), and the headline is longest.
    found: dict[str, str] = {}
    for anchor in tree.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        split = urlsplit(absolute)
        if split.scheme not in ("http", "https"):
            continue
        url = urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))
        if url in found:
            found[url] = max(found[url], _anchor_text(anchor), key=len)
            continue
        if not same_site(url, site):
            dropped["other_site"] += 1
            continue
        if is_navigation(url):
            dropped["not_an_article"] += 1
            continue
        found[url] = _anchor_text(anchor)

    series = _series(found)
    urls: list[str] = []
    truncated = False
    for url in found:
        if article_reason(url) is None and url not in series:
            dropped["not_an_article"] += 1
            continue
        if len(urls) >= MAX_LINKS:
            truncated = True
            break
        urls.append(url)
    titles = {url: found[url] for url in urls}
    return ParsedSitemap(
        kind="listing",
        urls=tuple(urls),
        truncated=truncated,
        dropped=dict(dropped),
        titles={u: t for u, t in titles.items() if t},
    )
