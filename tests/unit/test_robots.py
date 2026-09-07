"""robots.txt parsing, matching and caching (task P1-04, spec §14.2).

§14.2 states respecting robots.txt as a commitment, which makes "which rule
matches this path" a question with exactly one right answer. These tests are the
specification of that answer: RFC 9309 §2.2 (longest match wins, ``Allow``
breaks a tie, ``*`` and ``$`` are the only metacharacters) and §2.3.1.3 (4xx
allows everything, 5xx refuses everything).

The `test_matches_rfc_9309_where_the_stdlib_did_not` case is the reason this
module exists rather than a call to `urllib.robotparser`; see the module
docstring in `worker/robots.py`.
"""

from __future__ import annotations

import pytest

from worker.robots import (
    ALLOW_ALL,
    ROBOTS_ERROR_TTL_S,
    ROBOTS_MAX_BYTES,
    RobotsCache,
    RobotsRules,
    parse,
    product_token,
    robots_url,
)

AGENT = "MeridianBot/0.1 (+https://example.org/contact)"


# --------------------------------------------------------------------------
# Path matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,allowed,why",
    [
        ("/open/page", True, "no rule mentions it"),
        ("/private/secret", False, "Disallow: /private/"),
        ("/private/notice", True, "a longer Allow beats the shorter Disallow"),
        ("/reports/annual.pdf", False, "Disallow: /*.pdf$ — wildcard then anchor"),
        ("/reports/annual.pdf.html", True, "$ anchors the end, so this is not a .pdf"),
        ("/search?q=buses", False, "Disallow: /search"),
    ],
)
def test_matches_rfc_9309_where_the_stdlib_did_not(path: str, allowed: bool, why: str) -> None:
    """Every case here is one urllib.robotparser answers differently on 3.12.

    Wildcards and longest-match arrived in the stdlib only in 3.13, and this
    project supports 3.12, so these answers had to stop depending on which
    interpreter the container shipped.
    """
    rules = parse(
        "User-agent: *\n"
        "Disallow: /private/\n"
        "Disallow: /*.pdf$\n"
        "Disallow: /search\n"
        "Allow: /private/notice\n",
        AGENT,
    )
    assert rules.allows(path) is allowed, why


def test_a_tie_on_length_goes_to_allow() -> None:
    """RFC 9309 §2.2.2's tiebreak, and the direction is not arbitrary.

    An equal-length Allow displaces a Disallow; the reverse must not happen,
    whatever order the two appear in the file.
    """
    forwards = parse("User-agent: *\nDisallow: /docs\nAllow: /docs\n", AGENT)
    backwards = parse("User-agent: *\nAllow: /docs\nDisallow: /docs\n", AGENT)
    assert forwards.allows("/docs/a") is True
    assert backwards.allows("/docs/a") is True


def test_path_metacharacters_are_literal() -> None:
    """A path is full of regex metacharacters that must not be interpreted.

    `Disallow: /a+b` refers to a path with a plus sign in it, not to one or
    more `a`s — and a parser that compiled it naively would refuse `/aaab`
    while happily fetching the page the site actually asked it to leave alone.
    """
    rules = parse("User-agent: *\nDisallow: /a+b\nDisallow: /q.php\n", AGENT)
    assert rules.allows("/a+b") is False
    assert rules.allows("/aaab") is True
    assert rules.allows("/q.php") is False
    assert rules.allows("/qxphp") is True


def test_an_empty_disallow_forbids_nothing() -> None:
    """`Disallow:` with no value is the idiom for "everything is permitted".

    Read as a pattern it would match every path and lock the crawler out of the
    entire site — the exact inversion of what the site said.
    """
    rules = parse("User-agent: *\nDisallow:\n", AGENT)
    assert rules.allows("/anything") is True


def test_an_html_soft_404_yields_no_rules() -> None:
    """Sites serve their 404 page at /robots.txt with a 200, and it must be harmless.

    www.example-org.test does exactly this, so it is not a hypothetical. Markup has
    no lines shaped like a robots directive, so nothing is extracted and the
    origin ends up permitted — which is the same answer a real 404 gives.
    """
    page = (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta http-equiv="content-type" content="text/html; charset=UTF-8">\n'
        '<link rel="stylesheet" href="/style.css" type="text/css">\n'
        "</head><body><p>Page not found</p></body></html>\n"
    )
    rules = parse(page, AGENT)
    assert rules.rules == ()
    assert rules.allows("/anything") is True


def test_a_file_with_no_rules_allows_everything() -> None:
    assert parse("", AGENT).allows("/x") is True
    assert parse("# just a comment\n", AGENT).allows("/x") is True


def test_comments_and_blank_lines_are_ignored() -> None:
    rules = parse(
        "# leading comment\n\nUser-agent: *   # trailing\nDisallow: /admin  # why not\n", AGENT
    )
    assert rules.allows("/admin/x") is False
    assert rules.allows("/public") is True


def test_the_query_string_participates_in_matching() -> None:
    """`Disallow: /*?` is a common way to exclude parameterised URLs."""
    rules = parse("User-agent: *\nDisallow: /*?\n", AGENT)
    assert rules.allows("/page?ref=x") is False
    assert rules.allows("/page") is True


def test_a_full_url_matches_the_same_as_its_path() -> None:
    rules = parse("User-agent: *\nDisallow: /private/\n", AGENT)
    assert rules.allows("https://example.test/private/x") is False
    assert rules.allows("https://example.test/other") is True


# --------------------------------------------------------------------------
# Group selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent,expected",
    [
        ("MeridianBot/0.1 (+https://example.org)", "meridianbot"),
        ("MeridianBot", "meridianbot"),
        ("Meridian Bot/2", "meridian"),
        ("  meridianbot/9  ", "meridianbot"),
    ],
)
def test_the_product_token_ignores_version_and_contact(agent: str, expected: str) -> None:
    assert product_token(agent) == expected


def test_a_named_group_suppresses_the_wildcard_group_entirely() -> None:
    """The rule most implementations get wrong, and it is not a small difference.

    A site with rules for us *and* stricter rules for `*` is saying the `*`
    rules are for somebody else. Obeying both would be over-cautious; obeying
    only `*` would be a breach. RFC 9309 §2.2.1: one group applies.
    """
    text = "User-agent: *\nDisallow: /\n\nUser-agent: MeridianBot\nDisallow: /admin\n"
    rules = parse(text, AGENT)
    assert rules.allows("/research/paper") is True, "the '*' blanket ban is not ours to obey"
    assert rules.allows("/admin/x") is False, "our own group still binds"


def test_the_longest_matching_agent_name_wins() -> None:
    text = (
        "User-agent: meridian\nDisallow: /a\n\n"
        "User-agent: meridianbot\nDisallow: /b\n\n"
        "User-agent: *\nDisallow: /c\n"
    )
    rules = parse(text, AGENT)
    assert rules.allows("/a") is True
    assert rules.allows("/b") is False
    assert rules.allows("/c") is True


def test_agent_names_are_matched_case_insensitively() -> None:
    rules = parse("User-agent: MERIDIANBOT\nDisallow: /x\n", AGENT)
    assert rules.allows("/x") is False


def test_consecutive_agent_lines_share_one_group() -> None:
    """Two user-agent lines with no rule between them name one group."""
    rules = parse("User-agent: somebot\nUser-agent: meridianbot\nDisallow: /shared\n", AGENT)
    assert rules.allows("/shared") is False


def test_repeated_groups_for_one_agent_are_merged() -> None:
    text = "User-agent: meridianbot\nDisallow: /a\n\nUser-agent: meridianbot\nDisallow: /b\n"
    rules = parse(text, AGENT)
    assert rules.allows("/a") is False
    assert rules.allows("/b") is False


def test_rules_before_any_agent_line_belong_to_nobody() -> None:
    """A stray rule at the top of the file must not leak into our group."""
    rules = parse("Disallow: /orphan\nUser-agent: *\nDisallow: /named\n", AGENT)
    assert rules.allows("/orphan") is True
    assert rules.allows("/named") is False


# --------------------------------------------------------------------------
# Crawl-delay and sitemaps
# --------------------------------------------------------------------------


def test_crawl_delay_comes_from_the_matched_group_only() -> None:
    text = "User-agent: *\nCrawl-delay: 30\n\nUser-agent: meridianbot\nDisallow: /x\n"
    assert parse(text, AGENT).crawl_delay_s is None, "the '*' group's delay is not ours"
    assert parse("User-agent: *\nCrawl-delay: 30\n", AGENT).crawl_delay_s == 30


@pytest.mark.parametrize("value,expected", [("0.5", 0.5), ("10", 10.0), ("nonsense", None)])
def test_crawl_delay_parsing(value: str, expected: float | None) -> None:
    assert parse(f"User-agent: *\nCrawl-delay: {value}\n", AGENT).crawl_delay_s == expected


def test_sitemaps_are_global_not_per_group() -> None:
    """A Sitemap line belongs to the file, not to whichever group precedes it."""
    text = (
        "Sitemap: https://example.test/sitemap.xml\n"
        "User-agent: somebodyelse\nDisallow: /\n"
        "Sitemap: https://example.test/news.xml\n"
    )
    rules = parse(text, AGENT)
    assert rules.sitemaps == (
        "https://example.test/sitemap.xml",
        "https://example.test/news.xml",
    )


# --------------------------------------------------------------------------
# robots_url
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.test/a/b?c=1", "https://example.test/robots.txt"),
        ("http://example.test:8080/x", "http://example.test:8080/robots.txt"),
        # Per origin, not per registrable domain: these are separate files.
        ("https://data.example.test/x", "https://data.example.test/robots.txt"),
    ],
)
def test_robots_url_is_per_origin(url: str, expected: str) -> None:
    assert robots_url(url) == expected


# --------------------------------------------------------------------------
# The cache, and what happens when robots.txt cannot be read
# --------------------------------------------------------------------------


class RecordingFetch:
    """Stands in for the rate-limited fetch the cache is given."""

    def __init__(self, *results) -> None:
        self.calls: list[str] = []
        self.policies: list[object] = []
        self._results = list(results)

    async def __call__(self, url, policy):
        self.calls.append(url)
        self.policies.append(policy)
        return self._results[min(len(self.calls) - 1, len(self._results) - 1)]


def result(outcome="success", *, status=200, body=b"", url="https://example.test/robots.txt"):
    from worker.fetch import FetchResult

    return FetchResult(
        requested_url=url, final_url=url, outcome=outcome, status_code=status, content=body
    )


async def test_robots_is_fetched_once_per_origin_then_cached(policy) -> None:
    fetch = RecordingFetch(result(body=b"User-agent: *\nDisallow: /private/\n"))
    cache = RobotsCache(fetch)

    first = await cache.rules_for("https://example.test/a", policy())
    second = await cache.rules_for("https://example.test/b", policy())

    assert first.allows("/private/x") is False
    assert second is first
    assert fetch.calls == ["https://example.test/robots.txt"], "one fetch, not one per URL"


async def test_separate_origins_get_separate_entries(policy) -> None:
    fetch = RecordingFetch(result(body=b"User-agent: *\nDisallow: /\n"))
    cache = RobotsCache(fetch)

    await cache.rules_for("https://a.test/x", policy())
    await cache.rules_for("https://b.test/x", policy())

    assert fetch.calls == ["https://a.test/robots.txt", "https://b.test/robots.txt"]


async def test_a_404_allows_everything(policy) -> None:
    """§2.3.1.3: a site with no robots.txt has stated no exclusions."""
    cache = RobotsCache(RecordingFetch(result("http_error", status=404)))
    rules = await cache.rules_for("https://example.test/x", policy())

    assert rules.allows("/anything") is True
    assert rules.unreachable is False


@pytest.mark.parametrize(
    "outcome,status",
    [("http_error", 500), ("http_error", 503), ("timeout", None), ("connection_error", None)],
)
async def test_an_unreadable_robots_refuses_the_origin(policy, outcome, status) -> None:
    """§2.3.1.3 is asymmetric on purpose, and this is the half that costs pages.

    A server that is currently broken has not granted permission. Assuming it
    would have is how a crawler gets itself banned — so the refusal is deliberate,
    and it is why the error TTL is minutes rather than a day.
    """
    cache = RobotsCache(RecordingFetch(result(outcome, status=status)))
    rules = await cache.rules_for("https://example.test/x", policy())

    assert rules.allows("/anything") is False
    assert rules.unreachable is True


async def test_a_refusal_is_cached_briefly_and_a_success_for_a_day(policy) -> None:
    """The two TTLs must differ, or one 503 removes a domain for a day."""
    assert ROBOTS_ERROR_TTL_S < 3600 < RobotsCache(RecordingFetch())._ttl_s


async def test_an_ssrf_refusal_does_not_become_permission(policy) -> None:
    """netguard refusing robots.txt must not read as "no rules, crawl away"."""
    cache = RobotsCache(RecordingFetch(result("unsafe_target", status=None)))
    rules = await cache.rules_for("https://example.test/x", policy())

    assert rules.allows("/anything") is False


async def test_an_oversized_robots_is_treated_as_empty(policy) -> None:
    """RFC 9309 §2.5 caps parsing; a huge file is not a refusal to serve one."""
    cache = RobotsCache(RecordingFetch(result("too_large", status=200)))
    rules = await cache.rules_for("https://example.test/x", policy())

    assert rules.allows("/anything") is True


async def test_robots_is_fetched_under_a_relaxed_policy(policy) -> None:
    """The file itself must not be blocked by rules meant for content.

    A content-type allowlist would refuse a robots.txt served as
    application/octet-stream, and `require_https_final` would refuse a domain
    whose robots.txt redirects to http — in both cases taking the whole origin
    out of the crawl over a file that carries nothing worth protecting.
    """
    fetch = RecordingFetch(result(body=b"User-agent: *\n"))
    cache = RobotsCache(fetch)
    await cache.rules_for(
        "https://example.test/x",
        policy(allowed_content_types=["text/html"], render_js="always", max_page_bytes=99_000_000),
    )

    used = fetch.policies[0]
    assert used.allowed_content_types == []
    assert used.render_js == "never", "a browser for a text file is pure waste"
    assert used.require_https_final is False
    assert used.max_page_bytes == ROBOTS_MAX_BYTES


async def test_priming_the_cache_skips_the_fetch(policy) -> None:
    fetch = RecordingFetch(result())
    cache = RobotsCache(fetch)
    cache.prime("https://example.test/", ALLOW_ALL)

    rules = await cache.rules_for("https://example.test/deep/page", policy())

    assert rules is ALLOW_ALL
    assert fetch.calls == []


def test_rules_with_no_entries_permit_everything() -> None:
    assert RobotsRules().allows("/x") is True
