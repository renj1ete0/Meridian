"""URL normalisation and shape filtering (task P1-06, spec §6.4).

The database half of the prefilter is in `tests/integration/test_prefilter.py`.
What is tested here is the part that decides whether two strings are the same
URL, which is the part everything else depends on: an already-seen check over
un-normalised URLs finds nothing, and the duplicates it lets through are
invisible until someone counts rows.

Normalisation is deliberately conservative, so the tests come in two halves —
what it must fold together, and what it must leave alone.
"""

from __future__ import annotations

import pytest

from worker.prefilter import (
    SKIP_EXTENSIONS,
    TRACKING_PARAMS,
    Verdict,
    has_skipped_extension,
    normalise_url,
)

# --------------------------------------------------------------------------
# What must be folded together
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "https://Example.TEST/a",
        "https://EXAMPLE.test/a",
        "https://example.test:443/a",
        "https://example.test/a#section",
        "https://example.test/a#",
        "  https://example.test/a  ",
        "https://user:secret@example.test/a",
    ],
)
def test_spellings_of_one_url_normalise_to_one_string(variant: str) -> None:
    """Each of these would otherwise be its own queue row, fetch and source."""
    assert normalise_url(variant) == "https://example.test/a"


def test_credentials_are_dropped_rather_than_carried_into_the_queue() -> None:
    """A password in a crawl target is a mistake or an attempt to get it logged.

    Either way it must not end up in `queue.url_or_query`, which is read in
    Admin and printed in every log line about the task.
    """
    assert "secret" not in normalise_url("https://user:secret@example.test/a")


@pytest.mark.parametrize("param", sorted(TRACKING_PARAMS)[:8])
def test_tracking_parameters_are_stripped(param: str) -> None:
    """One campaign-tagged link and one bare link are the same page."""
    assert normalise_url(f"https://example.test/a?{param}=xyz") == "https://example.test/a"


def test_a_real_parameter_survives_alongside_a_tracking_one() -> None:
    """Stripping too much would fetch the wrong page, which is the worse error."""
    assert (
        normalise_url("https://example.test/search?utm_source=news&q=transit&page=2")
        == "https://example.test/search?q=transit&page=2"
    )


def test_an_empty_path_becomes_root() -> None:
    assert normalise_url("https://example.test") == "https://example.test/"
    assert normalise_url("https://example.test?q=1") == "https://example.test/?q=1"


def test_a_non_default_port_is_kept() -> None:
    """It addresses a different service, so folding it would fetch the wrong one."""
    assert normalise_url("https://example.test:8443/a") == "https://example.test:8443/a"
    assert normalise_url("http://example.test:8080/a") == "http://example.test:8080/a"


# --------------------------------------------------------------------------
# What must be left alone
# --------------------------------------------------------------------------


def test_a_trailing_slash_is_not_stripped() -> None:
    """`/a` and `/a/` are different resources in principle.

    Folding them trades a duplicate — visible, cheap, caught later by the
    novelty gate — for a page that silently never enters the corpus. The two
    errors are not symmetric, so the conservative one wins.
    """
    assert normalise_url("https://example.test/a/") != normalise_url("https://example.test/a")


def test_path_case_is_preserved() -> None:
    """Most servers treat paths case-sensitively; the host is the part that is not."""
    assert normalise_url("https://example.test/CaseSensitive") == (
        "https://example.test/CaseSensitive"
    )


def test_query_parameter_order_is_preserved() -> None:
    """Sorting would dedupe more and could break a signed URL."""
    assert normalise_url("https://example.test/a?b=2&a=1") == "https://example.test/a?b=2&a=1"


def test_a_blank_valued_parameter_survives() -> None:
    """`?draft=` and `?` are different requests to some servers."""
    assert normalise_url("https://example.test/a?draft=") == "https://example.test/a?draft="


# --------------------------------------------------------------------------
# What is not a URL at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "/relative/path",
        "not a url",
        "ftp://example.test/a",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "mailto:a@b.test",
        "data:text/html,<p>x</p>",
        "https://",
        "http:///nohost",
    ],
)
def test_anything_that_is_not_a_fetchable_url_is_refused(url: str) -> None:
    """Refused here so it never becomes a queue row that can only ever fail.

    `netguard` refuses these again at fetch time; this saves the row rather than
    the request.
    """
    assert normalise_url(url) is None


def test_a_url_that_cannot_be_parsed_returns_none_rather_than_raising() -> None:
    """A worker that runs for weeks must not die on one malformed href."""
    assert normalise_url("https://[not-an-ipv6/a") is None


# --------------------------------------------------------------------------
# Extensions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ext", [".jpg", ".png", ".css", ".js", ".zip", ".mp4", ".woff2"])
def test_assets_are_recognised_by_extension(ext: str) -> None:
    """Queueing one buys a `content_type_rejected` at the price of a real request."""
    assert has_skipped_extension(f"https://example.test/thing{ext}")


def test_the_check_is_case_insensitive() -> None:
    assert has_skipped_extension("https://example.test/PHOTO.JPG")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/report.pdf",
        "https://example.test/data.csv",
        "https://example.test/paper.docx",
        "https://example.test/page.html",
        "https://example.test/page",
        "https://example.test/",
        "https://example.test/a.b/c",  # the dot is in a directory, not the file
    ],
)
def test_documents_are_not_mistaken_for_assets(url: str) -> None:
    """The formats this corpus reads, or will read once `P1-08`/`P1-09` land."""
    assert not has_skipped_extension(url)


def test_a_query_string_does_not_hide_the_extension() -> None:
    assert has_skipped_extension("https://example.test/photo.jpg?size=large")
    assert not has_skipped_extension("https://example.test/page?file=x.jpg")


def test_no_skipped_extension_is_one_this_corpus_reads() -> None:
    """A completeness probe against the fetch policy's own allowlist.

    Adding `.pdf` here would silently stop the crawler ever queueing a PDF, and
    nothing else would notice — the frontier would just get quieter.
    """
    readable = {
        ".pdf",
        ".html",
        ".htm",
        ".txt",
        ".csv",
        ".json",
        ".xml",
        ".md",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".epub",
    }
    assert not (SKIP_EXTENSIONS & readable)


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------


def test_a_verdict_counts_everything_it_saw() -> None:
    """The log line that explains a thin frontier."""
    verdict = Verdict(kept=("a", "b"), dropped={"already_seen": 5, "blocked_domain": 1})

    assert verdict.considered == 8


def test_an_empty_verdict_is_coherent() -> None:
    assert Verdict().considered == 0
    assert Verdict().kept == ()
