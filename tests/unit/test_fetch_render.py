"""Static vs browser dispatch, and the JS-dependence heuristic (task P1-03, §6.4).

§6.4's first operational constraint is "don't render every page": Playwright plus
Chromium against 16GB shared is the heaviest thing the ingestion node can do, and
most of this corpus — government PDFs, statistical releases, academic pages —
needs none of it. `render_js: auto` is what keeps that constraint, so the tests
that matter here are the ones asserting the browser is *not* used.

The Crawl4AI double returns the shape the real service returns; the fields were
taken from a live 0.9.2 response, not from its documentation.
"""

from __future__ import annotations

import httpx
import pytest
from http_doubles import streamed

from worker.fetch import Fetcher, looks_javascript_dependent, visible_text

PUBLIC = "93.184.216.34"

ARTICLE = (
    "<html><head><title>Walkability</title><script src='/analytics.js'></script></head>"
    "<body><h1>Walking distance to transit</h1>"
    "<p>" + ("Pedestrian catchment analysis for the north-east line. " * 20) + "</p>"
    "</body></html>"
)

SPA_SHELL = (
    "<html><head><script src='/bundle.js'></script></head><body><div id='root'></div></body></html>"
)

NOSCRIPT_PAGE = (
    "<html><body><noscript>You need to enable JavaScript to run this app.</noscript>"
    "<div class='mount'></div><script src='/app.js'></script></body></html>"
)

THIN_STATIC = "<html><body><h1>404</h1><p>Not found.</p></body></html>"


class FakeBrowser:
    """Stands in for Crawl4AIClient, recording what it was asked to render."""

    def __init__(self, result: dict | None = None, raises: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._result = result
        self._raises = raises

    async def crawl(self, url: str, policy) -> dict:
        self.calls.append(url)
        if self._raises is not None:
            raise self._raises
        return self._result or crawl_result(url)

    async def aclose(self) -> None:
        pass


def crawl_result(url: str, **overrides) -> dict:
    """The subset of a real Crawl4AI 0.9.2 result the fetcher reads."""
    base = {
        "success": True,
        "status_code": 200,
        "url": url,
        "redirected_url": url,
        "error_message": "",
        "html": "<html><body><h1>Rendered</h1><p>Content from the browser.</p></body></html>",
        "response_headers": {"content-type": "text/html", "etag": '"xyz"'},
        "markdown": {"raw_markdown": "# Rendered", "fit_markdown": "# Rendered"},
        "links": {"internal": [], "external": []},
    }
    return {**base, **overrides}


# --------------------------------------------------------------------------
# visible_text / looks_javascript_dependent
# --------------------------------------------------------------------------


def test_visible_text_drops_the_things_a_reader_never_sees() -> None:
    html = (
        "<html><!-- a comment --><head><style>body{color:red}</style>"
        "<script>var x = 'not text';</script></head>"
        "<body><p>Real   text</p><template><p>unrendered</p></template></body></html>"
    )
    text = visible_text(html)
    assert text == "Real text"
    for absent in ("color:red", "var x", "a comment", "unrendered"):
        assert absent not in text


@pytest.mark.parametrize(
    "page,expected,why",
    [
        (ARTICLE, False, "a page with real prose is not worth a browser, scripts or not"),
        (SPA_SHELL, True, "an empty #root div is the signature of client-side rendering"),
        (NOSCRIPT_PAGE, True, "the page says so itself"),
        (THIN_STATIC, False, "short, but there is nothing a browser would add"),
        ("<html><body></body></html>", False, "empty and scriptless — nothing to render"),
    ],
)
def test_javascript_dependence_heuristic(page: str, expected: bool, why: str) -> None:
    assert looks_javascript_dependent(page) is expected, why


def test_the_text_floor_short_circuits_before_any_other_signal() -> None:
    """Ordering matters: this is what stops `auto` rendering the whole crawl.

    A long article that also happens to contain a `<div id="app">` must still
    take the cheap path.
    """
    page = (
        "<html><body><div id='app'></div>"
        f"<p>{'word ' * 200}</p><script src='/a.js'></script></body></html>"
    )
    assert len(visible_text(page)) >= 500
    assert looks_javascript_dependent(page) is False


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


async def test_render_never_uses_the_static_path_even_for_a_shell(
    policy, resolver, recorder
) -> None:
    browser = FakeBrowser()
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "text/html"}, chunks=[SPA_SHELL.encode()])
    )
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/", policy(render_js="never"))

    assert result.render_mode == "http"
    assert browser.calls == []


async def test_auto_does_not_reach_for_the_browser_on_an_ordinary_page(
    policy, resolver, recorder
) -> None:
    """The constraint that keeps the node alive: most pages cost one HTTP request."""
    browser = FakeBrowser()
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "text/html"}, chunks=[ARTICLE.encode()])
    )
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/", policy(render_js="auto"))

    assert result.ok
    assert result.render_mode == "http"
    assert browser.calls == [], "a browser launch for a page that did not need one"


async def test_auto_re_fetches_a_shell_through_the_browser(policy, resolver, recorder) -> None:
    browser = FakeBrowser()
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "text/html"}, chunks=[SPA_SHELL.encode()])
    )
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/", policy(render_js="auto"))

    assert result.ok
    assert result.render_mode == "browser"
    assert browser.calls == ["https://example.test/"]
    assert b"Content from the browser" in result.content
    assert result.browser_payload is not None, (
        "the markdown and links Crawl4AI already produced must not be thrown away, "
        "or extraction pays for the page twice"
    )


async def test_auto_keeps_the_static_body_when_the_browser_fails(
    policy, resolver, recorder
) -> None:
    """Degraded beats absent: a worse extraction is better than no page at all."""
    browser = FakeBrowser(raises=httpx.ConnectError("crawl4ai is down"))
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "text/html"}, chunks=[SPA_SHELL.encode()])
    )
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/", policy(render_js="auto"))

    assert result.ok
    assert result.render_mode == "http"
    assert result.content == SPA_SHELL.encode()


async def test_auto_does_not_render_a_non_html_response(policy, resolver, recorder) -> None:
    """A PDF has no JS to run; sending one to a browser is pure waste."""
    browser = FakeBrowser()
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "application/pdf"}, chunks=[b"%PDF-1.7"])
    )
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/report.pdf", policy(render_js="auto"))

    assert result.ok
    assert browser.calls == []


async def test_auto_does_not_render_after_a_failed_static_fetch(policy, resolver, recorder) -> None:
    """A 404 is a 404 in a browser too, and a refused target must stay refused."""
    browser = FakeBrowser()
    rec = recorder(lambda r: streamed(404, headers={"content-type": "text/html"}))
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/", policy(render_js="auto"))

    assert result.outcome == "http_error"
    assert browser.calls == []


async def test_always_falls_back_to_static_when_no_browser_is_configured(
    policy, resolver, recorder
) -> None:
    """A worker with no Crawl4AI is degraded, not broken."""
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "text/html"}, chunks=[ARTICLE.encode()])
    )
    async with Fetcher(client=rec.client(), resolver=resolver({"example.test": [PUBLIC]})) as f:
        result = await f.fetch("https://example.test/", policy(render_js="always"))

    assert result.ok
    assert result.render_mode == "http"


async def test_always_reports_the_browser_failure_when_a_browser_exists(
    policy, resolver, recorder
) -> None:
    """With a browser configured and failing, the failure is the answer.

    Silently substituting the static body would hide a broken Crawl4AI behind a
    stream of low-quality extractions — exactly the kind of silent degradation
    §13.4 says an unattended system must not have.
    """
    browser = FakeBrowser(raises=httpx.ConnectError("down"))
    rec = recorder(
        lambda r: streamed(200, headers={"content-type": "text/html"}, chunks=[ARTICLE.encode()])
    )
    async with Fetcher(
        client=rec.client(), resolver=resolver({"example.test": [PUBLIC]}), browser=browser
    ) as f:
        result = await f.fetch("https://example.test/", policy(render_js="always"))

    assert result.outcome == "connection_error"
    assert result.render_mode == "browser"


# --------------------------------------------------------------------------
# The browser path is still guarded
# --------------------------------------------------------------------------


async def test_the_browser_is_never_handed_an_unsafe_target(policy, resolver) -> None:
    browser = FakeBrowser()
    async with Fetcher(resolver=resolver({"lan.test": ["10.0.0.9"]}), browser=browser) as f:
        result = await f.fetch_rendered("https://lan.test/", policy())

    assert result.outcome == "unsafe_target"
    assert browser.calls == [], "the check must happen before the URL is handed over"


async def test_where_the_browser_landed_is_checked_after_the_fact(policy, resolver) -> None:
    """Crawl4AI follows its own redirects, so the landing URL is judged on return.

    Not pinning — see the module docstring and `P1-25` — but it is the only
    thing standing between a redirect chain and an internal address on this path.
    """
    browser = FakeBrowser(
        crawl_result("https://start.test/", redirected_url="http://192.168.0.1/admin")
    )
    async with Fetcher(
        resolver=resolver({"start.test": [PUBLIC], "192.168.0.1": ["192.168.0.1"]}),
        browser=browser,
    ) as f:
        result = await f.fetch_rendered("https://start.test/", policy())

    assert result.outcome == "unsafe_target"
    assert result.content == b""


async def test_a_plaintext_landing_url_is_refused_on_the_browser_path_too(policy, resolver) -> None:
    browser = FakeBrowser(
        crawl_result("https://start.test/", redirected_url="http://start.test/insecure")
    )
    async with Fetcher(resolver=resolver({"start.test": [PUBLIC]}), browser=browser) as f:
        result = await f.fetch_rendered("https://start.test/", policy(require_https_final=True))

    assert result.outcome == "unsafe_target"


async def test_the_browser_path_honours_the_size_cap(policy, resolver) -> None:
    browser = FakeBrowser(crawl_result("https://example.test/", html="<p>" + "x" * 50_000))
    async with Fetcher(resolver=resolver({"example.test": [PUBLIC]}), browser=browser) as f:
        result = await f.fetch_rendered("https://example.test/", policy(max_page_bytes=1000))

    assert result.outcome == "too_large"


async def test_the_browser_path_honours_the_content_type_allowlist(policy, resolver) -> None:
    browser = FakeBrowser(
        crawl_result(
            "https://example.test/",
            response_headers={"content-type": "application/octet-stream"},
        )
    )
    async with Fetcher(resolver=resolver({"example.test": [PUBLIC]}), browser=browser) as f:
        result = await f.fetch_rendered(
            "https://example.test/", policy(allowed_content_types=["text/html"])
        )

    assert result.outcome == "content_type_rejected"


async def test_a_crawl_that_reports_failure_is_not_treated_as_a_page(policy, resolver) -> None:
    browser = FakeBrowser(
        crawl_result(
            "https://example.test/",
            success=False,
            status_code=None,
            error_message="page timed out",
            html="",
        )
    )
    async with Fetcher(resolver=resolver({"example.test": [PUBLIC]}), browser=browser) as f:
        result = await f.fetch_rendered("https://example.test/", policy())

    assert not result.ok
    assert "page timed out" in result.detail
