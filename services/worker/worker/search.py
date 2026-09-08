"""The search backend (task P1-34, spec §6.4, §6.1).

§6.4 picks SearXNG, self-hosted: metasearch over several upstream engines, JSON
output, no API keys. It also states the weakness plainly, and the whole design
of this module follows from it — *it scrapes upstream engines, so individual
engines break or get rate-limited regularly*. Configure several, treat engine
failure as routine, **and never let a dead engine stall the queue.**

That sentence is the specification for the failure handling here, so it is worth
being precise about what "stall" means. Three different things can go wrong and
they are not the same event:

- **An engine is unresponsive.** SearXNG says so in `unresponsive_engines` and
  returns the other engines' results anyway. This is the routine case: it is
  logged and otherwise ignored, because a query answered by three engines
  instead of four is answered.
- **Every engine failed, or the query genuinely matches nothing.** SearXNG
  answers with an empty result list. The query is *done*, not failed — retrying
  a query nobody can answer just burns the queue slot again tomorrow.
- **SearXNG itself is unreachable.** That is transient and local, so the task
  retries with the ordinary backoff, and the health line says the backend is
  missing.

**This does not go through `Crawler.fetch`, and must not.** The crawler pins
every request to a validated public address and `netguard` refuses RFC1918 —
which is correct for the open web and exactly wrong for an internal service
reachable only at a private address on the compose network. A search is also not
a *fetch*: it produces no bytes to store, no source to cite, and no
`fetch_attempts` row that would mean anything, because the domain that answered
is SearXNG rather than the domain the URL belongs to.

**The results are candidates, not pages.** §6.4 notes SearXNG returns a lot of
content-farm and SEO junk. Everything here does is hand URLs to the prefilter;
what is worth a request is `prefilter.py`'s question, and what is a duplicate is
the novelty gate's.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpx

from meridian_core.logging import get_logger

log = get_logger(__name__)

#: How long to wait for the whole metasearch. Generous by HTTP standards and
#: deliberately so: SearXNG is itself waiting on several upstream engines, and a
#: timeout here throws away the engines that *did* answer.
DEFAULT_TIMEOUT_S = 30.0

#: How many results to take from one query. Not a SearXNG parameter — it pages
#: at 10 per page — but a bound on what one queue row may produce, so a single
#: seed query cannot flood the frontier ahead of everything else in it.
DEFAULT_MAX_RESULTS = 50

#: Schemes worth queueing. `netguard` enforces this again at fetch time; here it
#: saves the queue row rather than the request.
ALLOWED_SCHEMES = frozenset({"http", "https"})


class SearchError(RuntimeError):
    """The backend could not be reached or did not answer usefully."""


@dataclasses.dataclass(frozen=True)
class SearchResults:
    """What one query produced.

    ``unresponsive`` is kept rather than dropped because it is the number that
    explains a thin result set, and §6.4 says to expect it — an operator looking
    at a query that returned four URLs needs to know whether three engines were
    down or the web simply has four.
    """

    query: str
    urls: tuple[str, ...] = ()
    unresponsive: tuple[str, ...] = ()
    #: Results the backend returned that were dropped here, by reason. Shape
    #: matches `prefilter.Verdict.dropped` so the two read the same way in logs.
    dropped: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.urls


class SearxClient:
    """Thin client over SearXNG's JSON API (§6.4).

    Thin in the same way `Crawl4aiClient` is: it asks one question, and every
    judgement about what to do with the answer belongs somewhere else.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_results: int = DEFAULT_MAX_RESULTS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_results = max_results
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> SearxClient | None:
        """Build from ``SEARXNG_URL``, or return None.

        None rather than a raised error, for the reason `Crawl4aiClient` returns
        None: a worker with no search backend is degraded, not broken. It still
        crawls every URL it already has — it just cannot widen the frontier when
        that runs out, which is why the health line has to say so (§12.5).
        """
        url = os.environ.get("SEARXNG_URL")
        if not url:
            return None
        return cls(
            url,
            timeout_s=_float_env("MERIDIAN_SEARCH_TIMEOUT_S", DEFAULT_TIMEOUT_S),
            max_results=_int_env("MERIDIAN_SEARCH_MAX_RESULTS", DEFAULT_MAX_RESULTS),
        )

    async def __aenter__(self) -> SearxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def healthy(self, timeout_s: float = 5.0) -> bool:
        """Is the backend answering at all? (§12.5)

        Same reason `P1-26` gave the browser one: the failure is silent. A
        worker whose search backend died a week ago keeps crawling, drains its
        frontier, and then idles — and "idle" looks identical to "finished".
        """
        try:
            response = await self._http().get(f"{self.base_url}/healthz", timeout=timeout_s)
        except (httpx.HTTPError, OSError):
            return False
        # `/healthz` is the documented probe, but it is disabled in some
        # deployments and answers 404 rather than refusing the connection. A
        # response of any kind means the service is up, which is the question.
        return response.status_code < 500

    async def search(self, query: str) -> SearchResults:
        """Run one query. Raises `SearchError` only when the *backend* failed.

        An empty result set is a return value, not an exception: it means the
        query was answered and the answer was nothing, and the two have to be
        distinguishable because one retries and the other does not.
        """
        if not query or not query.strip():
            raise SearchError("refusing to search for an empty query")

        try:
            response = await self._http().get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json"},
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise SearchError(f"searxng returned {exc.response.status_code}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise SearchError(f"{type(exc).__name__}: {exc}") from exc
        except ValueError as exc:
            # JSON output is a `settings.yml` option. A deployment that never
            # enabled it answers 200 with HTML, and the honest report is that
            # the backend is misconfigured rather than that nothing matched.
            raise SearchError(f"searxng did not return JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise SearchError(f"searxng returned {type(payload).__name__}, expected an object")

        return self._collect(query, payload)

    def _collect(self, query: str, payload: dict) -> SearchResults:
        """Pull URLs out of one JSON body, in rank order, without duplicates.

        Rank order matters: SearXNG has already fused several engines' rankings,
        and truncating at `max_results` keeps the best of that fusion rather
        than an arbitrary slice of it.
        """
        dropped: dict[str, int] = {}
        seen: dict[str, None] = {}

        def drop(reason: str) -> None:
            dropped[reason] = dropped.get(reason, 0) + 1

        for result in payload.get("results") or []:
            if len(seen) >= self.max_results:
                drop("over_max_results")
                continue
            url = result.get("url") if isinstance(result, dict) else None
            if not isinstance(url, str) or not url.strip():
                drop("no_url")
                continue
            url = url.strip()
            if urlsplit(url).scheme.lower() not in ALLOWED_SCHEMES:
                # `javascript:` and `magnet:` do turn up in scraped result sets.
                drop("bad_scheme")
                continue
            if url in seen:
                # The same page ranked by two engines is one candidate. The
                # prefilter would catch it, but counting it here is what makes
                # "40 results, 12 candidates" legible.
                drop("duplicate")
                continue
            seen[url] = None

        return SearchResults(
            query=query,
            urls=tuple(seen),
            unresponsive=tuple(_strings(payload.get("unresponsive_engines") or [])),
            dropped=dropped,
        )


def _strings(entries: Iterable[object]) -> list[str]:
    """SearXNG reports unresponsive engines as `[name, reason]` pairs in some
    versions and as bare strings in others. Both are just labels for a log."""
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, (list, tuple)) and entry:
            out.append(str(entry[0]))
    return out


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive, got {value}")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive, got {value}")
    return value
