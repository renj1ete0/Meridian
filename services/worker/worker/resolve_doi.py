"""Paper full-text resolution (task P1-14, spec §6.5, §6.4).

Resolves a DOI to a *legally available* copy: Unpaywall, OpenAlex, CORE, the preprint
servers, then Europe PMC and Semantic Scholar. One provider failing means the next is
tried; all answering with no copy is a final answer; none answering is transient. A
provider without its credential is skipped. The APIs are called directly; the URLs they
return go through the crawler. See docs/features/discovery.md#resolution.
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

#: Minimum seconds between calls to one provider, by name, per process. Measured:
#: anonymous Semantic Scholar allows about a request a second (`B-67`).
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


class ResolutionThrottled(ResolutionUnavailable):
    """Not a final answer because a provider told us to slow down (`B-67`).

    ``retry_after_s`` is how long until the soonest throttled provider may be asked
    again; the task retries after that, not on the queue's ordinary backoff.
    """

    def __init__(self, message: str, *, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


#: A provider that rate-limited us is left alone this long when it names no
#: Retry-After, doubling with each refusal in a row up to the cap (`B-67`).
COOLDOWN_BASE_S = 60.0
COOLDOWN_MAX_S = 1800.0


@dataclasses.dataclass(frozen=True)
class OpenAccessCopy:
    """Where a legally available copy of one paper lives."""

    url: str
    #: Which link in the chain answered: provenance for how the URL was found (§5.2).
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
        # Cooling providers (`B-67`): until when, and how many refusals in a
        # row, on the monotonic clock. Per process, like the pacing.
        self._cool_until: dict[str, float] = {}
        self._refusals: dict[str, int] = {}

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
        """Identifiable and contactable (§14.2), which is also what the polite pools want.

        OpenAlex reads a mailto here; Unpaywall reads the query parameter instead, and gets both.
        """
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
        waits: list[float] = []
        for name, provider in (
            ("unpaywall", self._unpaywall),
            ("openalex", self._openalex),
            ("core", self._core),
            ("europepmc", self._europepmc),
            ("semanticscholar", self._semantic_scholar),
        ):
            cooling = self._cool_until.get(name, 0.0) - time.monotonic()
            if cooling > 0:
                # Still told to slow down: its opinion is unknown without asking.
                waits.append(cooling)
                continue
            try:
                async with self._paced(name):
                    copy = await provider(doi)
            except _ProviderSkipped as exc:
                log.debug("skipping a provider", extra={"provider": name, "reason": str(exc)})
                continue
            except _ProviderRateLimited as exc:
                # Not an answer, and not a routine outage either: we were told
                # to slow down, so this provider's opinion is simply unknown.
                wait = self._cool(name, exc.retry_after_s)
                waits.append(wait)
                log.info(
                    "a resolution provider rate-limited us",
                    extra={"provider": name, "doi": doi, "error": str(exc), "cooling_s": wait},
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
            self._refusals.pop(name, None)
            if copy is not None:
                return copy

        if waits:
            # "Nobody has a copy" settles the task; "we did not finish asking" must retry,
            # after the cooldown, or throttling writes papers off.
            said = "no copy found" if answered else "no provider answered"
            raise ResolutionThrottled(
                f"{said} for {doi}, but a provider was rate-limited — not a final answer",
                retry_after_s=min(waits),
            )
        if not answered:
            raise ResolutionUnavailable(f"no resolution provider answered for {doi}")
        return None

    def _cool(self, name: str, retry_after_s: float | None) -> float:
        """Leave a provider alone after a refusal. Returns for how long."""
        refusals = self._refusals.get(name, 0) + 1
        self._refusals[name] = refusals
        if retry_after_s is not None and retry_after_s > 0:
            wait = min(retry_after_s, COOLDOWN_MAX_S)
        else:
            wait = min(COOLDOWN_BASE_S * 2 ** (refusals - 1), COOLDOWN_MAX_S)
        self._cool_until[name] = time.monotonic() + wait
        return wait

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
        """§6.5 step 1.

        `best_oa_location` is Unpaywall's own ranking; taking it rather than re-ranking
        `oa_locations` keeps one opinion in one place.
        """
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
        """§6.5 step 3: institutional repositories.

        That is where the accepted manuscript usually is when the published version is closed.
        """
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
        """Beyond §6.5's list: Europe PMC mirrors full text rather than pointing at it.

        The DOI goes into a quoted field query; `normalise_doi` has already refused URL
        or query syntax.
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

        Often a better URL than Unpaywall's, but below it because Unpaywall is
        authoritative about licence and version.
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

        Around the call, so a provider skipped for a missing credential costs nothing.
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

        Except a 404 ("no such DOI"), which returns an empty body: answered, nothing found.
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
            # 403 as well as 429: several of these APIs answer 403 for "over the anonymous
            # quota".
            raise _ProviderRateLimited(
                f"HTTP {response.status_code}",
                retry_after_s=_retry_after(response.headers.get("Retry-After")),
            )
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


def _retry_after(raw: str | None) -> float | None:
    """Seconds from a Retry-After header, when it gives seconds.

    The HTTP-date form is rare from these APIs and falls back to the doubling cooldown.
    """
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


class _ProviderRateLimited(_ProviderUnreachable):
    """This provider refused *because we asked too fast*.

    Not the same as having no copy: folded into "unreachable", it would settle the
    task `done` and lose the paper. See docs/features/discovery.md#resolution.
    """

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


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
