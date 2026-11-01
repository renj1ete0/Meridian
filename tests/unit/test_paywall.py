"""Which pages are paywalled stubs, and what a stub keeps (task `B-256`).

Pages here are made up; their shapes are the ones measured on real outlets (see
docs/features/extraction.md#paywalled-articles): a JSON-LD article declaring itself not
free, sometimes only through a `hasPart`, with `false` as a boolean or as `"False"`.
"""

from __future__ import annotations

import json

import pytest

from worker.extract.base import ExtractedDocument
from worker.extract.html import extract_html
from worker.extract.paywall import STUB_CEILING, VISIBLE, paywall_stub, walled_item

HEADLINE = "Ferry fares to rise in the new year"
TEASER = (
    "Commuters on the island routes will pay about a tenth more from January, the operator "
    "said on Friday."
)


def page(*ld_items: object, head: str = "", body: str = "") -> str:
    blocks = "".join(
        f'<script type="application/ld+json">{json.dumps(item)}</script>' for item in ld_items
    )
    return f"<html><head><title>{HEADLINE}</title>{head}{blocks}</head><body>{body}</body></html>"


def article(**fields: object) -> dict[str, object]:
    return {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": HEADLINE,
        "datePublished": "2026-10-30T08:15:00+08:00",
        **fields,
    }


def doc(text: str = f"{HEADLINE}\n{TEASER}", **fields: object) -> ExtractedDocument:
    return ExtractedDocument(text=text, title=HEADLINE, extractor="trafilatura", **fields)


# --------------------------------------------------------------------------
# What counts as walled
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [False, "false", "False", " FALSE "])
def test_an_article_declared_not_free_is_a_stub(value) -> None:
    stub = paywall_stub(page(article(isAccessibleForFree=value)), doc())
    assert stub is not None and stub.rule == "isAccessibleForFree"


def test_a_walled_part_alone_makes_a_stub() -> None:
    """Free at the top, with the body named as a walled part: the markup publishers write."""
    part = {"@type": "WebPageElement", "isAccessibleForFree": "False", "cssSelector": ".rest"}
    stub = paywall_stub(page(article(isAccessibleForFree=True, hasPart=[part])), doc())
    assert stub is not None and stub.rule == "hasPart"


def test_a_walled_item_inside_a_graph_counts() -> None:
    graph = {"@context": "https://schema.org", "@graph": [article(isAccessibleForFree=False)]}
    assert walled_item(page(graph)) is not None


@pytest.mark.parametrize("value", [True, "true", "True", None])
def test_a_free_article_with_a_short_body_is_not_a_stub(value) -> None:
    """Short is not walled: a brief is a whole article."""
    fields = {} if value is None else {"isAccessibleForFree": value}
    assert paywall_stub(page(article(**fields)), doc()) is None


def test_paywall_words_in_the_markup_are_not_evidence() -> None:
    """Measured: an outlet's free pages carry the same wrapper classes as its walled ones."""
    html = page(
        article(),
        head='<script src="https://cdn.example.net/tinypass.js"></script>',
        body='<div class="paywall premium subscriber-only">Subscribe</div>',
    )
    assert walled_item(html) is None
    assert paywall_stub(html, doc()) is None


def test_a_walled_article_listed_inside_the_page_does_not_wall_the_page() -> None:
    """Only what the page declares itself to be: a related article nested in it is not."""
    listing = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [article(isAccessibleForFree=False)],
        },
    }
    assert walled_item(page(listing)) is None


def test_the_whole_article_served_behind_the_markup_is_not_a_stub() -> None:
    """A metered site that sends every reader the full text has not walled it."""
    long_text = HEADLINE + "\n" + ("A sentence of the article. " * 200)
    assert len(long_text) >= STUB_CEILING
    assert paywall_stub(page(article(isAccessibleForFree=False)), doc(long_text)) is None
    just_under = long_text[: STUB_CEILING - 1]
    assert paywall_stub(page(article(isAccessibleForFree=False)), doc(just_under)) is not None


@pytest.mark.parametrize("field", [{"doi": "10.1234/abcd"}, {"scholarly_meta": True}])
def test_a_scholarly_page_is_never_a_stub(field) -> None:
    """An abstract is a paper's own summary, and resolution looks for the open copy."""
    assert paywall_stub(page(article(isAccessibleForFree=False)), doc(**field)) is None


def test_invalid_json_ld_is_skipped_not_repaired() -> None:
    html = (
        f"<html><head><title>{HEADLINE}</title>"
        '<script type="application/ld+json">{"isAccessibleForFree": false,</script>'
        "</head></html>"
    )
    assert paywall_stub(html, doc()) is None


def test_no_document_is_no_stub() -> None:
    assert paywall_stub(page(article(isAccessibleForFree=False)), None) is None


# --------------------------------------------------------------------------
# What a stub keeps
# --------------------------------------------------------------------------


def test_the_passage_is_headline_time_and_teaser_without_repeating_the_headline() -> None:
    stub = paywall_stub(page(article(isAccessibleForFree=False)), doc())
    assert stub is not None
    assert stub.text == f"{HEADLINE}\nPublished 2026-10-30T08:15:00+08:00\n\n{TEASER}"
    assert stub.mark() == {
        "reason": "paywall",
        "rule": "isAccessibleForFree",
        "from": "page",
        "visible": list(VISIBLE),
        "published": "2026-10-30T08:15:00+08:00",
        "chars_seen": len(f"{HEADLINE}\n{TEASER}"),
    }


def test_with_no_visible_text_the_page_description_is_the_teaser() -> None:
    """Some walled pages render nothing; what the page says of itself is still public."""
    html = page(
        article(isAccessibleForFree=False),
        head='<meta property="og:description" content="Fares rise &amp; routes change.">',
    )
    stub = paywall_stub(html, doc(text=""))
    assert stub is not None and stub.teaser == "Fares rise & routes change."


def test_the_json_ld_description_comes_before_the_meta_tags() -> None:
    html = page(
        article(isAccessibleForFree=False, description="From the markup, a full sentence."),
        head='<meta property="og:description" content="From the meta tag.">',
    )
    stub = paywall_stub(html, doc(text=""))
    assert stub is not None and stub.teaser == "From the markup, a full sentence."


def test_a_time_that_is_not_iso_is_left_out_not_guessed() -> None:
    stub = paywall_stub(page(article(isAccessibleForFree=False, datePublished="Friday")), doc())
    assert stub is not None and stub.published is None
    assert "Published" not in stub.text
    assert stub.mark()["visible"] == ["headline", "teaser"]


def test_a_page_showing_almost_nothing_is_not_kept_as_a_stub() -> None:
    """A headline of a few words and no teaser is not worth a passage."""
    html = page({"@type": "NewsArticle", "headline": "Fares", "isAccessibleForFree": False})
    bare = ExtractedDocument(text="", title="Fares")
    assert paywall_stub(html, bare) is None


def test_as_document_drops_pages_and_figures() -> None:
    stub = paywall_stub(page(article(isAccessibleForFree=False)), doc())
    assert stub is not None
    replaced = stub.as_document(doc())
    assert replaced.text == stub.text and replaced.pages == () and replaced.figures == ()


def test_through_the_real_extractor() -> None:
    """End to end over trafilatura, so the detector sees the text extraction really gives."""
    html = page(
        article(isAccessibleForFree=False),
        head='<meta property="og:type" content="article">',
        body=f"<article><h1>{HEADLINE}</h1><p>{TEASER}</p></article>",
    )
    stub = paywall_stub(html, extract_html(html, "https://news.example.org/fares"))
    assert stub is not None
    assert stub.text.startswith(f"{HEADLINE}\nPublished ")
    assert TEASER in stub.text
