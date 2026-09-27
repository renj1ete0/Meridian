"""What is worth putting in the queue (task P1-06, spec §6.4, §6.1).

Every link on every page is a candidate, and most of them are not worth a
request. §6.4 gives the reason plainly: the novelty gate catches duplicates
afterwards, but *not fetching them is cheaper* — a duplicate that never gets
fetched costs nothing, and one that does costs a request, a page of bandwidth,
an extraction, and a slot in a rate limiter some other domain wanted.

Four gates, cheapest first, and the order is the design:

1. **Normalise.** Two spellings of one URL are two queue rows, two fetches and
   two source records, and the duplicate is invisible until someone counts. This
   is free and it makes every check below actually work.
2. **Shape.** Scheme, host, and an extension allowlist. A `.jpg` link is not a
   document this corpus can read, and queueing it buys a `content_type_rejected`
   at the cost of a real request.
3. **Blocklist.** A domain the policy says is blocked, or one seeded as never
   worth following. Social platforms and link shorteners appear on every
   government page and lead nowhere a research corpus wants to go.
4. **Already seen.** One query for the whole batch against `queue` and one
   against `sources`.
5. **Refused by robots.txt** (`B-90`), when the crawl already holds a fresh
   copy of the file. Nothing is fetched here: an origin never visited is
   waved through and asked at fetch time as before. What this stops is a
   search engine returning the same refusing site's pages every day, each of
   which took a claim only to be refused.

The database queries come last because they are the expensive ones, and by the
time a batch of 500 links reaches them it is usually a batch of 40.

**Normalisation stays conservative.** Anything that changes *which resource is
requested* trades duplicates for missing pages, and a missing page is invisible
in a way a duplicate is not. So the fragment goes, tracking parameters go, the
host is lowercased and a default port dropped — and a trailing slash stays,
because `/a` and `/a/` are different resources in principle and the cost of
being wrong is a page that silently never enters the corpus.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections.abc import Iterable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core import robotscache
from meridian_core.logging import get_logger
from meridian_core.models import FetchPolicy, Source
from meridian_core.policy import resolve_policy
from meridian_core.queueing import already_queued
from meridian_core.tiering import registrable_domain

from .robots import parse as parse_robots
from .robots import robots_url

log = get_logger(__name__)

#: Schemes the fetcher can handle. `netguard` enforces this again at fetch time;
#: here it saves the queue row rather than the request.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Query parameters that identify a *referral*, not a resource. Stripping them
#: is what makes one campaign-tagged link and one bare link the same URL, which
#: is most of what already-seen is up against on a real page.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "gclid",
        "gclsrc",
        "dclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "referrer",
        "source",
        "_ga",
        "_gl",
        "yclid",
        "wt_mc",
        "s_cid",
        "cmpid",
    }
)

#: File extensions that are never a document worth extracting. Checked on the
#: path so the queue row is never created; `allowed_content_types` catches the
#: rest at fetch time, but only after paying for the request.
SKIP_EXTENSIONS = frozenset(
    {
        # images
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".bmp",
        ".tiff",
        ".avif",
        # media
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4a",
        ".m4v",
        ".ogg",
        ".wav",
        # archives and binaries
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".iso",
        ".dmg",
        ".exe",
        ".msi",
        ".deb",
        ".rpm",
        ".apk",
        ".pkg",
        ".bin",
        # assets
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
    }
)

#: Hosts that resolve an identifier rather than serving a document (`B-23`).
#:
#: A `doi.org` URL is a DOI wearing a URL's clothes. Fetching it follows a
#: redirect to a publisher, which is usually a paywall, a consent wall or a
#: landing stub — measured on the first real corpus: 244 such rows, 134 of them
#: with no extractable text at all. Meanwhile the citation channel was already
#: queueing the *same* identifiers correctly, as `doi` tasks that the resolver
#: turns into open-access copies. The frontier was simply asking the wrong
#: question about the same thing.
#:
#: So these are routed, not dropped — see `Verdict.dois`.
IDENTIFIER_HOSTS = frozenset({"doi.org", "dx.doi.org"})

#: Path segments that mean "this page is about the website" (`B-23`).
#:
#: Not a blocklist of topics — a shape. A corpus of research documents has no
#: use for a site's contact form, and one real run indexed 106 pages of a
#: single site's help section, which is a tenth of everything it fetched that
#: day. Checked as a whole path segment, so `/about/` matches and
#: `/about-congestion-pricing` does not: the second is an article and the
#: distinction is the entire reason this is a segment match rather than a
#: substring one.
FURNITURE_SEGMENTS = frozenset(
    {
        "about",
        "contact",
        "help",
        "faq",
        "faqs",
        "support",
        "privacy",
        "terms",
        "legal",
        "cookies",
        "accessibility",
        "login",
        "signin",
        "register",
        "subscribe",
        "newsletter",
        "careers",
        "jobs",
        "advertise",
        "donate",
        "cart",
        "checkout",
    }
)

#: Whole-segment phrases that mean the same thing as `FURNITURE_SEGMENTS`
#: (`B-42`). The single-word rule caught `/privacy` and let `/privacy-policy`,
#: `/terms-of-use`, `privacy_policy.html` and `/contact-us/` through — every one
#: of which reached a real corpus. A segment matches only when its *entire*
#: word sequence is one of these, so `/terms-of-reference-for-the-review` and
#: `/contact-lens-associated-problems` stay documents.
FURNITURE_PHRASES = frozenset(
    {
        ("privacy", "policy"),
        ("privacy", "notice"),
        ("privacy", "statement"),
        ("privacy", "request"),
        ("terms", "of", "use"),
        ("terms", "of", "service"),
        ("terms", "and", "conditions"),
        ("terms", "conditions"),
        ("cookie", "policy"),
        ("cookies", "policy"),
        ("contact", "us"),
        ("accessibility", "statement"),
        ("accessibility", "assistance"),
        ("web", "accessibility"),
        ("site", "map"),
        ("sitemap",),
        ("legal", "notice"),
        ("disclaimer",),
        ("copyright",),
        ("imprint",),
        ("impressum",),
    }
)

#: Last segments that are a search engine's result page when they carry a
#: query (`B-42`). A query-less `/results` is a section — often a study's
#: findings — and stays a document.
SEARCH_SEGMENTS = frozenset({"search", "results", "advanced-search", "advancedsearch"})

#: Page suffixes stripped before a segment is read as words.
_PAGE_SUFFIX = re.compile(r"\.(html?|php|aspx?|cfm|jsp)$")
_WORD_SPLIT = re.compile(r"[-_\s]+")

#: Default ports, dropped so `https://x.test:443/a` and `https://x.test/a` are
#: one URL rather than two.
_DEFAULT_PORTS = {"http": "80", "https": "443"}


@dataclasses.dataclass(frozen=True)
class Verdict:
    """Why a URL was dropped, for the log line that explains a thin frontier.

    Counted rather than logged per URL: a page with 400 links produces 400
    decisions, and a log that records each one is a log nobody reads.
    """

    kept: tuple[str, ...] = ()
    #: Identifiers found on a redirector host, for the caller to queue as `doi`
    #: tasks. Separate from `kept` because they are a different *kind* of work:
    #: queued as a URL they fetch a redirect, and queued as a DOI they reach
    #: the resolver that finds an open-access copy (`B-23`).
    dois: tuple[str, ...] = ()
    dropped: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def considered(self) -> int:
        return len(self.kept) + len(self.dois) + sum(self.dropped.values())


def normalise_url(url: str) -> str | None:
    """One canonical spelling of ``url``, or None if it is not usable.

    Conservative on purpose — see the module docstring. What is done here cannot
    change which resource is requested; what is left undone (trailing slashes,
    query parameter order, case in the path) is left because it can.
    """
    try:
        split = urlsplit(url.strip())
    except ValueError:
        return None

    scheme = split.scheme.lower()
    if scheme not in ALLOWED_SCHEMES or not split.hostname:
        return None

    host = split.hostname.lower()
    port = split.port
    netloc = host if port is None or str(port) == _DEFAULT_PORTS.get(scheme) else f"{host}:{port}"

    # Credentials in a crawl target are either a mistake or an attempt to get
    # them logged somewhere. Neither is a reason to keep them.
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(split.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
    )
    # The fragment addresses a place inside a document, not a document.
    return urlunsplit((scheme, netloc, split.path or "/", query, ""))


def identifier_for(url: str) -> str | None:
    """The DOI a redirector URL stands for, or None if it is not one.

    Parsed here rather than fetched: the identifier is in the path, and the
    only thing a request to `doi.org` adds is a redirect to somewhere the
    resolver would have reached better.
    """
    # `removeprefix`, not `lstrip("www.")`: the second strips *characters*, so
    # it would turn `wwwdoi.org` into `doi.org` and `dx.doi.org` into
    # `x.doi.org`. Ruff catches this one (B005) and it is worth spelling out,
    # because the wrong version looks right.
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host not in IDENTIFIER_HOSTS:
        return None
    candidate = urlsplit(url).path.lstrip("/")
    return candidate or None


def is_site_furniture(url: str) -> bool:
    """True for a path that is about the website rather than about anything.

    Segment-wise, and only for segments that are *exactly* one of these words.
    A substring rule would drop `/about-congestion-pricing`, which is an
    article, and the two are told apart by nothing else.
    """
    split = urlsplit(url)
    segments = [segment.lower() for segment in split.path.split("/") if segment]
    if any(segment in FURNITURE_SEGMENTS for segment in segments):
        return True
    for segment in segments:
        words = tuple(word for word in _WORD_SPLIT.split(_PAGE_SUFFIX.sub("", segment)) if word)
        if words in FURNITURE_PHRASES or (len(words) == 1 and words[0] in FURNITURE_SEGMENTS):
            return True
    return bool(segments and split.query and segments[-1] in SEARCH_SEGMENTS)


def has_skipped_extension(url: str) -> bool:
    """True for a path ending in something this corpus cannot read as text."""
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    slash = path.rfind("/")
    return dot > slash and path[dot:] in SKIP_EXTENSIONS


class Prefilter:
    """Decides which candidate URLs are worth a queue row.

    Holds the seeded blocklist for the life of the worker. The blocklist is
    config (§13.1) and changes when someone edits it in Admin, not between two
    pages of one crawl; re-reading it per link would be one query per link for
    an answer that does not move.
    """

    def __init__(self, blocked_domains: Iterable[str] = ()) -> None:
        self._blocked = frozenset(registrable_domain(d) for d in blocked_domains if d)

    @property
    def blocked_domains(self) -> frozenset[str]:
        return self._blocked

    def is_blocked(self, url: str) -> bool:
        """True if this URL's host is on the seeded blocklist, or under one.

        Suffix matching, not equality. `registrable_domain` keeps subdomains —
        correctly, since a subdomain is a distinct source from its apex and
        tiering depends on telling them apart — so an exact
        match would block `facebook.com` and wave `m.facebook.com` through,
        which is the same site and the whole reason the entry is there.
        """
        host = registrable_domain(url)
        return any(host == blocked or host.endswith(f".{blocked}") for blocked in self._blocked)

    async def keep(self, sess: AsyncSession, urls: Sequence[str]) -> Verdict:
        """The subset of ``urls`` worth queueing, plus a count of what went.

        Order-preserving and duplicate-free: a page's earlier links are its more
        likely to matter, and the same URL appearing twice on one page must not
        produce two rows.
        """
        dropped: dict[str, int] = {}
        candidates: dict[str, None] = {}
        identifiers: dict[str, None] = {}

        def drop(reason: str) -> None:
            dropped[reason] = dropped.get(reason, 0) + 1

        for url in urls:
            normalised = normalise_url(url)
            if normalised is None:
                drop("unusable")
                continue
            if has_skipped_extension(normalised):
                drop("not_a_document")
                continue
            if (identifier := identifier_for(normalised)) is not None:
                # Routed rather than kept or dropped. The resolver deduplicates
                # against whatever the citation channel already queued, because
                # both arrive as the same bare identifier.
                identifiers[identifier] = None
                continue
            if is_site_furniture(normalised):
                drop("site_furniture")
                continue
            if self.is_blocked(normalised):
                drop("blocked_domain")
                continue
            if normalised in candidates:
                drop("duplicate_on_page")
                continue
            candidates[normalised] = None

        if not candidates:
            return Verdict(dois=tuple(identifiers), dropped=dropped)

        # The two database round-trips, once for the whole batch rather than
        # once per link — the difference between one query and four hundred.
        inactive = await self._inactive_domains(sess, candidates)
        seen = await already_queued(sess, list(candidates)) | await self._known_sources(
            sess, list(candidates)
        )

        kept: list[str] = []
        for url in candidates:
            if registrable_domain(url) in inactive:
                drop("policy_blocked")
            elif url in seen:
                drop("already_seen")
            else:
                kept.append(url)

        # Last, and only over what survived: it parses files, and most of a
        # page's links are gone by now.
        refused = await self._robots_refused(sess, kept)
        if refused:
            kept = [url for url in kept if url not in refused]
            dropped["robots_denied"] = len(refused)

        return Verdict(kept=tuple(kept), dois=tuple(identifiers), dropped=dropped)

    async def _robots_refused(self, sess: AsyncSession, urls: Sequence[str]) -> set[str]:
        """The URLs a fresh cached robots.txt refuses, under their domain's policy.

        The crawler's own reading, not a second one: the same parser, the same
        per-domain user agent, and a domain whose policy does not respect
        robots.txt is never refused here. Only a file that was read counts —
        ``missing`` permits everything, and ``unreachable`` is not the site's
        answer, only the absence of one, which the fetch retries.

        A cache is an optimisation (`robotscache`): any error here keeps every
        URL, inside a savepoint so the caller's transaction survives it.
        """
        by_origin: dict[str, list[str]] = {}
        for url in urls:
            by_origin.setdefault(robots_url(url), []).append(url)
        if not by_origin:
            return set()

        refused: set[str] = set()
        try:
            async with sess.begin_nested():
                cached = await robotscache.load_many(sess, by_origin, now=dt.datetime.now(dt.UTC))
                for origin, entry in cached.items():
                    if entry.outcome != robotscache.OK:
                        continue
                    policy = await resolve_policy(sess, registrable_domain(origin))
                    if not policy.respect_robots:
                        continue
                    rules = parse_robots(entry.body or "", policy.user_agent)
                    refused.update(url for url in by_origin[origin] if not rules.allows(url))
        except Exception:
            log.warning("robots cache unreadable; nothing refused", exc_info=True)
            return set()
        return refused

    async def _inactive_domains(self, sess: AsyncSession, urls: Iterable[str]) -> set[str]:
        """Domains whose `fetch_policy` row says blocked or paused.

        `P1-05` writes these automatically after consecutive failures, so this
        is what stops the frontier re-queueing a dead site every time another
        page links to it.
        """
        domains = {registrable_domain(url) for url in urls}
        rows = await sess.execute(
            select(FetchPolicy.domain).where(
                FetchPolicy.domain.in_(domains), FetchPolicy.status != "active"
            )
        )
        return set(rows.scalars())

    async def _known_sources(self, sess: AsyncSession, urls: Sequence[str]) -> set[str]:
        """URLs already in `sources` — fetched at some point, whatever the queue says.

        Checked as well as the queue because a queue row can be pruned while its
        source record stays, and re-fetching what the corpus already holds is
        precisely what the prefilter exists to prevent.
        """
        rows = await sess.execute(select(Source.url).where(Source.url.in_(list(urls))))
        return set(rows.scalars())
