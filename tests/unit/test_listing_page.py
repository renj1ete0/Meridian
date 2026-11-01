"""Article links read off a latest-articles page (task B-255).

A listing watch queues what this keeps, every interval, from a page that is mostly
navigation. So most of this file is rejection: the section, tag and author pages, the other
sites, the media files and the short paths a front page is full of, beside the three shapes
an article link takes (a date, a long slug, a numbered series). Measured on seven real
latest-articles pages before it was written; see docs/features/discovery.md#watched-sources.
"""

from __future__ import annotations

import pytest

from worker.listingpage import (
    MAX_LINKS,
    SERIES_MIN,
    article_reason,
    is_navigation,
    parse_listing,
    same_site,
)
from worker.sitemaps import SitemapError

BASE = "https://www.outlet.test/singapore"


def page(*links: tuple[str, str]) -> bytes:
    anchors = "".join(f'<li><a href="{href}">{text}</a></li>' for href, text in links)
    return f"<!doctype html><html><body><nav><ul>{anchors}</ul></nav></body></html>".encode()


@pytest.mark.parametrize(
    "url,reason",
    [
        ("https://outlet.test/2026/11/01/new-bus-lanes/", "dated"),
        ("https://outlet.test/news/2026-11-01-bus-lanes", "dated"),
        ("https://outlet.test/a/20261101/lanes", "dated"),
        ("https://outlet.test/singapore/new-bus-lanes-open-in-the-east", "slug"),
        ("https://outlet.test/singapore/new-bus-lanes-open-east-6506081", "slug"),
        ("https://outlet.test/singapore/new-bus-lanes-open-east/6506081", "slug"),
        ("https://outlet.test/news/bus_lanes_open_east.html", "slug"),
    ],
)
def test_an_article_link_is_recognised_by_its_shape(url, reason) -> None:
    assert article_reason(url) == reason


@pytest.mark.parametrize(
    "url",
    [
        "https://outlet.test/",
        "https://outlet.test/singapore",
        "https://outlet.test/singapore/transport",
        "https://outlet.test/tag/new-bus-lanes-open-east",
        "https://outlet.test/author/a-reporter-with-long-name",
        "https://outlet.test/category/news/page/2",
        "https://outlet.test/about-us",
        "https://outlet.test/videos/new-bus-lanes-open-in-the-east",
        "https://outlet.test/images/2026/11/new-bus-lanes-open.jpg",
        "https://outlet.test/news/1-2-3-4",  # digits joined by hyphens are not words
    ],
)
def test_navigation_and_short_paths_are_not_articles(url) -> None:
    assert article_reason(url) is None


def test_a_year_alone_in_a_path_is_not_a_date() -> None:
    """`/2026/` is an archive index, and `/2026-budget` a topic page: neither names a day."""
    assert article_reason("https://outlet.test/archive2/2026/") is None
    assert article_reason("https://outlet.test/2026-budget") is None


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.outlet.test/a", True),
        ("https://today.outlet.test/a", True),
        ("https://notoutlet.test/a", False),
        ("https://outlet.test.evil.example/a", False),
    ],
)
def test_same_site_means_the_host_or_a_subdomain(url, expected) -> None:
    assert same_site(url, "outlet.test") is expected


def test_a_page_yields_its_articles_and_counts_what_it_left_out() -> None:
    parsed = parse_listing(
        page(
            ("/singapore/new-bus-lanes-open-in-the-east-6506081", "New bus lanes open in the east"),
            ("/2026/11/01/rail-line-delayed/", "Rail line delayed"),
            ("/singapore/transport", "Transport"),
            ("/tag/buses", "Buses"),
            ("https://elsewhere.test/some-other-story-on-a-site", "Elsewhere"),
            ("#top", "Back to top"),
            ("mailto:desk@outlet.test", "Write to us"),
        ),
        base_url=BASE,
    )

    assert parsed.kind == "listing" and not parsed.is_index
    assert parsed.urls == (
        "https://www.outlet.test/singapore/new-bus-lanes-open-in-the-east-6506081",
        "https://www.outlet.test/2026/11/01/rail-line-delayed/",
    )
    assert parsed.titles[parsed.urls[1]] == "Rail line delayed"
    assert parsed.dropped == {"not_an_article": 2, "other_site": 1}


def test_a_story_linked_twice_keeps_its_headline_not_its_label() -> None:
    """Image first, then a section label, then the headline: the headline is what the
    watch's filter must read, and it is the longest of the three."""
    href = "/2026/11/new-bus-lanes/"
    parsed = parse_listing(
        page((href, '<img src="x.jpg" alt="">'), (href, "News"), (href, "New bus lanes open")),
        base_url=BASE,
    )
    assert parsed.titles == {"https://www.outlet.test/2026/11/new-bus-lanes/": "New bus lanes open"}


def test_a_fragment_does_not_make_a_second_article() -> None:
    parsed = parse_listing(
        page(("/2026/11/a-story/", "A story"), ("/2026/11/a-story/#comments", "12 comments")),
        base_url=BASE,
    )
    assert parsed.urls == ("https://www.outlet.test/2026/11/a-story/",)


def test_a_numbered_series_under_one_parent_is_a_list_of_articles() -> None:
    """An agency numbers its releases (`pr26-67`): no date, no slug, many siblings."""
    links = [(f"/news/media/pr26-{n}/", f"Release {n}") for n in range(60, 60 + SERIES_MIN)]
    parsed = parse_listing(page(*links), base_url=BASE)
    assert len(parsed.urls) == SERIES_MIN


def test_a_few_numbered_links_are_not_a_series() -> None:
    links = [(f"/news/media/pr26-{n}/", f"Release {n}") for n in range(SERIES_MIN - 1)]
    assert parse_listing(page(*links), base_url=BASE).urls == ()


def test_numbered_navigation_is_never_a_series() -> None:
    """Pagination is the commonest run of numbered siblings on a listing."""
    links = [(f"/category/news/page/{n}", str(n)) for n in range(2, 12)]
    assert parse_listing(page(*links), base_url=BASE).urls == ()
    assert is_navigation("https://outlet.test/category/news/page/2")


def test_an_archive_sized_page_is_cut_and_says_so() -> None:
    links = [(f"/2026/11/story-{n}/", f"Story {n}") for n in range(MAX_LINKS + 5)]
    parsed = parse_listing(page(*links), base_url=BASE)
    assert len(parsed.urls) == MAX_LINKS and parsed.truncated


@pytest.mark.parametrize("content", [b"", b"   ", b'{"not": "html"}', b"plain text, no markup"])
def test_what_is_not_a_page_is_refused_as_an_unreadable_sitemap_is(content) -> None:
    with pytest.raises(SitemapError):
        parse_listing(content, base_url=BASE)
