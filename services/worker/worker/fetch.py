"""Fetching a URL, safely (spec §6.4, §11.8; tasks P1-03, P1-21, P1-24).

Two paths, one policy. Static content goes over plain HTTP with ``httpx``;
genuinely JS-dependent pages go to Crawl4AI's browser. `render_js: auto` decides
per page, because Playwright + Chromium against 16GB shared is the single
heaviest thing this node can do, and most of the corpus — government PDFs,
academic pages, statistical releases — needs none of it (§6.4).

**The fetcher connects to the address it validated.** ``netguard`` resolves a
hostname and judges the addresses, but a normal HTTP client then does its *own*
DNS lookup when it opens the socket, and nothing says the second answer matches
the first. That gap is not theoretical: it is precisely what DNS rebinding
exploits, and a crawler following links out of untrusted pages is the ideal
victim. So the request goes to the validated IP literal, with ``Host`` and TLS
SNI set to the original hostname — the name is still what the certificate is
checked against, but the socket cannot be steered elsewhere between the check
and the connection.

Redirects are followed by hand for the same reason. ``follow_redirects=False``
is not caution about redirects as such; it is that a client-followed redirect
resolves and connects without ever handing the new URL back for judgement. Each
hop is re-validated, re-pinned, and re-connected here.

What this still does not close, honestly: the browser path. Crawl4AI does its
own DNS and its own connecting inside its own container, so a URL handed to it
is validated but not pinned. 0.9.2 ships an egress pinning proxy of its own,
which helps, but the defence that actually survives an application bug is
`P1-25` — giving the fetching process no route to private address space at all.

Every refusal returns a :class:`FetchResult` rather than raising, and every
outcome is a ``fetch_attempts.outcome`` value. A crawler that runs unattended
for weeks needs its refusals counted, not caught and swallowed at some call site
that decided they were unremarkable.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import os
import re
import time
import zlib
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from meridian_core.logging import get_logger
from meridian_core.netguard import (
    DNS_REASONS,
    BlockedTarget,
    IPAddress,
    Resolver,
    assert_redirect_allowed,
    assert_url_allowed,
    check_https_final,
)
from meridian_core.policy import ResolvedPolicy
from meridian_core.tiering import registrable_domain

log = get_logger(__name__)

# A gzip of ordinary HTML lands around 5:1, and a repetitive page can reach 20:1
# honestly, so the ratio only starts being evidence once there is real volume
# behind it. Below this floor a 100:1 ratio is a 10KB file, not an attack, and
# refusing it would drop legitimate pages. A decompression bomb clears the floor
# in its first few chunks.
RATIO_FLOOR_BYTES = 1_048_576

# Visible characters below which a page is treated as possibly JS-rendered. A
# real article clears this in its first paragraph; an empty SPA shell never does.
JS_TEXT_FLOOR = 500

# How much a single decompression step may produce before the caps get to
# look again. Small enough that a bomb cannot allocate its way past the
# check, large enough that an ordinary page costs a handful of steps.
_INFLATE_STEP_BYTES = 1 << 20

# Response headers worth keeping for the conditional request next time round
# (§6.4 `conditional_requests`), and for extraction to know what it is holding.
_KEPT_HEADERS = (
    "content-type",
    "etag",
    "last-modified",
    "content-length",
    "content-encoding",
    # Not a validator. Kept because it is what tells a bot challenge apart from
    # an ordinary refusal, and the decision to re-fetch through the browser is
    # made from the result rather than from inside the response context.
    "cf-mitigated",
)

#: Headers that say *why* a 4xx happened, when the status code does not.
#:
#: `P1-19` records every attempt so the health line can tell one failure from
#: another, and "HTTP 403" defeats that: a bot challenge, a geo-block, a
#: genuinely forbidden path and an expired credential are four different
#: problems with four different responses, and they arrive as the same three
#: digits. These headers are what separate them, and they cost nothing to read.
_DIAGNOSTIC_HEADERS = (
    "cf-mitigated",
    "cf-ray",
    "retry-after",
    "x-blocked-by",
    "x-error",
    "server",
)


#: Body markers for a challenge interstitial, for origins that send no header.
#: Deliberately few and specific — these strings do not occur in ordinary prose,
#: and a loose match here would send perfectly good pages through the browser.
_CHALLENGE_MARKERS = (b"cf-chl", b"/cdn-cgi/challenge-platform", b"just a moment")

#: Statuses a challenge interstitial is served with. A 503 is the classic
#: non-interactive one, which is exactly the case worth waiting out.
_CHALLENGE_STATUSES = frozenset({403, 429, 503})


def is_challenge(
    status_code: int | None,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
) -> bool:
    """Does this response look like a bot-challenge interstitial?

    Worth asking because a challenge is the one refusal a browser can sometimes
    turn into a success. The common non-interactive kind runs a few seconds of
    JavaScript and then serves the real page, so waiting is all that is needed —
    no evasion, just being patient in the way an ordinary browser is.

    The interactive kind never resolves however long it is given (§6.4 declines
    to defeat those, and measurement says undetected browsing does not anyway),
    so this is a cheap bounded attempt rather than a guarantee.
    """
    if status_code not in _CHALLENGE_STATUSES:
        return False
    if headers:
        lowered = {k.lower(): (v or "").strip().lower() for k, v in headers.items()}
        if lowered.get("cf-mitigated") == "challenge":
            return True
    window = body[:8192].lower()
    return any(marker in window for marker in _CHALLENGE_MARKERS)


def describe_http_error(status_code: int, headers: Mapping[str, str] | None = None) -> str:
    """``HTTP 403`` plus whatever the response said about the reason.

    Deliberately mechanical — it reports the headers the origin sent rather than
    concluding anything from them. The one interpretation it does make is
    naming Cloudflare's managed challenge, because ``cf-mitigated: challenge``
    means exactly one thing and it is the single most common reason a public
    page refuses a crawler that is behaving itself.

    A challenge is worth distinguishing because the response to it is not
    "retry later" — it will 403 forever until something renders JavaScript. An
    operator reading a wall of `HTTP 403` has no way to know that.
    """
    base = f"HTTP {status_code}"
    if not headers:
        return base

    lowered = {k.lower(): v for k, v in headers.items()}
    notes: list[str] = []

    if lowered.get("cf-mitigated", "").strip().lower() == "challenge":
        notes.append("cloudflare bot challenge")

    for name in _DIAGNOSTIC_HEADERS:
        value = lowered.get(name)
        if not value:
            continue
        if name == "server" and "cloudflare" in value.lower() and notes:
            # Already said, and saying it twice makes the line harder to read.
            continue
        notes.append(f"{name}={value.strip()[:80]}")

    return f"{base} ({'; '.join(notes)})" if notes else base


@dataclasses.dataclass(frozen=True)
class FetchResult:
    """What one fetch attempt produced, successful or not.

    ``outcome`` is a ``fetch_attempts.outcome`` value so the caller can record
    the row without translating. ``content`` is empty for every outcome except
    ``success``: a refusal must not be mistakable for a small page.
    """

    requested_url: str
    final_url: str
    outcome: str
    status_code: int | None = None
    content: bytes = b""
    media_type: str | None = None
    headers: Mapping[str, str] = dataclasses.field(default_factory=dict)
    redirect_chain: tuple[str, ...] = ()
    elapsed_ms: int = 0
    render_mode: str = "http"
    detail: str = ""
    # Crawl4AI returns markdown, links and citations alongside the HTML. Carried
    # through so extraction (P1-07) and frontier expansion (P5-01) can use them
    # rather than crawling the page a second time to get them.
    browser_payload: dict[str, Any] | None = None
    # Whatever this domain's robots.txt advertised (P1-28, §6.4). Carried on the
    # result rather than enqueued by the fetcher: `Crawler` reads robots.txt on
    # the way past and the loop owns the queue, and a fetcher that wrote frontier
    # rows would make every liveness probe and ad-hoc refetch expand the crawl.
    sitemaps: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome == "success"

    @property
    def domain(self) -> str:
        return registrable_domain(self.final_url or self.requested_url)

    def text(self, errors: str = "replace") -> str:
        """The body decoded as text. UTF-8 with replacement, deliberately.

        Charset negotiation belongs to extraction, which has the document in
        hand; here the only consumer is the JS-dependence heuristic, and it must
        not raise on a page that mislabels its encoding.
        """
        return self.content.decode("utf-8", errors=errors)


def authority(url: str) -> str:
    """The ``Host`` header value for ``url``: hostname, plus port if explicit."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def pinned_url(url: str, address: IPAddress) -> str:
    """Rewrite ``url`` to address the validated IP literal directly.

    Userinfo and fragment are dropped on the way through: neither belongs on the
    wire, and ``user:pass@`` in a crawl target is far more likely to be an
    attempt to confuse a URL parser than a credential anyone meant to send.
    """
    parts = urlsplit(url)
    literal = f"[{address}]" if isinstance(address, ipaddress.IPv6Address) else str(address)
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def media_type(content_type: str | None) -> str | None:
    """The bare media type from a Content-Type header, lowercased."""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def content_type_allowed(media: str | None, allowlist: list[str]) -> bool:
    """Is ``media`` fetchable under this policy?

    An empty allowlist means no restriction — that is the shipped state of
    ``ResolvedPolicy`` before the global row is seeded, and a worker started
    against an unseeded database should be over-permissive about content types
    rather than refuse every page on earth.

    A response with no Content-Type at all is allowed through when the allowlist
    is empty and refused when it is not. Guessing a type for it would defeat the
    point of having an allowlist.
    """
    if not allowlist:
        return True
    if media is None:
        return False
    return media in {entry.strip().lower() for entry in allowlist}


# --------------------------------------------------------------------------
# JS-dependence heuristic — mechanical, no model, per the fast-loop invariant
# --------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"(?is)<script\b.*?</script\s*>")
_DROP_RE = re.compile(r"(?is)<(style|noscript|template)\b.*?</\1\s*>")
_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_TAG_RE = re.compile(r"(?s)<[^>]*>")
_WS_RE = re.compile(r"\s+")

# The mount point of an app that renders itself. Empty in the served HTML by
# construction, which is the whole signature.
_SPA_ROOT_RE = re.compile(
    r"""(?ix) < (?: div | main | section ) [^>]* \b id \s* = \s* ["']? """
    r"""(?: root | app | __next | __nuxt | application | react-root | ember-app ) \b"""
)
_NOSCRIPT_RE = re.compile(r"(?is)<noscript\b[^>]*>(.*?)</noscript\s*>")
_JS_REQUIRED_RE = re.compile(r"(?i)\b(enable|turn on|requires?|needs?)\b[^.]{0,40}\bjavascript\b")


def visible_text(html: str) -> str:
    """Roughly what a reader would see: markup, scripts and styles removed.

    Deliberately regex-only. This runs on every static fetch to decide whether
    to pay for a browser, so it must be cheap; a parse tree is the extractor's
    job (P1-07), by which point the decision has already been made.
    """
    stripped = _COMMENT_RE.sub(" ", html)
    stripped = _SCRIPT_RE.sub(" ", stripped)
    stripped = _DROP_RE.sub(" ", stripped)
    stripped = _TAG_RE.sub(" ", stripped)
    return _WS_RE.sub(" ", stripped).strip()


def looks_javascript_dependent(html: str, *, text_floor: int = JS_TEXT_FLOOR) -> bool:
    """Would a browser plausibly get materially more text than this?

    The text floor is checked first and short-circuits: a page that already has
    a paragraph of prose is not worth re-fetching through Chromium however many
    scripts it also loads, and that ordering is what keeps `render_js: auto`
    from quietly rendering the whole crawl (§6.4, operational constraint 1).
    """
    if len(visible_text(html)) >= text_floor:
        return False
    if _SPA_ROOT_RE.search(html):
        return True
    if any(_JS_REQUIRED_RE.search(block) for block in _NOSCRIPT_RE.findall(html)):
        return True
    # Thin page that ships scripts: the remaining case worth a browser.
    return bool(_SCRIPT_RE.search(html))


# --------------------------------------------------------------------------
# The browser path
# --------------------------------------------------------------------------


class Crawl4aiClient:
    """Thin client over the Crawl4AI Docker API (§6.4).

    Deliberately thin. Crawl4AI is used for clean markdown and citation
    extraction and nothing else — no ``LLMExtractionStrategy`` (an LLM in the
    fast loop breaks §2 principle 1), no stealth mode, no proxy escalation.

    The token is not optional in practice: since 0.9.0 the server binds loopback
    inside its own container unless ``CRAWL4AI_API_TOKEN`` is set, so an
    unauthenticated deployment is also an unreachable one.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> Crawl4aiClient | None:
        """Build from ``CRAWL4AI_URL`` / ``CRAWL4AI_TOKEN``, or return None.

        None rather than a raised error: a worker with no browser available is
        degraded, not broken, and should still crawl everything static.
        """
        url = os.environ.get("CRAWL4AI_URL")
        if not url:
            return None
        return cls(url, os.environ.get("CRAWL4AI_TOKEN") or None)

    async def __aenter__(self) -> Crawl4aiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self, timeout_s: float) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=timeout_s)
        return self._client

    async def crawl(
        self, url: str, policy: ResolvedPolicy, *, settle_s: float = 0.0
    ) -> dict[str, Any]:
        """Render one URL and return Crawl4AI's result dict.

        The browser gets its own generous timeout on top of the policy's: page
        load, JS execution and settling are all inside it, and reusing the
        static timeout would report every heavy page as a failure.

        ``settle_s`` holds the page open after load before reading the HTML. It
        is zero for an ordinary render — waiting costs a browser slot and buys
        nothing on a page that is already complete — and non-zero only when the
        static fetch saw a challenge interstitial, which resolves itself a few
        seconds later or not at all.
        """
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "urls": [url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True, "user_agent": policy.user_agent},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    # The crawler must not follow links of its own; the queue
                    # decides what gets fetched, and every target it hands over
                    # has been through netguard.
                    "cache_mode": "BYPASS",
                    "page_timeout": policy.timeout_s * 1000,
                    "screenshot": False,
                    "pdf": False,
                },
            },
        }
        if settle_s > 0:
            run_params = payload["crawler_config"]["params"]
            run_params["delay_before_return_html"] = settle_s
            # `networkidle` rather than `domcontentloaded`: a challenge fires its
            # own requests, and returning at DOM-ready reads the interstitial
            # instead of whatever replaces it.
            run_params["wait_until"] = "networkidle"

        # The settle happens inside the browser, so the HTTP timeout has to
        # cover it as well as the page load, or the wait is spent and then
        # thrown away by a client-side timeout.
        http_timeout = policy.timeout_s * 3 + settle_s
        response = await self._http(http_timeout).post(
            f"{self.base_url}/crawl", json=payload, headers=headers
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("results") or []
        if not results:
            raise httpx.HTTPError(f"crawl4ai returned no result for {url}")
        return results[0]


# --------------------------------------------------------------------------
# The fetcher
# --------------------------------------------------------------------------


class Fetcher:
    """Fetches URLs under a resolved policy, static or rendered.

    Holds one connection pool across fetches. Because requests are addressed to
    IP literals, httpx pools per address rather than per hostname — which is the
    behaviour wanted anyway, since the pinned address is what the connection is
    actually to.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        browser: Crawl4aiClient | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._resolver = resolver
        self._browser = browser

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._browser is not None:
            await self._browser.aclose()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # follow_redirects stays off at the client level too, so a future
            # call site cannot re-enable the behaviour this module exists to
            # prevent by passing the flag per request.
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    def _request_headers(self, url: str, policy: ResolvedPolicy) -> dict[str, str]:
        headers = {
            "Host": authority(url),
            "User-Agent": policy.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        if policy.send_contact_header:
            # §14.2: identifiable, contactable crawling. The contact URL rides
            # in the user agent string itself, which is where operators look.
            headers["From"] = policy.user_agent
        return headers

    # -- public API --------------------------------------------------------

    async def fetch(
        self,
        url: str,
        policy: ResolvedPolicy,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        """Fetch ``url``, choosing the static or browser path per ``render_js``.

        ``auto`` fetches statically first and re-fetches through the browser
        only when the static HTML looks like a shell. That ordering costs one
        cheap request on JS-dependent pages and saves a browser launch on
        everything else, which is the right way round for this corpus.
        """
        mode = (policy.render_js or "auto").lower()

        if mode == "always":
            rendered = await self.fetch_rendered(url, policy)
            if rendered.ok or self._browser is not None:
                return rendered
            # No browser configured at all. Degraded, not broken: a static body
            # extracts worse than a rendered one and infinitely better than the
            # page never being fetched.
            log.warning(
                "render_js=always but no browser configured; falling back to static",
                extra={"url": url, "domain": registrable_domain(url)},
            )
            return await self.fetch_static(url, policy, extra_headers=extra_headers)

        static = await self.fetch_static(url, policy, extra_headers=extra_headers)

        # A challenge is the one refusal a browser can sometimes turn into a
        # success, so it is checked before `static.ok` sends the result back.
        # The common non-interactive challenge runs a few seconds of JavaScript
        # and then serves the real page; waiting it out is not evasion, it is
        # what any browser does. The interactive kind never resolves, which is
        # why this is bounded and tried once.
        if (
            not static.ok
            and mode != "never"
            and policy.challenge_wait_s > 0
            and self._browser is not None
            and is_challenge(static.status_code, static.headers)
        ):
            log.info(
                "challenge interstitial; re-fetching through the browser",
                extra={
                    "url": url,
                    "domain": registrable_domain(url),
                    "status": static.status_code,
                    "settle_s": policy.challenge_wait_s,
                },
            )
            rendered = await self.fetch_rendered(
                url, policy, settle_s=float(policy.challenge_wait_s)
            )
            if rendered.ok:
                return rendered
            # Still challenged. Keep the static refusal, which carries the
            # header that says so, rather than the browser's less specific one.
            log.info(
                "challenge did not clear within the wait",
                extra={"url": url, "domain": registrable_domain(url)},
            )
            return static

        if mode != "auto" or not static.ok:
            return static
        if media_type(static.headers.get("content-type")) not in ("text/html", None):
            return static
        if not looks_javascript_dependent(static.text()):
            return static

        rendered = await self.fetch_rendered(url, policy)
        if rendered.ok:
            return rendered
        log.info(
            "browser re-fetch failed; keeping the static body",
            extra={"url": url, "outcome": rendered.outcome, "detail": rendered.detail},
        )
        return static

    async def fetch_static(
        self,
        url: str,
        policy: ResolvedPolicy,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        """The pinned HTTP path: validate, pin, request, re-validate each hop."""
        started = time.monotonic()
        chain: list[str] = []
        current = url

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        def refuse(
            outcome: str,
            detail: str,
            status: int | None = None,
            headers: Mapping[str, str] | None = None,
        ) -> FetchResult:
            log.warning(
                "fetch refused",
                extra={
                    "url": url,
                    "final_url": current,
                    "domain": registrable_domain(current),
                    "outcome": outcome,
                    "detail": detail,
                },
            )
            return FetchResult(
                requested_url=url,
                final_url=current,
                outcome=outcome,
                status_code=status,
                detail=detail,
                # Carried even on a refusal: `is_challenge` reads them, and the
                # decision to re-fetch through a browser is made by the caller
                # from the result, long after the response context has closed.
                headers=dict(headers or {}),
                redirect_chain=tuple(chain),
                elapsed_ms=elapsed(),
            )

        try:
            for hop in range(policy.max_redirects + 1):
                # Resolution happens on every hop whether or not the policy
                # revalidates: pinning needs an address, and there is no way to
                # pin without one. `revalidate_each_redirect: false` therefore
                # relaxes only the *verdict* on later hops, never the lookup —
                # a per-domain escape hatch for a site whose redirect chain
                # trips the classifier, not a way to turn the guard off.
                revalidate = hop == 0 or policy.revalidate_each_redirect
                guard_kwargs = {
                    "allowed_schemes": policy.allowed_schemes,
                    "block_private": policy.block_private_addresses and revalidate,
                    "block_mixed_dns": policy.block_mixed_dns,
                    "resolver": self._resolver,
                }
                if hop == 0:
                    addresses = await assert_url_allowed(current, **guard_kwargs)
                else:
                    addresses = await assert_redirect_allowed(
                        current, is_final=False, **guard_kwargs
                    )

                headers = self._request_headers(current, policy)
                if extra_headers:
                    headers.update(extra_headers)

                async with self._http().stream(
                    "GET",
                    pinned_url(current, addresses[0]),
                    headers=headers,
                    extensions={"sni_hostname": urlsplit(current).hostname},
                    timeout=policy.timeout_s,
                    follow_redirects=False,
                ) as response:
                    location = response.headers.get("location")
                    if response.is_redirect and location:
                        current = urljoin(current, location)
                        chain.append(current)
                        continue

                    # Only now is this hop known to be the last one.
                    check_https_final(current, policy.require_https_final)

                    kept = {k: v for k, v in response.headers.items() if k.lower() in _KEPT_HEADERS}
                    media = media_type(response.headers.get("content-type"))

                    if response.status_code == 304:
                        return FetchResult(
                            requested_url=url,
                            final_url=current,
                            outcome="not_modified",
                            status_code=304,
                            media_type=media,
                            headers=kept,
                            redirect_chain=tuple(chain),
                            elapsed_ms=elapsed(),
                        )

                    if response.status_code >= 400:
                        return refuse(
                            "http_error",
                            describe_http_error(response.status_code, response.headers),
                            response.status_code,
                            kept,
                        )

                    if not content_type_allowed(media, policy.allowed_content_types):
                        return refuse(
                            "content_type_rejected",
                            media or "no content-type",
                            response.status_code,
                        )

                    declared = _declared_length(response.headers.get("content-length"))
                    if declared is not None and declared > policy.max_page_bytes:
                        return refuse(
                            "too_large",
                            f"content-length {declared} > {policy.max_page_bytes}",
                            response.status_code,
                        )

                    body, overrun = await _read_capped(response, policy)
                    if overrun:
                        return refuse(
                            overrun, _overrun_detail(overrun, policy), response.status_code
                        )

                    return FetchResult(
                        requested_url=url,
                        final_url=current,
                        outcome="success",
                        status_code=response.status_code,
                        content=body,
                        media_type=media,
                        headers=kept,
                        redirect_chain=tuple(chain),
                        elapsed_ms=elapsed(),
                    )

            return refuse("too_many_redirects", f"more than {policy.max_redirects} hops")

        except BlockedTarget as exc:
            # A name that will not resolve is a dead link, not an attack; the
            # health line needs to tell those apart or a DNS outage reads as a
            # burst of hostile targets.
            outcome = "connection_error" if exc.reason in DNS_REASONS else "unsafe_target"
            return refuse(outcome, str(exc))
        except httpx.TimeoutException as exc:
            return refuse("timeout", f"{type(exc).__name__}: {exc}")
        except httpx.HTTPError as exc:
            return refuse("connection_error", f"{type(exc).__name__}: {exc}")

    async def fetch_rendered(
        self, url: str, policy: ResolvedPolicy, *, settle_s: float = 0.0
    ) -> FetchResult:
        """The browser path, through Crawl4AI.

        The URL is validated before it is handed over and the URL Crawl4AI
        reports having *landed* on is validated afterwards — the browser follows
        its own redirects, so the second check is the only thing standing
        between a redirect chain and an internal address. Neither is pinning;
        see the module docstring, and `P1-25`.
        """
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        def refuse(outcome: str, detail: str, final: str = url) -> FetchResult:
            log.warning(
                "rendered fetch refused",
                extra={
                    "url": url,
                    "domain": registrable_domain(url),
                    "outcome": outcome,
                    "detail": detail,
                },
            )
            return FetchResult(
                requested_url=url,
                final_url=final,
                outcome=outcome,
                detail=detail,
                render_mode="browser",
                elapsed_ms=elapsed(),
            )

        if self._browser is None:
            return refuse("blocked", "no browser configured")

        try:
            await assert_url_allowed(
                url,
                allowed_schemes=policy.allowed_schemes,
                block_private=policy.block_private_addresses,
                block_mixed_dns=policy.block_mixed_dns,
                resolver=self._resolver,
            )
        except BlockedTarget as exc:
            outcome = "connection_error" if exc.reason in DNS_REASONS else "unsafe_target"
            return refuse(outcome, str(exc))

        try:
            result = await self._browser.crawl(url, policy, settle_s=settle_s)
        except httpx.TimeoutException as exc:
            return refuse("timeout", f"{type(exc).__name__}: {exc}")
        except httpx.HTTPError as exc:
            return refuse("connection_error", f"{type(exc).__name__}: {exc}")

        final = result.get("redirected_url") or result.get("url") or url
        status = result.get("status_code")

        try:
            check_https_final(final, policy.require_https_final)
            if final != url:
                await assert_url_allowed(
                    final,
                    allowed_schemes=policy.allowed_schemes,
                    block_private=policy.block_private_addresses,
                    block_mixed_dns=policy.block_mixed_dns,
                    resolver=self._resolver,
                )
        except BlockedTarget as exc:
            outcome = "connection_error" if exc.reason in DNS_REASONS else "unsafe_target"
            return refuse(outcome, str(exc), final=final)

        if not result.get("success"):
            # The browser path gets the same diagnosis as the static one: a
            # rendered page refused by a bot challenge looks identical to one
            # refused for any other reason, and Crawl4AI's error_message says
            # nothing about which.
            detail = result.get("error_message") or "crawl4ai reported failure"
            if status and status >= 400:
                detail = f"{describe_http_error(status, result.get('response_headers') or {})}: {detail}"
            return refuse(
                "http_error" if status else "connection_error",
                detail,
                final=final,
            )

        raw_headers = result.get("response_headers") or {}
        kept = {k: v for k, v in raw_headers.items() if k.lower() in _KEPT_HEADERS}
        media = media_type(raw_headers.get("content-type")) or "text/html"

        if not content_type_allowed(media, policy.allowed_content_types):
            return refuse("content_type_rejected", media, final=final)

        body = (result.get("html") or "").encode("utf-8")
        if len(body) > policy.max_page_bytes:
            return refuse("too_large", f"{len(body)} > {policy.max_page_bytes}", final=final)

        return FetchResult(
            requested_url=url,
            final_url=final,
            outcome="success",
            status_code=status,
            content=body,
            media_type=media,
            headers=kept,
            redirect_chain=(final,) if final != url else (),
            elapsed_ms=elapsed(),
            render_mode="browser",
            browser_payload=result,
        )


class _Inflater:
    """Decompresses a response body in bounded steps.

    ``zlib.decompressobj().decompress(data, max_length)`` is the primitive that
    makes a cap enforceable: it returns at most ``max_length`` bytes and parks
    the rest of the input in ``unconsumed_tail``, so the caller gets control back
    between steps instead of after the allocation.

    Only gzip, deflate and identity are handled, which is complete rather than
    partial: the fetcher sends ``Accept-Encoding: gzip, deflate``, so anything
    else is a server ignoring what was asked for, and guessing at it would be
    worse than refusing to read it.
    """

    def __init__(self, encoding: str) -> None:
        self._encoding = encoding
        self._obj = (
            zlib.decompressobj(16 + zlib.MAX_WBITS)
            if encoding == "gzip"
            else zlib.decompressobj()
            if encoding == "deflate"
            else None
        )
        # RFC 1950 vs RFC 1951: "deflate" is served both zlib-wrapped and raw,
        # and the only way to tell is to try. Retried once, on the first chunk,
        # while the decompressor still has no state to lose.
        self._may_retry_raw = encoding == "deflate"

    def feed(self, data: bytes) -> Iterator[bytes]:
        if self._obj is None:
            if data:
                yield data
            return

        pending = data
        while True:
            try:
                piece = self._obj.decompress(pending, _INFLATE_STEP_BYTES)
            except zlib.error:
                if not self._may_retry_raw:
                    raise
                self._may_retry_raw = False
                self._obj = zlib.decompressobj(-zlib.MAX_WBITS)
                piece = self._obj.decompress(pending, _INFLATE_STEP_BYTES)
            self._may_retry_raw = False

            if not piece:
                # No output means the input was fully consumed — zlib only
                # leaves an unconsumed tail when max_length truncated the
                # output, which implies a non-empty piece. Breaking here is
                # what guarantees this loop terminates.
                return
            yield piece
            pending = self._obj.unconsumed_tail
            if not pending:
                return


def _inflater_for(content_encoding: str | None) -> tuple[_Inflater, str | None]:
    """Build an inflater, or report the encoding as unsupported.

    Multiple encodings (``gzip, br``) are refused rather than partly applied.
    """
    encoding = (content_encoding or "identity").strip().lower()
    if encoding in ("", "identity", "none"):
        encoding = "identity"
    if encoding not in ("identity", "gzip", "deflate", "x-gzip"):
        return _Inflater("identity"), encoding
    return _Inflater("gzip" if encoding == "x-gzip" else encoding), None


def _declared_length(value: str | None) -> int | None:
    """Content-Length as an int, or None if absent or unparseable.

    A malformed header is treated as absent rather than as an error: the
    streaming cap below is the real limit, and the declared length is only ever
    an optimisation that avoids downloading a body already known to be too big.
    """
    if not value:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _overrun_detail(outcome: str, policy: ResolvedPolicy) -> str:
    if outcome == "too_large":
        return f"body exceeded max_page_bytes={policy.max_page_bytes}"
    return f"decoded/raw exceeded max_decompression_ratio={policy.max_decompression_ratio}"


async def _read_capped(
    response: httpx.Response, policy: ResolvedPolicy
) -> tuple[bytes, str | None]:
    """Stream a body, aborting on size or compression ratio.

    Returns ``(body, None)`` or ``(b"", outcome)``.

    **The decompression is driven by hand, and that is the whole point.** Letting
    httpx decode (``aiter_bytes``) hands back whatever one network read inflates
    to, as a single object, before any cap can look at it: measured here, a 64KB
    read of a gzip bomb arrived as one 67MB chunk. Checking a limit after the
    allocation that the limit exists to prevent is not a limit. So the raw bytes
    are read instead and pushed through ``zlib`` in bounded steps, with both caps
    re-checked between steps — the abort happens while the bomb is inflating.

    Both caps are wanted, not one. ``max_page_bytes`` bounds an honestly large
    page; the ratio bounds a small one that is lying about how large it is, and
    it fires roughly twenty times sooner, so the memory a hostile response can
    cost is bounded by ``RATIO_FLOOR_BYTES`` rather than by ``max_page_bytes``.
    """
    inflater, unsupported = _inflater_for(response.headers.get("content-encoding"))
    if unsupported is not None:
        return b"", "parse_error"

    chunks: list[bytes] = []
    decoded = 0
    raw_total = 0

    async for raw_chunk in response.aiter_raw():
        raw_total += len(raw_chunk)
        try:
            # A generator, deliberately: materialising the pieces of one raw
            # chunk into a list before checking anything would re-introduce the
            # unbounded allocation this function exists to avoid.
            for piece in inflater.feed(raw_chunk):
                decoded += len(piece)
                if decoded > policy.max_page_bytes:
                    return b"", "too_large"
                if (
                    decoded > RATIO_FLOOR_BYTES
                    and raw_total > 0
                    and decoded > raw_total * policy.max_decompression_ratio
                ):
                    return b"", "decompression_bomb"
                chunks.append(piece)
        except zlib.error:
            # A body that will not decompress is not a fetch failure to retry;
            # it is a document this crawler cannot read.
            return b"", "parse_error"

    return b"".join(chunks), None
