"""What kind of document a source is (task B-59).

The expensive error here is a document called a listing: it loses its passages
from search, the map and synthesis. So most of this file is rejection — shapes
that carry a lot of links and are *not* listings — driven through the real HTML
extractor, because the classifier reads links as the extractor leaves them in
the text and a fixture of hand-written markdown would test a format nothing
produces.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from meridian_core.models.source import DOC_KIND
from worker.extract.base import ExtractedDocument, Page
from worker.extract.dockind import (
    LINK_SHARE,
    RECORD_GAP_CV,
    RECORD_GROUPS,
    Evidence,
    Verdict,
    classify,
    evidence_from,
    link_shape,
    listing_rule,
    record_doc_kind,
)
from worker.extract.html import extract_html, has_scholarly_meta, og_type

PROSE = (
    "The survey followed each household for a full year and recorded every trip, "
    "its purpose, its length and the mode chosen, which lets the analysis separate "
    "habit from circumstance in a way a single-day diary cannot. "
)


def html(body: str, *, title: str = "A page", head: str = "") -> bytes:
    return (
        f"<!doctype html><html lang='en'><head><title>{title}</title>{head}</head>"
        f"<body><main>{body}</main></body></html>"
    ).encode()


def kind_of(
    content: bytes,
    url: str,
    *,
    tier: str = "institutional",
    media_type: str = "text/html",
) -> Verdict:
    document = extract_html(content, url)
    assert document.has_text, "the fixture extracted to nothing; it tests no rule"
    return classify(evidence_from(url, document, media_type=media_type, tier=tier))


# --------------------------------------------------------------------------
# Listings are recognised
# --------------------------------------------------------------------------


def a_feed_of_records(n: int = 25) -> bytes:
    """New items, each an identifier link, a title, author links and a summary."""
    records = "".join(
        f'<dt><a href="/item/{i}">[{i}]</a> <a href="/item/{i}">item:{i:05d}</a></dt>'
        f"<dd><div>Title: A study of question number {i} and what it found</div>"
        f'<div><a href="/people/a{i}">First Author</a>, <a href="/people/b{i}">Second Author</a>'
        f"</div><p>{PROSE * (1 + i % 3)}</p></dd>"
        for i in range(1, n + 1)
    )
    return html(f"<h1>New submissions</h1><dl>{records}</dl>", title="New submissions")


def test_a_feed_of_records_with_summaries_is_a_listing() -> None:
    """The hard case: mostly prose by volume, but one record after another.

    Its link share is low — the summaries outweigh the links — so only the
    evenly recurring link rows give it away.
    """
    verdict = kind_of(a_feed_of_records(), "https://example.test/feed/latest")

    assert verdict.kind == "listing"
    assert verdict.rule == "record_rows"
    assert verdict.shape.link_share < LINK_SHARE


def test_a_page_that_is_mostly_links_is_a_listing() -> None:
    # In an <article> and with a count beside each: the extractor drops a bare
    # list of links as page furniture, so that shape never reaches the rules.
    items = "".join(
        f'<li><a href="/collection/{i}">Collection of working papers number {i}</a> (12)</li>'
        for i in range(40)
    )
    verdict = kind_of(
        html(f"<article><h1>Browse collections</h1><ul>{items}</ul></article>"),
        "https://example.test/x",
    )

    assert (verdict.kind, verdict.rule) == ("listing", "link_share")


def test_a_listing_url_needs_only_a_few_dense_links() -> None:
    """A near-empty tag page: too few links for the share rule to mean anything."""
    items = "".join(f'<p><a href="/post/{i}">Post {i}</a> {PROSE[:60]}</p>' for i in range(4))
    verdict = kind_of(html(items + f"<p>{PROSE}</p>"), "https://example.test/tag/walking")

    assert (verdict.kind, verdict.rule) == ("listing", "listing_url")


def test_a_listing_url_alone_is_not_enough() -> None:
    """The URL is a hint, not a verdict: an article filed under /archive/ is an article."""
    verdict = kind_of(
        html(f"<article><p>{PROSE * 8}</p></article>"),
        "https://example.test/archive/2026/05/a-study",
    )

    assert verdict.kind != "listing"


# --------------------------------------------------------------------------
# Rejections: link-heavy documents that are not listings
# --------------------------------------------------------------------------


def a_paper(*, head: str = "", references: int = 60, sections: int = 8) -> bytes:
    """A paper's body, then a long reference list where every entry is a link row."""
    body = "".join(f"<h2>Section {i}</h2><p>{PROSE * 3}</p>" for i in range(1, sections + 1))
    refs = "".join(
        f"<li>Author {i}, A. (2020). An earlier study number {i}. Journal of Things, 12, {i}. "
        f'<a href="https://doi.org/10.1234/ref.{i}">https://doi.org/10.1234/ref.{i}</a></li>'
        f'<li><a href="https://scholar.example/{i}">Google Scholar</a></li>'
        for i in range(references)
    )
    return html(
        f"<article><h1>Findings</h1>{body}<h2>References</h2><ol>{refs}</ol></article>",
        title="Findings",
        head=head,
    )


def test_a_paper_with_a_long_reference_list_is_not_a_listing() -> None:
    """Without any metadata to say it is a paper, the shape alone must not call it a list.

    Its reference rows are many and regular, but all at the end: they do not
    recur through the page the way a feed's records do.
    """
    verdict = kind_of(a_paper(), "https://example.test/work/findings")

    assert verdict.kind != "listing", verdict
    assert verdict.shape.record_spread < 8


def test_an_abstract_page_that_is_mostly_references_is_not_a_listing() -> None:
    """A publisher's abstract page: a paragraph, then references filling the page.

    The rows now recur from top to bottom and evenly — what spread and
    regularity alone would call a feed. What they lack is a summary between
    them: a citation line apart, not an abstract apart.
    """
    verdict = kind_of(a_paper(sections=1, references=150), "https://example.test/work/abstract")

    assert verdict.kind != "listing", verdict
    assert verdict.shape.record_groups >= RECORD_GROUPS, "the fixture has too few rows to test"
    assert verdict.shape.gap_cv <= RECORD_GAP_CV, "the rows are not even; it tests nothing"


def test_a_paper_that_says_so_is_a_paper_before_any_link_is_counted() -> None:
    head = (
        '<meta name="citation_title" content="Findings">'
        '<meta name="citation_journal_title" content="Journal of Things">'
    )
    verdict = kind_of(a_paper(head=head, references=200), "https://example.test/work/findings")

    assert (verdict.kind, verdict.rule) == ("paper", "citation_meta")


def test_a_papers_own_doi_makes_it_a_paper() -> None:
    head = '<meta name="citation_doi" content="10.5555/findings.2026">'
    verdict = kind_of(a_paper(head=head), "https://example.test/work/findings")

    assert (verdict.kind, verdict.rule) == ("paper", "own_doi")


def test_an_article_dense_with_inline_links_is_not_a_listing() -> None:
    """An encyclopedia-style entry that links a term in every sentence.

    Many links per thousand characters, but inside prose: the lines are not
    link rows and most of what a reader reads is not link text.
    """
    sentences = "".join(
        f'The <a href="/glossary/term{i}">defined term {i}</a> applies when a '
        f'<a href="/glossary/party{i}">party</a> relies on an earlier '
        f'<a href="/glossary/rule{i}">rule</a>, and courts have read it narrowly since. '
        for i in range(30)
    )
    verdict = kind_of(
        html(f"<article><h1>An entry</h1><p>{sentences}</p></article>"),
        "https://example.test/glossary/entry",
    )

    assert verdict.kind != "listing", verdict
    assert verdict.shape.links_per_1k > 10, "the fixture is not link-dense; it tests nothing"


# --------------------------------------------------------------------------
# A statute's index is a listing; its text is legal
# --------------------------------------------------------------------------


def test_a_statute_index_is_a_listing() -> None:
    sections = "".join(
        f'<li><a href="/code/title-5/section-{100 + i}">§ {100 + i}. Provision on matter {i}</a>'
        " (amended)</li>"
        for i in range(30)
    )
    verdict = kind_of(
        html(
            f"<article><h1>Title 5 — Contents</h1><ul>{sections}</ul></article>",
            title="Title 5 — Contents",
        ),
        "https://example.test/statutes/title-5",
    )

    assert verdict.kind == "listing"


def test_a_statute_text_is_legal_not_a_listing() -> None:
    """Every defined term is a link, which is exactly what made a raw-markdown measure
    call these indexes: the URLs are long and the anchor text is one word."""
    long = "?width=840&height=800&iframe=true&def_id=" + "x" * 120
    clauses = "".join(
        f"<p>({chr(97 + i)}) Except as provided in this section, a person may not "
        f'<a href="/definitions/term{i}{long}">manufacture</a> for sale any '
        f'<a href="/definitions/vehicle{i}{long}">motor vehicle</a> that does not meet '
        f"the applicable standard in force on the date of manufacture.</p>"
        for i in range(12)
    )
    verdict = kind_of(
        html(
            f"<h1>§ 30112. Prohibitions</h1>{clauses}",
            title="§ 30112 - Prohibitions on manufacturing",
        ),
        "https://example.test/code/title-49/30112",
    )

    assert (verdict.kind, verdict.rule) == ("legal", "section_title")


# --------------------------------------------------------------------------
# The other kinds
# --------------------------------------------------------------------------


def evidence(**overrides) -> Evidence:
    base = Evidence(url="https://example.test/a/b", media_type="text/html", text=PROSE * 4)
    return dataclasses.replace(base, **overrides)


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"tier": "press", "og_type": "article"}, ("news", "press_article")),
        ({"tier": "press", "publication_date": dt.date(2026, 1, 2)}, ("news", "press_article")),
        (
            {
                "url": "https://example.test/news/a-story",
                "og_type": "article",
                "publication_date": dt.date(2026, 1, 2),
            },
            ("news", "news_path_article"),
        ),
        (
            {"media_type": "application/pdf", "tier": "government"},
            ("report", "institutional_document"),
        ),
        ({"url": "https://example.test/"}, ("profile", "home_page")),
        ({"url": "https://example.test/about-us/leadership"}, ("profile", "profile_path")),
        ({"url": "https://example.test/legislation/act-2020"}, ("legal", "legal_path")),
        ({}, ("other", "no_rule")),
    ],
)
def test_each_rule_names_itself(overrides, expected) -> None:
    verdict = classify(evidence(**overrides))

    assert (verdict.kind, verdict.rule) == expected
    assert verdict.kind in DOC_KIND.enums


def test_an_undated_article_off_a_news_path_is_not_news() -> None:
    """`og:type article` is what most CMSs emit for every page; alone it proves nothing."""
    assert classify(evidence(og_type="article")).kind != "news"


def test_a_press_page_that_is_an_index_is_still_a_listing() -> None:
    items = "\n".join(
        f"- [Story headline number {i}](https://example.test/s/{i})" for i in range(30)
    )
    verdict = classify(evidence(tier="press", og_type="article", text=items))

    assert verdict.kind == "listing"


def test_a_pdf_with_an_abstract_is_a_paper_and_one_without_is_a_report() -> None:
    front = "A study of walking\n\nAbstract\nWe measured.\n\n" + PROSE * 3
    paper = classify(evidence(media_type="application/pdf", tier="institutional", text=front))
    report = classify(evidence(media_type="application/pdf", tier="institutional", text=PROSE * 5))

    assert (paper.kind, paper.rule) == ("paper", "pdf_abstract")
    assert report.kind == "report"


def test_an_abstract_deep_in_a_pdf_is_not_front_matter() -> None:
    late = PROSE * 40 + "\nAbstract\n" + PROSE
    assert (
        classify(evidence(media_type="application/pdf", tier="informal", text=late)).kind != "paper"
    )


def test_a_scan_with_no_text_is_still_classified_by_what_is_known() -> None:
    """A document that did not extract has a URL, a media type and a publisher."""
    verdict = classify(
        evidence_from(
            "https://example.test/files/plan.pdf",
            None,
            media_type="application/pdf",
            tier="government",
        )
    )

    assert (verdict.kind, verdict.rule) == ("report", "institutional_document")


def test_a_paginated_document_is_read_through_its_pages() -> None:
    document = ExtractedDocument(
        pages=(Page(1, "Title page"), Page(2, "Abstract\nWe measured things.")),
        extractor="pdftotext",
    )
    verdict = classify(
        evidence_from(
            "https://example.test/p.pdf", document, media_type="application/pdf", tier=None
        )
    )

    assert verdict.kind == "paper"


def test_every_rule_yields_a_kind_the_database_accepts() -> None:
    """Both directions, read from the module rather than listed here.

    A kind used in code and absent from the CHECK fails at the insert, after
    the fetch has reported success (the handover's `P1-28` trap); a kind in the
    CHECK that no rule produces is a value set that has drifted from the code.
    """
    import inspect
    import re

    import worker.extract.dockind as module

    used = set(re.findall(r'Verdict\("([a-z_]+)"', inspect.getsource(module)))
    assert used, "no Verdict literals found; the scan is broken"
    assert used == set(DOC_KIND.enums)


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def test_link_share_counts_anchor_text_not_urls() -> None:
    long_url = "https://example.test/" + "segment/" * 30
    text = f"A sentence of ordinary prose with one [word]({long_url}) linked in the middle of it."

    shape = link_shape(text)

    assert shape.links == 1
    assert shape.link_share < 0.1


def test_escaped_brackets_in_anchor_text_are_read() -> None:
    shape = link_shape("- [\\[1\\]](https://example.test/1)\n- [\\[2\\]](https://example.test/2)")

    assert shape.links == 2
    assert shape.link_rows == 2
    assert shape.link_share == 1.0


def test_empty_text_measures_as_nothing() -> None:
    shape = link_shape("")
    assert shape.visible_chars == 0 and shape.links == 0 and shape.gap_cv is None
    assert listing_rule("https://example.test/list/x", shape) is None


def test_irregular_link_rows_are_not_records() -> None:
    """Rows scattered at uneven intervals — a page's section links — are not a feed."""
    parts = []
    for i in range(RECORD_GROUPS + 2):
        parts.append(f"[Section {i}](https://example.test/{i})")
        parts.append(PROSE * (1 if i % 2 else 12))
    shape = link_shape("\n".join(parts))

    assert shape.gap_cv is not None and shape.gap_cv > RECORD_GAP_CV
    assert listing_rule("https://example.test/page", shape) is None


# --------------------------------------------------------------------------
# Head metadata
# --------------------------------------------------------------------------


def test_og_type_is_read_from_the_head_only() -> None:
    page = (
        '<html><head><meta property="og:type" content="Article"></head>'
        '<body><meta property="og:type" content="website"></body></html>'
    )
    assert og_type(page) == "article"
    assert og_type("<html><head></head><body></body></html>") is None


def test_scholarly_meta_needs_a_value_and_the_head() -> None:
    assert has_scholarly_meta('<head><meta name="citation_title" content="X"></head>')
    assert not has_scholarly_meta('<head><meta name="citation_title" content=""></head>')
    assert not has_scholarly_meta('<head></head><body><meta name="citation_title" content="X">')
    assert not has_scholarly_meta('<head><meta name="description" content="X"></head>')


def test_the_extractor_carries_both_signals() -> None:
    document = extract_html(
        html(
            f"<p>{PROSE * 3}</p>",
            head='<meta property="og:type" content="article">'
            '<meta name="citation_pdf_url" content="/x.pdf">',
        ),
        "https://example.test/a",
    )
    assert document.og_type == "article"
    assert document.scholarly_meta is True


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def test_recording_a_verdict_keeps_the_rest_of_extra() -> None:
    from meridian_core.models import Source

    source = Source(url="https://example.test/a", extra={"media_type": "text/html"})
    verdict = classify(
        evidence(text="\n".join(f"- [Item {i}](https://x.test/{i})" for i in range(20)))
    )

    record_doc_kind(source, verdict)

    assert source.doc_kind == "listing"
    assert source.extra["media_type"] == "text/html"
    assert source.extra["doc_kind"]["rule"] == verdict.rule
    assert source.extra["doc_kind"]["links"]["links"] == 20
