"""What an HTML page declares about itself, and how it beats trafilatura (task B-234).

Made-up pages. The trafilatura cases are the three defects measured on the corpus: a
copyright year read as 1 January, a corporate author cut at "of", and a site name
never read because its JSON key was misnamed, so every publisher was the address.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from worker.extract.base import ExtractedDocument
from worker.extract.declared import read_declared
from worker.extract.html import extract_html, with_declared

BODY = (
    "Commuters on the harbour ferry route reported shorter waits after the timetable change. " * 6
)
URL = "https://news.velde.example/2021/ferry-waits"
FETCHED = dt.date(2026, 6, 15)


def page(head: str = "", body: str = BODY, extra: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><title>Ferry waits fall</title>{head}</head>
<body><article><h1>Ferry waits fall</h1><p>{body}</p>{extra}</article>
<footer><p>Copyright 2026 Velde Gazette</p></footer></body></html>"""


def ld(data: object) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


# --------------------------------------------------------------------------
# Sources of each field
# --------------------------------------------------------------------------


def test_highwire_tags_declare_a_scholarly_work() -> None:
    head = (
        '<meta name="citation_title" content="Wait times on a harbour ferry">'
        '<meta name="citation_author" content="Odum, Pell">'
        '<meta name="citation_author" content="Castellan, Mira">'
        '<meta name="citation_publication_date" content="2021/03/09">'
        '<meta name="citation_publisher" content="Velde University Press">'
    )
    declared = read_declared(page(head))

    assert declared.title.value == "Wait times on a harbour ferry"
    assert (declared.author.value, declared.author.source) == (
        "Odum, Pell; Castellan, Mira",
        "citation",
    )
    assert declared.publication_date.value == dt.date(2021, 3, 9)
    assert declared.publication_date.source == "citation"
    assert declared.publisher.value == "Velde University Press"


def test_json_ld_names_are_resolved_through_graph_references() -> None:
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": "#site",
                "name": "Velde Gazette",
                "datePublished": "2001-01-01",
            },
            {"@type": "Organization", "@id": "#org", "name": "Velde Gazette Ltd"},
            {"@type": "Person", "@id": "#p1", "name": "Mira Castellan"},
            {
                "@type": "NewsArticle",
                "headline": "Ferry waits fall",
                "datePublished": "2021-03-09T08:00:00+08:00",
                "author": [{"@id": "#p1"}],
                "publisher": {"@id": "#org"},
            },
        ],
    }
    declared = read_declared(page(ld(graph)))

    assert declared.author.value == "Mira Castellan"
    assert declared.publisher.value == "Velde Gazette Ltd"
    # The site's own item is not the page: its date is not the article's.
    assert declared.publication_date.value == dt.date(2021, 3, 9)
    assert declared.publication_date.source == "jsonld"


def test_an_article_inside_a_web_page_wins_over_the_page() -> None:
    items = [
        {"@type": "WebPage", "datePublished": "2015-02-01"},
        {"@type": "Article", "datePublished": "2021-03-09"},
    ]
    assert read_declared(page(ld(items))).publication_date.value == dt.date(2021, 3, 9)


def test_open_graph_and_dublin_core_fill_in() -> None:
    head = (
        '<meta property="article:published_time" content="2021-03-09T08:00:00Z">'
        '<meta name="DC.creator" content="Velde Transit Authority">'
        '<meta property="og:site_name" content="Velde Gazette">'
        '<meta property="og:locale" content="ms_MY">'
    )
    declared = read_declared(page(head))

    assert declared.publication_date.source == "article"
    assert (declared.author.value, declared.author.source) == ("Velde Transit Authority", "dc")
    assert (declared.publisher.value, declared.publisher.source) == ("Velde Gazette", "meta")
    assert declared.language.value == "ms"


def test_several_author_tags_are_all_kept() -> None:
    head = '<meta name="author" content="Pell Odum"><meta name="author" content="Mira Castellan">'
    assert read_declared(page(head)).author.value == "Pell Odum; Mira Castellan"


def test_microdata_is_read_when_the_page_agrees_with_itself() -> None:
    extra = '<time itemprop="datePublished" datetime="2021-03-09">9 March</time>'
    declared = read_declared(page(extra=extra))
    assert (declared.publication_date.value, declared.publication_date.source) == (
        dt.date(2021, 3, 9),
        "microdata",
    )


# --------------------------------------------------------------------------
# What is not taken
# --------------------------------------------------------------------------


def test_a_listings_many_microdata_dates_describe_its_items_not_itself() -> None:
    extra = (
        '<time itemprop="datePublished" datetime="2021-03-09">a</time>'
        '<time itemprop="datePublished" datetime="2020-11-02">b</time>'
    )
    assert read_declared(page(extra=extra)).publication_date is None


def test_citation_tags_in_the_body_describe_a_reference() -> None:
    extra = '<meta name="citation_author" content="Odum, Pell">'
    assert read_declared(page(extra=extra)).author is None


def test_a_year_alone_is_not_completed() -> None:
    head = '<meta name="citation_publication_date" content="2021">'
    assert read_declared(page(head)).publication_date is None


def test_broken_json_ld_is_skipped_not_repaired() -> None:
    head = '<script type="application/ld+json">{"@type": "Article", "datePublished": </script>'
    assert read_declared(page(head)).publication_date is None


def test_placeholder_authors_and_platform_publishers_fall_through() -> None:
    head = (
        '<meta name="author" content="admin">'
        '<meta name="generator" content="Pagecraft 9.2">'
        '<meta property="og:site_name" content="Pagecraft">'
    )
    declared = read_declared(page(head))

    assert declared.author is None
    assert declared.publisher is None
    assert declared.generator == "Pagecraft 9.2"


def test_a_later_valid_candidate_is_used_when_an_earlier_one_is_refused() -> None:
    head = (
        '<meta name="DC.publisher" content="WordPress">'
        '<meta property="og:site_name" content="Velde Gazette">'
    )
    assert read_declared(page(head)).publisher.value == "Velde Gazette"


@pytest.mark.parametrize("content", ["", "<html>", "\x00\x01", "<head><meta name=author></head>"])
def test_malformed_pages_declare_nothing_rather_than_raising(content: str) -> None:
    declared = read_declared(content)
    assert declared.author is None and declared.publication_date is None


# --------------------------------------------------------------------------
# Through extraction: precedence, rules and provenance
# --------------------------------------------------------------------------


def test_a_copyright_year_is_not_a_publication_date() -> None:
    document = extract_html(page(), URL, fetched=FETCHED)

    assert document.publication_date is None
    assert document.metadata_rejected.get("publication_date") == "2026-01-01"


def test_a_declared_date_beats_the_inferred_one_and_says_so() -> None:
    head = '<meta property="article:published_time" content="2021-03-09T08:00:00Z">'
    document = extract_html(page(head), URL, fetched=FETCHED)

    assert document.publication_date == dt.date(2021, 3, 9)
    assert document.metadata_from["publication_date"] == "article"


def test_a_declared_date_after_the_fetch_is_refused_and_recorded() -> None:
    head = '<meta name="citation_publication_date" content="2027-01-05">'
    document = extract_html(page(head), URL, fetched=FETCHED)

    assert document.publication_date is None
    assert document.metadata_rejected["publication_date"] == "2027-01-05"


def test_a_corporate_author_keeps_its_whole_name() -> None:
    """trafilatura cuts "Northwind Department of Health" at "of"; the tag says it in full."""
    head = '<meta name="author" content="Northwind Department of Health">'
    document = extract_html(page(head), URL, fetched=FETCHED)

    assert document.author == "Northwind Department of Health"
    assert document.metadata_from["author"] == "meta"


def test_the_site_name_is_the_publisher_not_the_address() -> None:
    head = '<meta property="og:site_name" content="Velde Gazette">'
    document = extract_html(page(head), URL, fetched=FETCHED)

    assert document.publisher == "Velde Gazette"
    assert document.metadata_from["publisher"] == "meta"


def test_with_no_site_name_the_address_stands_and_is_marked_inferred() -> None:
    document = extract_html(page(), URL, fetched=FETCHED)

    assert document.publisher == "velde.example"
    assert document.metadata_from["publisher"] == "host"


def test_a_declared_title_fills_one_trafilatura_did_not_find() -> None:
    html = page('<meta name="DC.title" content="Wait times on a harbour ferry">')
    document = with_declared(ExtractedDocument(text=BODY), html, fetched=FETCHED)

    assert document.title == "Wait times on a harbour ferry"
    assert document.metadata_from["title"] == "dc"


def test_a_title_trafilatura_found_is_not_replaced() -> None:
    html = page('<meta name="DC.title" content="Wait times on a harbour ferry">')
    document = with_declared(ExtractedDocument(title="Ferry waits fall"), html, fetched=FETCHED)

    assert document.title == "Ferry waits fall"
    assert "title" not in document.metadata_from
