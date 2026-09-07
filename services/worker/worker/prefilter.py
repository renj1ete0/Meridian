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
from collections.abc import Iterable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.logging import get_logger
from meridian_core.models import FetchPolicy, Source
from meridian_core.queueing import already_queued
from meridian_core.tiering import registrable_domain

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
    dropped: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def considered(self) -> int:
        return len(self.kept) + sum(self.dropped.values())


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
        correctly, since `datamall.lta.gov.sg` is a distinct source from
        `lta.gov.sg` and tiering depends on telling them apart — so an exact
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
            if self.is_blocked(normalised):
                drop("blocked_domain")
                continue
            if normalised in candidates:
                drop("duplicate_on_page")
                continue
            candidates[normalised] = None

        if not candidates:
            return Verdict(dropped=dropped)

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

        return Verdict(kept=tuple(kept), dropped=dropped)

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
