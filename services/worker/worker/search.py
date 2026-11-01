"""The search backend (task P1-34, spec §6.4, §6.1).

SearXNG, called directly rather than through `Crawler.fetch`, which would refuse an
internal address. An unresponsive engine is logged, an empty answer is final, an
unreachable SearXNG is transient. The results are candidates for the prefilter. See
docs/features/discovery.md#search.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpx

from meridian_core.logging import get_logger
from meridian_core.searchseeds import NEWS_BANG

log = get_logger(__name__)

#: How long to wait for the whole metasearch. Generous by HTTP standards and
#: deliberately so: SearXNG is itself waiting on several upstream engines, and a
#: timeout here throws away the engines that *did* answer.
DEFAULT_TIMEOUT_S = 30.0

#: How many results to take from one query. Not a SearXNG parameter — it pages
#: at 10 per page — but a bound on what one queue row may produce, so a single
#: seed query cannot flood the frontier ahead of everything else in it.
DEFAULT_MAX_RESULTS = 50

#: The least time between two queries to the backend (`B-107`). The engines
#: behind it rate-limit by client: a batch of queries sent back to back got
#: every web engine suspended ("too many requests", CAPTCHA) within minutes.
DEFAULT_MIN_INTERVAL_S = 15.0

#: Words in an engine's unresponsive reason that mean "later", not "never".
THROTTLE_WORDS = ("suspended", "captcha", "too many requests", "access denied", "timeout")

#: Schemes worth queueing. `netguard` enforces this again at fetch time; here it
#: saves the queue row rather than the request.
ALLOWED_SCHEMES = frozenset({"http", "https"})


#: How far back a news query looks (`B-244`). Without a range SearXNG returned news of any age
#: by relevance, so a query repeated every few days found the same old articles. A week, not a
#: day: not every news engine honours `day`, and the daily repeat covers the overlap.
NEWS_TIME_RANGE = "week"


def search_params(query: str) -> dict[str, str]:
    """SearXNG's parameters for one query: a news query (`!news …`) asks for the last week."""
    params = {"q": query, "format": "json"}
    if query.lstrip().startswith(NEWS_BANG):
        params["time_range"] = NEWS_TIME_RANGE
    return params


class SearchError(RuntimeError):
    """The backend could not be reached or did not answer usefully."""


@dataclasses.dataclass(frozen=True)
class SearchResults:
    """What one query produced.

    ``unresponsive`` is kept because it explains a thin result set.
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

    @property
    def throttled(self) -> bool:
        """Nothing came back *because* the engines refused to answer (`B-107`).

        Not the same as an empty answer: a suspended or CAPTCHA-walled engine
        answers the same query in an hour. Settled as done, the query was lost
        for good, since queries are never asked twice.
        """
        return self.empty and any(
            word in reason.lower() for reason in self.unresponsive for word in THROTTLE_WORDS
        )


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
        min_interval_s: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_results = max_results
        self._client = client
        self._owns_client = client is None
        self.min_interval_s = min_interval_s
        self._pace = asyncio.Lock()
        self._last = 0.0

    @classmethod
    def from_env(cls) -> SearxClient | None:
        """Build from ``SEARXNG_URL``, or return None.

        None rather than an error: a worker with no search backend is degraded, not broken.
        """
        url = os.environ.get("SEARXNG_URL")
        if not url:
            return None
        return cls(
            url,
            timeout_s=_float_env("MERIDIAN_SEARCH_TIMEOUT_S", DEFAULT_TIMEOUT_S),
            max_results=_int_env("MERIDIAN_SEARCH_MAX_RESULTS", DEFAULT_MAX_RESULTS),
            min_interval_s=_float_env("MERIDIAN_SEARCH_MIN_INTERVAL_S", DEFAULT_MIN_INTERVAL_S),
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

        # One query at a time, spaced (`B-107`): lanes claim queries together.
        async with self._pace:
            wait = self._last + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            return await self._search(query)

    async def _search(self, query: str) -> SearchResults:
        try:
            response = await self._http().get(
                f"{self.base_url}/search",
                params=search_params(query),
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
    """Engine names from SearXNG's ``unresponsive_engines``.

    Some versions report `[name, reason]` pairs and others bare strings; both are labels for a log.
    """
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, (list, tuple)) and entry:
            # The reason too (`B-107`): "brave: Suspended: too many requests" is
            # what tells a throttled engine from a broken one.
            out.append(": ".join(str(part) for part in entry if part))
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


# --------------------------------------------------------------------------
# GDELT, a second source for news queries (`B-267`)
# --------------------------------------------------------------------------

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
#: GDELT asks for at most one request every five seconds, and answers faster ones with a
#: plain-text refusal; spaced wider, since a refused query costs a retry.
GDELT_MIN_INTERVAL_S = 8.0
GDELT_MAX_RESULTS = 50
#: GDELT's names for the languages a news query may name with `:xx` (`searchseeds`).
GDELT_LANGUAGES = {
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "it": "italian",
    "nl": "dutch",
    "pt": "portuguese",
    "zh": "chinese",
    "ja": "japanese",
    "ko": "korean",
    "ms": "malay",
    "id": "indonesian",
    "sv": "swedish",
    "da": "danish",
    "fi": "finnish",
    "no": "norwegian",
    "pl": "polish",
}


def gdelt_params(query: str) -> dict[str, str] | None:
    """GDELT DOC parameters for a news query (`!news [:xx] words`), or None for any other.

    The last week, newest first, matching the range SearXNG is asked for.
    """
    words = query.split()
    if not words or words[0] != NEWS_BANG:
        return None
    words = words[1:]
    language = None
    if words and words[0].startswith(":") and len(words[0]) == 3:
        language = GDELT_LANGUAGES.get(words[0][1:].lower())
        words = words[1:]
    if not words:
        return None
    text = " ".join(words)
    if language:
        text += f" sourcelang:{language}"
    return {
        "query": text,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(GDELT_MAX_RESULTS),
        "timespan": "1w",
        "sort": "DateDesc",
    }


class GdeltClient:
    """GDELT's free DOC API for news queries: articles from the last week, many languages.

    Its answers come with status 200 whatever happened: JSON with `articles` (or `{}` for
    none), or a plain-text sentence for a refusal ("Please limit requests…", read as
    throttled, so the query is retried) or a query it cannot run (read as an empty answer).
    """

    def __init__(
        self,
        *,
        base_url: str = GDELT_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_interval_s: float = GDELT_MIN_INTERVAL_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.min_interval_s = min_interval_s
        self._client = client
        self._owns_client = client is None
        self._pace = asyncio.Lock()
        self._last = 0.0

    @classmethod
    def from_env(cls) -> GdeltClient | None:
        """On unless `MERIDIAN_GDELT` says `false`: free, public, and only for news queries."""
        if os.environ.get("MERIDIAN_GDELT", "true").strip().lower() in {"0", "false", "no"}:
            return None
        return cls(min_interval_s=_float_env("MERIDIAN_GDELT_MIN_INTERVAL_S", GDELT_MIN_INTERVAL_S))

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str) -> SearchResults:
        """One news query. Raises `SearchError` when GDELT did not answer at all."""
        params = gdelt_params(query)
        if params is None:
            return SearchResults(query=query)
        async with self._pace:
            wait = self._last + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            try:
                response = await self._http().get(self.base_url, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise SearchError(f"gdelt returned {exc.response.status_code}") from exc
            except (httpx.HTTPError, OSError) as exc:
                raise SearchError(f"gdelt: {type(exc).__name__}: {exc}") from exc
        return self._read(query, response.text)

    @staticmethod
    def _read(query: str, body: str) -> SearchResults:
        try:
            payload = json.loads(body)
        except ValueError:
            sentence = body.strip().split("\n", 1)[0][:200]
            if "limit requests" in sentence.lower():
                return SearchResults(query=query, unresponsive=("gdelt: too many requests",))
            return SearchResults(query=query, dropped={"gdelt_refused_query": 1})
        urls: dict[str, None] = {}
        for article in (payload.get("articles") or []) if isinstance(payload, dict) else []:
            url = article.get("url") if isinstance(article, dict) else None
            if isinstance(url, str) and urlsplit(url).scheme.lower() in ALLOWED_SCHEMES:
                urls[url.strip()] = None
        return SearchResults(query=query, urls=tuple(urls)[:GDELT_MAX_RESULTS])


class NewsSearch:
    """SearXNG for every query, and GDELT beside it for news queries (`B-267`).

    The two answers are interleaved, so neither's ranking buries the other. One backend
    failing does not fail the query while the other answered; its failure is listed in
    `unresponsive`, which is what tells a throttled query (retried) from an empty one.
    """

    def __init__(self, searx: SearxClient, gdelt: GdeltClient | None) -> None:
        self.searx = searx
        self.gdelt = gdelt

    async def healthy(self, timeout_s: float = 5.0) -> bool:
        return await self.searx.healthy(timeout_s)

    async def aclose(self) -> None:
        await self.searx.aclose()
        if self.gdelt is not None:
            await self.gdelt.aclose()

    async def search(self, query: str) -> SearchResults:
        if self.gdelt is None or gdelt_params(query) is None:
            return await self.searx.search(query)
        answers = await asyncio.gather(
            self.searx.search(query), self.gdelt.search(query), return_exceptions=True
        )
        failed = [a for a in answers if isinstance(a, BaseException)]
        if len(failed) == len(answers):
            raise SearchError("; ".join(str(f) for f in failed))
        got = [a for a in answers if isinstance(a, SearchResults)]
        merged: dict[str, None] = {}
        for rank in range(max(len(a.urls) for a in got)):
            for answer in got:
                if rank < len(answer.urls):
                    merged.setdefault(answer.urls[rank], None)
        unresponsive = tuple(r for a in got for r in a.unresponsive) + tuple(str(f) for f in failed)
        dropped: dict[str, int] = {}
        for answer in got:
            for reason, n in answer.dropped.items():
                dropped[reason] = dropped.get(reason, 0) + n
        return SearchResults(
            query=query, urls=tuple(merged), unresponsive=unresponsive, dropped=dropped
        )
