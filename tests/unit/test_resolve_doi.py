"""Paper full-text resolution (task P1-14, spec §6.5).

§6.5 gives the chain — Unpaywall, OpenAlex, CORE, preprints — and one word that
shapes all of it: resolve to a **legally available** copy. So the assertions
here are mostly about the seams between links in that chain, because those are
where the behaviour is decided:

- a provider that errors must not end the search
- a provider with no credential must be skipped, not failed
- "every provider said no" and "no provider answered" must not collapse, because
  one settles the queue row `done` and the other settles it `retry`

And one about the input: a DOI reaches a URL path, and DOIs come from crawled
reference lists, so a page can put anything it likes in one.
"""

from __future__ import annotations

import httpx
import pytest

from worker.resolve_doi import (
    DoiError,
    DoiResolver,
    ResolutionUnavailable,
    ResolverSettings,
    normalise_doi,
)

DOI = "10.1016/j.trd.2021.103013"
OA_PDF = "https://repository.example.test/paper.pdf"


def resolver_for(routes, **settings) -> DoiResolver:
    """A resolver whose providers answer from a routing table keyed by host."""

    def handler(request: httpx.Request) -> httpx.Response:
        answer = routes.get(request.url.host)
        if answer is None:
            return httpx.Response(404, json={})
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, json={})
        return httpx.Response(200, json=answer)

    kwargs = {"contact_email": "ops@example.test", **settings}
    return DoiResolver(
        ResolverSettings(**kwargs),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def unpaywall(url: str | None, **fields) -> dict:
    location = {"url_for_pdf": url, **fields} if url else None
    return {"best_oa_location": location}


def openalex(url: str | None, **fields) -> dict:
    location = {"pdf_url": url, "is_oa": True, **fields} if url else None
    return {"best_oa_location": location}


# --------------------------------------------------------------------------
# The DOI itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "10.1016/j.trd.2021.103013",
        "https://doi.org/10.1016/j.trd.2021.103013",
        "doi:10.1016/j.trd.2021.103013",
        "  10.1016/J.TRD.2021.103013  ",
        "10.1016/j.trd.2021.103013.",
    ],
)
def test_the_ways_a_doi_is_written_down_all_normalise(raw: str) -> None:
    """A reference list writes them five ways, and two spellings of one DOI are
    two queue rows, two resolutions and two fetches of the same paper."""
    assert normalise_doi(raw) == DOI


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not a doi", "11.1016/x", "10.1/x", "doi.org", "10.1016/"],
)
def test_something_that_is_not_a_doi_is_refused(raw: str) -> None:
    with pytest.raises(DoiError):
        normalise_doi(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "10.1016/../../etc/passwd",
        "10.1016/x?callback=evil",
        "10.1016/x#fragment",
        "10.1016/x\\y",
    ],
)
def test_a_doi_carrying_url_syntax_is_refused(raw: str) -> None:
    """DOIs come from crawled reference lists, and a hostile page can write
    whatever it likes in one. This value reaches a URL path."""
    with pytest.raises(DoiError, match="URL syntax"):
        normalise_doi(raw)


async def test_a_bad_doi_never_reaches_the_network() -> None:
    """Refused before the first request, so a page full of junk references
    cannot spend one API call per line."""

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"a request went out for a bad DOI: {request.url}")

    resolver = DoiResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))

    with pytest.raises(DoiError):
        await resolver.resolve("nonsense")


# --------------------------------------------------------------------------
# The chain, in order
# --------------------------------------------------------------------------


async def test_unpaywall_answers_first() -> None:
    resolver = resolver_for(
        {
            "api.unpaywall.org": unpaywall(OA_PDF, version="publishedVersion", license="cc-by"),
            "api.openalex.org": openalex("https://elsewhere.test/other.pdf"),
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None
    assert (copy.url, copy.provider) == (OA_PDF, "unpaywall")
    assert (copy.version, copy.license) == ("publishedVersion", "cc-by")


async def test_openalex_answers_when_unpaywall_has_nothing() -> None:
    resolver = resolver_for(
        {"api.unpaywall.org": unpaywall(None), "api.openalex.org": openalex(OA_PDF)}
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "openalex"


async def test_core_is_skipped_without_a_key() -> None:
    """A provider with no credential never ran — which is different from one
    that ran and failed, and must not stop the chain reporting an answer."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": unpaywall(None),
            "api.openalex.org": openalex(None),
            "api.core.ac.uk": {"results": [{"downloadUrl": OA_PDF}]},
        },
        core_api_key=None,
    )

    assert await resolver.resolve(DOI) is None


async def test_core_answers_when_it_has_a_key() -> None:
    resolver = resolver_for(
        {
            "api.unpaywall.org": unpaywall(None),
            "api.openalex.org": openalex(None),
            "api.core.ac.uk": {"results": [{"downloadUrl": OA_PDF}]},
        },
        core_api_key="secret",
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "core"


async def test_unpaywall_is_skipped_without_a_contact_email() -> None:
    """Unpaywall requires one and refuses requests without it, so asking anyway
    would spend a request to be told off."""
    resolver = resolver_for(
        {"api.unpaywall.org": unpaywall(OA_PDF), "api.openalex.org": openalex(OA_PDF)},
        contact_email=None,
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "openalex"


async def test_an_arxiv_doi_resolves_without_a_request() -> None:
    """An arXiv DOI names its own copy. Asking Unpaywall would be told the same
    thing more slowly, so §6.5's step 4 is applied first when it applies."""

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"an arXiv DOI still hit the network: {request.url}")

    resolver = DoiResolver(
        ResolverSettings(contact_email="ops@example.test"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(explode)),
    )

    copy = await resolver.resolve("10.48550/arXiv.2301.00001")

    assert copy is not None
    assert copy.provider == "arxiv"
    assert copy.url == "https://arxiv.org/pdf/2301.00001"


# --------------------------------------------------------------------------
# The three ways it can fail, which settle three different ways
# --------------------------------------------------------------------------


async def test_one_provider_failing_does_not_end_the_chain() -> None:
    """Which is what a chain is for. §6.5 lists four places precisely because
    any one of them can be down."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": httpx.ConnectError("refused"),
            "api.openalex.org": openalex(OA_PDF),
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "openalex"


async def test_a_provider_returning_5xx_is_also_just_skipped() -> None:
    resolver = resolver_for({"api.unpaywall.org": 503, "api.openalex.org": openalex(OA_PDF)})

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "openalex"


async def test_every_provider_answering_no_is_an_answer() -> None:
    """None, not an exception. The paper is paywalled today and will be
    tomorrow, so the task settles `done` rather than retrying forever."""
    resolver = resolver_for(
        {"api.unpaywall.org": unpaywall(None), "api.openalex.org": openalex(None)}
    )

    assert await resolver.resolve(DOI) is None


async def test_an_unknown_doi_is_an_answer_not_an_outage() -> None:
    """All three APIs 404 for a DOI they have never heard of. Treating that as
    unreachable would make one bad reference look like an API outage."""
    resolver = resolver_for({})  # the handler 404s everything

    assert await resolver.resolve(DOI) is None


def all_down(**settings) -> DoiResolver:
    """A resolver whose every provider is unreachable, whatever the list is.

    Not a host routing table: a test that named the hosts would start passing
    for the wrong reason the moment a provider was added, because the unnamed
    one would answer 404 and count as an answer.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    kwargs = {"contact_email": "ops@example.test", **settings}
    return DoiResolver(
        ResolverSettings(**kwargs),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_no_provider_answering_at_all_raises() -> None:
    """The transient case, and the only one that should retry."""
    with pytest.raises(ResolutionUnavailable):
        await all_down().resolve(DOI)


async def test_a_chain_with_nothing_configured_and_nothing_reachable_raises() -> None:
    """Every provider skipped is not the same as every provider saying no —
    nothing was asked, so nothing is known."""
    with pytest.raises(ResolutionUnavailable):
        await all_down(contact_email=None, core_api_key=None).resolve(DOI)


async def test_one_provider_still_answering_is_enough_to_avoid_a_retry() -> None:
    """The boundary between the two: as long as *something* answered, "no copy"
    is a real answer and the task is done rather than retried."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json={})
        raise httpx.ConnectError("refused")

    resolver = DoiResolver(
        ResolverSettings(contact_email="ops@example.test"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await resolver.resolve(DOI) is None
    assert "api.semanticscholar.org" in calls


# --------------------------------------------------------------------------
# What comes back
# --------------------------------------------------------------------------


async def test_a_pdf_is_preferred_over_a_landing_page() -> None:
    """A PDF is extractable; a landing page is another hop that may itself be
    the paywall this whole chain exists to route around."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": {
                "best_oa_location": {"url_for_pdf": OA_PDF, "url": "https://x.test/landing"}
            }
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.url == OA_PDF


async def test_a_landing_page_is_used_when_there_is_no_pdf() -> None:
    resolver = resolver_for(
        {"api.unpaywall.org": {"best_oa_location": {"url": "https://x.test/landing"}}}
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.url == "https://x.test/landing"


async def test_a_location_openalex_calls_closed_is_not_followed() -> None:
    """Following it spends a request to reach the paywall the API just
    described."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": unpaywall(None),
            "api.openalex.org": {
                "best_oa_location": {"pdf_url": "https://publisher.test/x.pdf", "is_oa": False},
                "primary_location": {
                    "landing_page_url": "https://publisher.test/x",
                    "is_oa": False,
                },
            },
        }
    )

    assert await resolver.resolve(DOI) is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.test/a", "", None, 42])
async def test_a_location_that_is_not_an_http_url_is_ignored(url: object) -> None:
    """Provider output is not a schema, and this value becomes a queue row."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": {"best_oa_location": {"url_for_pdf": url}},
            "api.openalex.org": openalex(None),
        }
    )

    assert await resolver.resolve(DOI) is None


async def test_a_provider_returning_the_wrong_shape_does_not_crash_the_chain() -> None:
    """An API that changed, or a proxy that returned something else entirely."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": {"best_oa_location": "a string"},
            "api.openalex.org": openalex(OA_PDF),
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "openalex"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_settings_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_CONTACT_EMAIL", "ops@example.test")
    monkeypatch.setenv("CORE_API_KEY", "k")
    monkeypatch.setenv("MERIDIAN_DOI_TIMEOUT_S", "9")

    settings = ResolverSettings.from_env()

    assert (settings.contact_email, settings.core_api_key, settings.timeout_s) == (
        "ops@example.test",
        "k",
        9.0,
    )


def test_an_unset_environment_leaves_the_chain_partly_configured(monkeypatch) -> None:
    """Which is a supported state: OpenAlex and the preprint rule need no
    credential, so a bare deployment still resolves something."""
    monkeypatch.delenv("MERIDIAN_CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("CORE_API_KEY", raising=False)

    settings = ResolverSettings.from_env()

    assert settings.contact_email is None and settings.core_api_key is None


def test_a_nonsense_timeout_fails_loudly(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_DOI_TIMEOUT_S", "eventually")

    with pytest.raises(RuntimeError, match="MERIDIAN_DOI_TIMEOUT_S"):
        ResolverSettings.from_env()


# --------------------------------------------------------------------------
# Beyond §6.5's four
# --------------------------------------------------------------------------


def europepmc(*locations: dict, license: str | None = None) -> dict:
    entry = {"fullTextUrlList": {"fullTextUrl": list(locations)}}
    if license:
        entry["license"] = license
    return {"resultList": {"result": [entry]}}


def oa(url: str, style: str = "pdf") -> dict:
    return {"availabilityCode": "OA", "documentStyle": style, "url": url, "site": "Europe_PMC"}


def subscription(url: str) -> dict:
    return {"availabilityCode": "S", "documentStyle": "doi", "url": url, "site": "DOI"}


def exhausted() -> dict:
    """Everything above Europe PMC in the chain, answering "no copy"."""
    return {"api.unpaywall.org": unpaywall(None), "api.openalex.org": openalex(None)}


async def test_europepmc_answers_when_the_aggregators_have_nothing() -> None:
    """It mirrors full text rather than pointing at it, so a copy here is one
    hop rather than two — and it holds work the general aggregators miss."""
    resolver = resolver_for(
        {**exhausted(), "www.ebi.ac.uk": europepmc(oa(OA_PDF), license="cc-by")}
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None
    assert (copy.url, copy.provider, copy.license) == (OA_PDF, "europepmc", "cc-by")


async def test_europepmcs_publisher_link_is_never_followed() -> None:
    """Its result list always carries a `doi` entry pointing back at the
    publisher, marked "Subscription required". Following it lands on exactly
    the paywall this chain exists to route around.
    """
    resolver = resolver_for(
        {
            **exhausted(),
            "www.ebi.ac.uk": europepmc(subscription("https://publisher.test/paywalled")),
        }
    )

    assert await resolver.resolve(DOI) is None


async def test_europepmc_prefers_the_pdf_over_the_html_rendering() -> None:
    resolver = resolver_for(
        {
            **exhausted(),
            "www.ebi.ac.uk": europepmc(
                oa("https://europepmc.test/article", style="html"),
                oa(OA_PDF, style="pdf"),
            ),
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.url == OA_PDF


async def test_europepmc_falls_back_to_html_when_there_is_no_pdf() -> None:
    """An HTML rendering still extracts. Refusing it would discard a copy over
    a format the pipeline reads perfectly well."""
    html_url = "https://europepmc.test/article"
    resolver = resolver_for({**exhausted(), "www.ebi.ac.uk": europepmc(oa(html_url, style="html"))})

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.url == html_url


async def test_a_doi_europepmc_has_never_heard_of_is_not_an_error() -> None:
    """It answers 200 with an empty result list rather than 404."""
    resolver = resolver_for({**exhausted(), "www.ebi.ac.uk": {"resultList": {"result": []}}})

    assert await resolver.resolve(DOI) is None


async def test_semantic_scholar_is_the_last_net() -> None:
    """It indexes the repository PDF where Unpaywall often has only the
    repository's landing page."""
    resolver = resolver_for(
        {
            **exhausted(),
            "www.ebi.ac.uk": {"resultList": {"result": []}},
            "api.semanticscholar.org": {
                "openAccessPdf": {"url": OA_PDF, "status": "GREEN", "license": "CCBYNCND"}
            },
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None
    assert (copy.url, copy.provider, copy.license) == (OA_PDF, "semanticscholar", "CCBYNCND")


async def test_semantic_scholar_with_no_pdf_is_no_copy() -> None:
    """`isOpenAccess` without an `openAccessPdf` means it knows the paper is
    open somewhere and does not know where — which is not a URL to queue."""
    resolver = resolver_for(
        {
            **exhausted(),
            "www.ebi.ac.uk": {"resultList": {"result": []}},
            "api.semanticscholar.org": {"isOpenAccess": True, "openAccessPdf": None},
        }
    )

    assert await resolver.resolve(DOI) is None


async def test_the_api_key_rides_in_the_header_when_there_is_one() -> None:
    """Unauthenticated Semantic Scholar is rate-limited hard enough to matter
    for anything more than a DOI at a time."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            seen["key"] = request.headers.get("x-api-key")
            return httpx.Response(200, json={"openAccessPdf": {"url": OA_PDF}})
        return httpx.Response(404, json={})

    resolver = DoiResolver(
        ResolverSettings(contact_email="ops@example.test", semantic_scholar_key="s2-key"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await resolver.resolve(DOI)

    assert seen["key"] == "s2-key"


async def test_the_spec_order_still_wins() -> None:
    """The two additions sit *below* §6.5's four, not in place of them.

    Unpaywall's answer is authoritative about licence and version, and a
    reordering that quietly preferred a later provider would change what the
    corpus records about every paper it cites.
    """
    resolver = resolver_for(
        {
            "api.unpaywall.org": unpaywall(OA_PDF, version="publishedVersion"),
            "www.ebi.ac.uk": europepmc(oa("https://europepmc.test/other.pdf")),
            "api.semanticscholar.org": {"openAccessPdf": {"url": "https://s2.test/other.pdf"}},
        }
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "unpaywall"


# --------------------------------------------------------------------------
# Rate limiting: the failure that looks like an answer
# --------------------------------------------------------------------------
#
# Found by measurement, not by reading. Resolving 75 real DOIs back to back
# returned no copy from Semantic Scholar; the same DOIs asked one per second
# returned an open-access PDF for every one. A 429 folded in with connection
# errors is skipped silently, so the chain reports "no open-access copy", the
# task settles `done`, and the paper is never looked for again.


@pytest.mark.parametrize("status", [429, 403])
async def test_being_throttled_is_not_an_answer(status: int) -> None:
    """`None` here would settle the task `done` and lose the paper for good.
    Nothing was learned, so the task has to come back."""
    resolver = resolver_for({**exhausted(), "api.semanticscholar.org": status})

    with pytest.raises(ResolutionUnavailable, match="rate-limited"):
        await resolver.resolve(DOI)


async def test_a_copy_found_before_the_throttling_still_wins() -> None:
    """The chain stops at the first copy, so a later provider's quota never
    matters — being throttled only counts when nothing was found."""
    resolver = resolver_for(
        {"api.unpaywall.org": unpaywall(OA_PDF), "api.semanticscholar.org": 429}
    )

    copy = await resolver.resolve(DOI)

    assert copy is not None and copy.provider == "unpaywall"


async def test_a_throttled_provider_is_distinguishable_from_a_dead_one() -> None:
    """Both raise `ResolutionUnavailable` and both retry — but the messages have
    to differ, because one is fixed by waiting and the other by an API key."""
    dead = all_down()
    throttled = resolver_for({**exhausted(), "api.semanticscholar.org": 429})

    with pytest.raises(ResolutionUnavailable) as first:
        await dead.resolve(DOI)
    with pytest.raises(ResolutionUnavailable) as second:
        await throttled.resolve(DOI)

    assert "rate-limited" not in str(first.value)
    assert "rate-limited" in str(second.value)


async def test_calls_to_one_provider_are_paced() -> None:
    """Retrying into the same wall is not a fix. The interval is what makes the
    retry land somewhere different."""
    import time as _time

    resolver = resolver_for(
        {**exhausted(), "api.semanticscholar.org": {"openAccessPdf": {"url": OA_PDF}}}
    )
    resolver._last_call["semanticscholar"] = _time.monotonic()

    started = _time.monotonic()
    await resolver.resolve(DOI)
    elapsed = _time.monotonic() - started

    assert elapsed >= 1.0, "the second call to a rate-limited provider was not paced"


async def test_a_skipped_provider_costs_no_delay() -> None:
    """Pacing wraps the call, not the decision to make one. A deployment with no
    CORE key must not pay CORE's interval on every DOI."""
    import time as _time

    resolver = resolver_for({"api.unpaywall.org": unpaywall(OA_PDF)}, core_api_key=None)
    resolver._last_call["core"] = _time.monotonic()

    started = _time.monotonic()
    await resolver.resolve(DOI)

    assert _time.monotonic() - started < 0.2


def test_every_paced_provider_is_one_the_chain_actually_calls() -> None:
    """A typo in `PROVIDER_MIN_INTERVAL_S` is a limit that silently never
    applies — which is exactly the bug this table was added to fix."""
    import inspect

    from worker.resolve_doi import PROVIDER_MIN_INTERVAL_S

    chain = inspect.getsource(DoiResolver.resolve)
    unused = [name for name in PROVIDER_MIN_INTERVAL_S if f'"{name}"' not in chain]
    assert not unused, f"paced but never called: {unused}"
