"""Paper full-text resolution (task P1-14, spec §6.5, §6.4).

§6.5 states the problem and the order: given a DOI, resolve to a **legally
available** copy, trying Unpaywall, then OpenAlex, then CORE, then the preprint
servers. That word is doing work — the chain is a list of places that
redistribute papers with the publisher's or author's permission, and a corpus
whose whole promise is checkable citations cannot be built on copies its readers
cannot legally follow.

**Why this matters more than it used to.** `P1-34` wired an academic search feed
into the frontier, and a large share of what it returns are publisher landing
pages: an abstract, a paywall, and nothing to extract. Without resolution those
are requests spent to learn a title. With it they become the open-access PDF the
same paper is already sitting in somewhere else.

**Not through `Crawler.fetch`, for the reason the search client is not.** These
are JSON APIs, and `allowed_content_types` is the corpus's list of what can be
read *as a document* — `application/json` is not on it, so every call would be
refused as a content-type rejection. The URLs these APIs *return* do go through
the crawler, with `netguard` and robots and rate limits intact, which is what
makes it safe for a hostile page to put any DOI it likes in its citation list.

**A provider failing is routine; every provider failing is not.** The same
three-way distinction the search backend needs (§6.4), because it settles the
queue row three different ways:

- one provider errors            → try the next; that is what a chain is for
- all answered, none had a copy  → an *answer*. The paper is paywalled today,
                                   and it will be paywalled tomorrow, so the
                                   task is done rather than retried
- none answered at all           → transient; raise, and let the task retry

**A provider with no credential is skipped, not failed.** CORE needs an API key
and Unpaywall needs a contact email; a deployment that has neither should still
get OpenAlex and the preprint rule rather than an error per DOI.

**Beyond §6.5's four.** Europe PMC and Semantic Scholar are added after the
spec's list rather than in place of it. Both need no credential, both answer in
clean JSON, and both routinely hold a copy the aggregators above them miss —
Europe PMC because it mirrors the full text itself rather than pointing at it,
and Semantic Scholar because it indexes the repository PDF where Unpaywall
often has only the repository's landing page. §6.5's order is a ranking by how
likely a provider is to be right, not a closed list, and these two sit below the
four it names for exactly that reason.

**What is deliberately not here.** Sci-Hub and its mirrors. §6.5's requirement
is a *legally available* copy, and that is not a formality in a system whose
output is citations someone else has to be able to follow and check. Google
Scholar is a different case and also absent: it publishes no API, it captchas
scrapers, and it is already reachable — `P1-34` searches it as one of SearXNG's
engines, which is where an index belongs. An index tells you a paper exists;
this module's question is where a copy of it legally lives.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import re
import time
from urllib.parse import quote

import httpx

from meridian_core.logging import get_logger

log = get_logger(__name__)

#: A DOI, as registered: a `10.` prefix, a registrant code, then an opaque
#: suffix. Validated rather than trusted because the value reaches a URL path —
#: citations come from crawled pages, and a page can say anything.
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")

#: Prefixes a DOI arrives wearing when it was written as a link or a URN.
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")

#: DataCite's arXiv prefix. An `arXiv` DOI resolves to arXiv, which is where
#: Unpaywall would send us anyway — so the answer is available without a request.
ARXIV_DOI_PREFIX = "10.48550/arxiv."

DEFAULT_TIMEOUT_S = 20.0

#: Minimum seconds between calls to one provider, by name.
#:
#: Not politeness in the abstract — measured. Unauthenticated Semantic Scholar
#: allows roughly a request a second, and 75 DOIs resolved back to back got a
#: PDF for none of them while the same DOIs at one per second got a PDF for
#: every one. The others are generous by comparison and are paced only enough
#: that a citation-heavy page cannot burst through them.
#:
#: This is per-process and per-provider. The worker resolves one DOI per lane,
#: so a handful of lanes sharing one resolver is exactly the case this bounds.
PROVIDER_MIN_INTERVAL_S = {
    "semanticscholar": 1.1,
    "europepmc": 0.3,
    "unpaywall": 0.1,
    "openalex": 0.1,
    "core": 0.2,
}


class DoiError(ValueError):
    """The DOI itself is not usable. Not retryable, and not the network's fault."""


class ResolutionUnavailable(RuntimeError):
    """No provider could be reached. Transient — the DOI may resolve later."""


@dataclasses.dataclass(frozen=True)
class OpenAccessCopy:
    """Where a legally available copy of one paper lives."""

    url: str
    #: Which link in the chain answered. Kept because "Unpaywall found it" and
    #: "we guessed from the DOI" are different levels of confidence, and §5.2's
    #: provenance question applies to how a URL was found as much as to who
    #: published it.
    provider: str
    #: `publishedVersion` / `acceptedVersion` / `submittedVersion` when the
    #: provider says. The published version is the citable one; a preprint is
    #: still worth having and worth labelling as one.
    version: str | None = None
    license: str | None = None


def normalise_doi(raw: str) -> str:
    """Strip the ways a DOI is written down and check what is left.

    Raises `DoiError` rather than returning None: a caller that got a bad DOI
    has a bad row, not an empty result, and the two settle differently.
    """
    doi = (raw or "").strip()
    lowered = doi.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            doi = doi[len(prefix) :]
            lowered = doi.lower()
            break
    doi = doi.strip().rstrip(".,;)")
    if not DOI_PATTERN.match(doi):
        raise DoiError(f"not a DOI: {raw!r}")
    # A DOI cannot contain whitespace or a fragment, and something claiming to
    # is trying to reach a path this code never intended to construct.
    if any(ch in doi for ch in ("#", "?", "\\")) or ".." in doi:
        raise DoiError(f"refusing a DOI with URL syntax in it: {raw!r}")
    return doi.lower()


@dataclasses.dataclass(frozen=True)
class ResolverSettings:
    """Deployment rather than code. Credentials from the environment (§11.11)."""

    #: Unpaywall requires it and rejects requests without one. OpenAlex uses it
    #: for the polite pool, which is faster and separately rate-limited.
    contact_email: str | None = None
    core_api_key: str | None = None
    #: Optional. Semantic Scholar answers without one at a low rate limit, which
    #: is fine for a crawl that resolves a DOI at a time and not for a backfill.
    semantic_scholar_key: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_env(cls) -> ResolverSettings:
        return cls(
            contact_email=os.environ.get("MERIDIAN_CONTACT_EMAIL") or None,
            core_api_key=os.environ.get("CORE_API_KEY") or None,
            semantic_scholar_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or None,
            timeout_s=_float_env("MERIDIAN_DOI_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        )


class DoiResolver:
    """§6.5's chain, in order, stopping at the first legally available copy."""

    def __init__(
        self,
        settings: ResolverSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or ResolverSettings()
        self._client = client
        self._owns_client = client is None
        # Last call per provider, on the monotonic clock. A lock per provider
        # rather than one shared: pacing Semantic Scholar must not also pace
        # Unpaywall, which has no such limit and is first in the chain.
        self._last_call: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def settings(self) -> ResolverSettings:
        return self._settings

    async def __aenter__(self) -> DoiResolver:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.timeout_s, follow_redirects=True
            )
        return self._client

    def _user_agent(self) -> str:
        """Identifiable and contactable (§14.2), which is also what the polite
        pools want. OpenAlex reads a mailto here; Unpaywall reads the query
        parameter instead, and gets both."""
        if self._settings.contact_email:
            return f"MeridianBot/0.1 (mailto:{self._settings.contact_email})"
        return "MeridianBot/0.1"

    async def resolve(self, raw_doi: str) -> OpenAccessCopy | None:
        """The first legally available copy, or None if there is not one.

        None means every provider that answered said no. `ResolutionUnavailable`
        means none of them answered — see the module docstring for why those two
        must not collapse into each other.
        """
        doi = normalise_doi(raw_doi)

        # Before any request: an arXiv DOI names its own copy, and asking a
        # provider would only be told the same thing more slowly. Deviates from
        # §6.5's literal order and lands on §6.5's answer.
        preprint = self._arxiv_copy(doi)
        if preprint is not None:
            return preprint

        answered = False
        throttled = False
        for name, provider in (
            ("unpaywall", self._unpaywall),
            ("openalex", self._openalex),
            ("core", self._core),
            ("europepmc", self._europepmc),
            ("semanticscholar", self._semantic_scholar),
        ):
            try:
                async with self._paced(name):
                    copy = await provider(doi)
            except _ProviderSkipped as exc:
                log.debug("skipping a provider", extra={"provider": name, "reason": str(exc)})
                continue
            except _ProviderRateLimited as exc:
                # Not an answer, and not a routine outage either: we were told
                # to slow down, so this provider's opinion is simply unknown.
                throttled = True
                log.info(
                    "a resolution provider rate-limited us",
                    extra={"provider": name, "doi": doi, "error": str(exc)},
                )
                continue
            except _ProviderUnreachable as exc:
                # Routine. §6.5's chain exists precisely so one API being down
                # is not the end of the question.
                log.info(
                    "a resolution provider did not answer",
                    extra={"provider": name, "doi": doi, "error": str(exc)},
                )
                continue

            answered = True
            if copy is not None:
                return copy

        if not answered:
            raise ResolutionUnavailable(f"no resolution provider answered for {doi}")
        if throttled:
            # The distinction this whole chain turns on. "Nobody has a copy"
            # settles the task `done` and never looks again; "we did not finish
            # asking" has to retry, or a burst of throttling quietly writes off
            # every paper it touched.
            raise ResolutionUnavailable(
                f"no copy found for {doi}, but a provider was rate-limited — not a final answer"
            )
        return None

    # -- the chain ---------------------------------------------------------

    def _arxiv_copy(self, doi: str) -> OpenAccessCopy | None:
        """§6.5 step 4, applied first when the DOI already says so."""
        if not doi.startswith(ARXIV_DOI_PREFIX):
            return None
        identifier = doi[len(ARXIV_DOI_PREFIX) :]
        if not identifier:
            return None
        return OpenAccessCopy(
            url=f"https://arxiv.org/pdf/{quote(identifier, safe='./')}",
            provider="arxiv",
            version="submittedVersion",
        )

    async def _unpaywall(self, doi: str) -> OpenAccessCopy | None:
        """§6.5 step 1. `best_oa_location` is Unpaywall's own ranking; taking it
        rather than re-ranking `oa_locations` keeps one opinion in one place."""
        if not self._settings.contact_email:
            raise _ProviderSkipped("MERIDIAN_CONTACT_EMAIL is not set")

        payload = await self._get_json(
            f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
            params={"email": self._settings.contact_email},
        )
        location = payload.get("best_oa_location") if isinstance(payload, dict) else None
        if not isinstance(location, dict):
            return None
        # `url_for_pdf` before `url`: a PDF is extractable and a landing page is
        # another hop that may itself be a paywall.
        url = location.get("url_for_pdf") or location.get("url")
        if not _usable(url):
            return None
        return OpenAccessCopy(
            url=url,
            provider="unpaywall",
            version=_text(location.get("version")),
            license=_text(location.get("license")),
        )

    async def _openalex(self, doi: str) -> OpenAccessCopy | None:
        """§6.5 step 2. Also the metadata source, so the polite pool matters."""
        payload = await self._get_json(
            f"https://api.openalex.org/works/doi:{quote(doi, safe='')}",
            params={"mailto": self._settings.contact_email}
            if self._settings.contact_email
            else None,
        )
        if not isinstance(payload, dict):
            return None
        for key in ("best_oa_location", "primary_location"):
            location = payload.get(key)
            if not isinstance(location, dict):
                continue
            if location.get("is_oa") is False:
                # Recorded as closed. Following the landing page anyway spends a
                # request to reach the paywall the API just described.
                continue
            url = location.get("pdf_url") or location.get("landing_page_url")
            if not _usable(url):
                continue
            return OpenAccessCopy(
                url=url,
                provider="openalex",
                version=_text(location.get("version")),
                license=_text(location.get("license")),
            )
        return None

    async def _core(self, doi: str) -> OpenAccessCopy | None:
        """§6.5 step 3 — institutional repositories, which is where the accepted
        manuscript usually is when the published version is closed."""
        if not self._settings.core_api_key:
            raise _ProviderSkipped("CORE_API_KEY is not set")

        payload = await self._get_json(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": f'doi:"{doi}"', "limit": 1},
            headers={"Authorization": f"Bearer {self._settings.core_api_key}"},
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        url = first.get("downloadUrl") or first.get("sourceFulltextUrls")
        if isinstance(url, list):
            url = url[0] if url else None
        if not _usable(url):
            return None
        return OpenAccessCopy(url=url, provider="core", version="acceptedVersion")

    async def _europepmc(self, doi: str) -> OpenAccessCopy | None:
        """Beyond §6.5's list. Europe PMC mirrors full text rather than pointing
        at it, so a copy here is one hop rather than two — and it holds work the
        general aggregators miss whenever a paper touches health, environment or
        transport epidemiology.

        The DOI goes into a quoted field query, so the quote characters are the
        thing to be careful about; `normalise_doi` has already refused anything
        carrying URL or query syntax.
        """
        payload = await self._get_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"},
        )
        results = ((payload or {}).get("resultList") or {}).get("result") or []
        if not isinstance(results, list) or not results:
            return None
        entry = results[0]
        if not isinstance(entry, dict):
            return None

        locations = ((entry.get("fullTextUrlList") or {}).get("fullTextUrl")) or []
        if not isinstance(locations, list):
            return None
        # Only `OA`. The list always includes a `doi` entry pointing back at the
        # publisher, marked "Subscription required" — following that would land
        # on the paywall this chain exists to route around.
        open_access = [
            entry
            for entry in locations
            if isinstance(entry, dict)
            and entry.get("availabilityCode") == "OA"
            and _usable(entry.get("url"))
        ]
        if not open_access:
            return None
        best = min(open_access, key=lambda e: 0 if e.get("documentStyle") == "pdf" else 1)
        return OpenAccessCopy(
            url=best["url"],
            provider="europepmc",
            version="publishedVersion",
            license=_text(entry.get("license")),
        )

    async def _semantic_scholar(self, doi: str) -> OpenAccessCopy | None:
        """Beyond §6.5's list, and last because it is the widest net.

        Worth having below Unpaywall rather than above it despite often giving a
        *better* URL — it indexes the repository PDF where Unpaywall frequently
        has only the repository's landing page. The order stays as §6.5 wrote it
        because Unpaywall's answer is authoritative about licence and version,
        and a landing page that is genuinely open still extracts.
        """
        headers = (
            {"x-api-key": self._settings.semantic_scholar_key}
            if self._settings.semantic_scholar_key
            else None
        )
        payload = await self._get_json(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='')}",
            params={"fields": "openAccessPdf,isOpenAccess"},
            headers=headers,
        )
        location = payload.get("openAccessPdf") if isinstance(payload, dict) else None
        if not isinstance(location, dict) or not _usable(location.get("url")):
            return None
        return OpenAccessCopy(
            url=location["url"],
            provider="semanticscholar",
            # `status` is the OA colour — gold, green, hybrid — not a version.
            # Recorded as the licence field says nothing about it either way.
            version=None,
            license=_text(location.get("license")),
        )

    @contextlib.asynccontextmanager
    async def _paced(self, provider: str):
        """Hold back until this provider's minimum interval has passed.

        Around the call rather than inside `_get_json` so a provider that makes
        no request at all — one that is skipped for a missing credential — costs
        nothing. Waiting before a call we are not going to make would pace the
        chain by its slowest provider even on a deployment that never uses it.
        """
        interval = PROVIDER_MIN_INTERVAL_S.get(provider, 0.0)
        if interval <= 0:
            yield
            return

        lock = self._locks.setdefault(provider, asyncio.Lock())
        async with lock:
            last = self._last_call.get(provider)
            now = time.monotonic()
            if last is not None and now - last < interval:
                await asyncio.sleep(interval - (now - last))
            try:
                yield
            finally:
                self._last_call[provider] = time.monotonic()

    # -- transport ---------------------------------------------------------

    async def _get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> object:
        """One GET, JSON out. Every failure becomes `_ProviderUnreachable`.

        Including a 404, which is what all three APIs return for "no such DOI".
        That is genuinely an answer rather than an outage — but it is the same
        answer as "no copy here", and treating it as unreachable would make one
        unknown DOI look like an outage to the caller. So it returns an empty
        body instead: answered, nothing found.
        """
        request_headers = {"User-Agent": self._user_agent(), "Accept": "application/json"}
        request_headers.update(headers or {})
        try:
            response = await self._http().get(url, params=params, headers=request_headers)
        except (httpx.HTTPError, OSError) as exc:
            raise _ProviderUnreachable(f"{type(exc).__name__}: {exc}") from exc

        if response.status_code == 404:
            return {}
        if response.status_code in (429, 403):
            # 403 as well as 429: several of these APIs answer 403 for "you are
            # over the anonymous quota" rather than 429, and treating that as a
            # flat refusal would silently drop every paper for the rest of the
            # window.
            raise _ProviderRateLimited(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise _ProviderUnreachable(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise _ProviderUnreachable(f"not JSON: {exc}") from exc


class _ProviderSkipped(RuntimeError):
    """This provider was not configured. Not a failure — it never ran."""


class _ProviderUnreachable(RuntimeError):
    """This provider could not be asked. The next one still can be."""


class _ProviderRateLimited(_ProviderUnreachable):
    """This provider refused *because we asked too fast*, which is not the same
    as it having no copy — and the difference decides whether the paper is lost.

    Measured, not assumed: resolving 75 real DOIs back to back found nothing at
    Semantic Scholar, while the same DOIs asked one per second returned an
    open-access PDF for every one of them. Unauthenticated S2 allows roughly a
    request a second, and a 429 folded in with connection errors is skipped
    silently — so the chain reports "no open-access copy", the task settles
    `done`, and the paper is never looked for again.
    """


def _usable(url: object) -> bool:
    """A URL worth queueing. The prefilter and `netguard` check it again."""
    return isinstance(url, str) and url.strip().lower().startswith(("http://", "https://"))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


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
