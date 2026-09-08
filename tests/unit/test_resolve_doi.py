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


async def test_no_provider_answering_at_all_raises() -> None:
    """The transient case, and the only one that should retry."""
    resolver = resolver_for(
        {
            "api.unpaywall.org": httpx.ConnectError("refused"),
            "api.openalex.org": httpx.ConnectError("refused"),
        }
    )

    with pytest.raises(ResolutionUnavailable):
        await resolver.resolve(DOI)


async def test_a_chain_with_nothing_configured_and_nothing_reachable_raises() -> None:
    """Every provider skipped is not the same as every provider saying no —
    nothing was asked, so nothing is known."""
    resolver = resolver_for(
        {"api.openalex.org": httpx.ConnectError("refused")},
        contact_email=None,
        core_api_key=None,
    )

    with pytest.raises(ResolutionUnavailable):
        await resolver.resolve(DOI)


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
