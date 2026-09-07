"""HTML extraction (task P1-07, spec §6.6, §5.2).

The failures worth defending against here are quiet ones. Boilerplate admitted
as content becomes entities and then edges, and a graph full of "Skip to main
content" costs far more to unpick than the paragraph precision lost. A guessed
publication date is worse than a missing one in a corpus whose whole job is
being checkable. And a link list that includes every favicon on the page turns
frontier expansion into a budget spent fetching PNGs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from worker.extract.base import TEXT_FLOOR, Citation, ExtractedDocument
from worker.extract.html import MAX_LINKS, _own_doi, extract_html

BODY = "This is a real paragraph of prose about transit planning in dense cities. " * 6
URL = "https://example.test/report"


def page(body: str = BODY, *, head: str = "", lang: str = "en", extra: str = "") -> str:
    return f"""<!doctype html>
<html lang="{lang}"><head><title>Annual Report 2026</title>{head}</head>
<body>
  <nav><a href="/about">About</a><a href="/contact">Contact us</a></nav>
  <header>Skip to main content</header>
  <article><h1>Annual Report 2026</h1><p>{body}</p>{extra}</article>
  <footer>© 2026 Example Authority. All rights reserved. Privacy policy.</footer>
</body></html>"""


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------


def test_the_main_content_comes_out() -> None:
    document = extract_html(page(), URL)

    assert "transit planning in dense cities" in document.text
    assert document.has_text


def test_the_page_chrome_does_not() -> None:
    """The expensive error. Boilerplate becomes entities and entities become edges."""
    document = extract_html(page(), URL)

    for boilerplate in ("Skip to main content", "Privacy policy", "All rights reserved"):
        assert boilerplate not in document.text, f"{boilerplate!r} was admitted as content"


def test_bytes_and_str_give_the_same_answer() -> None:
    """The fetcher hands over bytes; a test or a re-extraction may hand over text."""
    assert extract_html(page().encode(), URL).text == extract_html(page(), URL).text


def test_a_page_that_lies_about_its_encoding_still_extracts() -> None:
    """Encoding detection is trafilatura's job precisely because pages lie.

    Bytes are passed through undecoded for this reason — a page declaring UTF-8
    and serving Latin-1 is common enough that decoding it here first would turn
    a recoverable document into replacement characters.
    """
    html = page(f"Café résumé naïve. {BODY}").encode("latin-1")

    document = extract_html(html, URL)

    assert "Caf" in document.text
    assert "�" not in document.text[:200], "the bytes were decoded before detection"


# --------------------------------------------------------------------------
# Empty is a valid answer (§6.5)
# --------------------------------------------------------------------------


def test_a_page_with_no_content_is_not_an_error() -> None:
    """§6.5: metadata-only is a resting state, not a failure."""
    document = extract_html("<html><body><nav>menu</nav></body></html>", URL)

    assert not document.has_text
    assert document.text == "" or len(document.text) < TEXT_FLOOR


def test_a_page_of_only_boilerplate_does_not_count_as_having_text() -> None:
    """`has_text` is a threshold, not `text != ""`.

    A cookie banner has text in the strict sense and nothing a graph can be built
    from, and calling that `text_available` would make §6.5's metadata-only state
    indistinguishable from a successful extraction.
    """
    assert not ExtractedDocument(text="We use cookies. Accept.").has_text
    assert ExtractedDocument(text="x" * TEXT_FLOOR).has_text


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not html at all",
        "<html>",
        "<html><body><p>" + "\x00" * 50 + "</p></body></html>",
        "<<<>>>",
    ],
)
def test_malformed_input_returns_a_document_rather_than_raising(content: str) -> None:
    """A worker that runs for weeks must not die on one bad page."""
    document = extract_html(content, URL)

    assert isinstance(document, ExtractedDocument)
    assert not document.has_text


def test_an_extractor_that_raises_is_caught_and_named(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("lxml said no")

    monkeypatch.setattr("worker.extract.html.trafilatura.extract", explode)

    document = extract_html(page(), URL)

    assert document.extractor == "failed"
    assert not document.has_text


# --------------------------------------------------------------------------
# Metadata — never guessed
# --------------------------------------------------------------------------


def test_the_title_is_read_from_the_page() -> None:
    assert extract_html(page(), URL).title == "Annual Report 2026"


def test_a_publication_date_is_taken_only_when_the_page_gives_a_full_one() -> None:
    head = '<meta property="article:published_time" content="2026-03-14T09:00:00Z">'

    assert extract_html(page(head=head), URL).publication_date == dt.date(2026, 3, 14)


def test_a_partial_date_is_discarded_rather_than_completed() -> None:
    """`sources.publication_date` is a DATE, and citations are built from it.

    Filling in a missing day would put a fabricated date in the column whose
    entire purpose is being checkable.
    """
    from worker.extract.html import _as_date

    assert _as_date("2026") is None
    assert _as_date("2026-03") is None
    assert _as_date("not a date") is None
    assert _as_date(None) is None
    assert _as_date("2026-03-14") == dt.date(2026, 3, 14)


def test_the_language_falls_back_to_the_html_lang_attribute() -> None:
    """A page naming its own language beats a guess from one paragraph of it."""
    assert extract_html(page(lang="fr"), URL).language == "fr"


def test_a_regional_variant_is_trimmed_to_the_primary_subtag() -> None:
    """`en` and `en-GB` splitting a language filter is precision nobody uses."""
    assert extract_html(page(lang="en-GB"), URL).language == "en"


def test_a_page_with_no_language_says_so_rather_than_defaulting() -> None:
    html = page().replace('<html lang="en">', "<html>")

    assert extract_html(html, URL).language in (None, "en"), (
        "language must come from the page or a real detector, never a hardcoded default"
    )


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------


def test_links_are_absolute() -> None:
    document = extract_html(page(extra='<a href="/deeper/page">more</a>'), URL)

    assert "https://example.test/deeper/page" in document.links


@pytest.mark.parametrize(
    "href",
    ["mailto:a@b.test", "javascript:void(0)", "tel:+6512345678", "data:text/html,x", "#section"],
)
def test_links_that_are_not_documents_are_dropped(href: str) -> None:
    """Each of these becomes a queue row that can only ever fail."""
    document = extract_html(page(extra=f'<a href="{href}">x</a>'), URL)

    assert href not in document.links
    assert all(link.startswith(("http://", "https://")) for link in document.links)


def test_assets_are_not_links() -> None:
    """`iterlinks()` yields favicons, stylesheets and scripts too.

    On a real government home page the assets outnumber the documents several
    times over, and a frontier fed from that list spends its budget fetching
    180-byte PNGs.
    """
    head = (
        '<link rel="icon" href="/favicon.ico">'
        '<link rel="stylesheet" href="/site.css">'
        '<script src="/app.js"></script>'
    )
    document = extract_html(page(head=head, extra='<img src="/photo.jpg">'), URL)

    for asset in ("favicon.ico", "site.css", "app.js", "photo.jpg"):
        assert not any(asset in link for link in document.links), f"{asset} was treated as a link"


def test_a_fragment_is_not_a_different_document() -> None:
    """Otherwise one page is enqueued once per heading it links to."""
    extra = '<a href="/page#one">1</a><a href="/page#two">2</a><a href="/page">3</a>'
    document = extract_html(page(extra=extra), URL)

    assert document.links.count("https://example.test/page") == 1


def test_links_keep_their_order_through_deduplication() -> None:
    """A page's first links matter most; truncation must not drop them.

    Relative order, not absolute position — the nav links are earlier in the
    document than anything inside the article, and correctly come first.
    """
    extra = "".join(f'<a href="/p{i}">{i}</a>' for i in range(5)) + '<a href="/p0">again</a>'
    document = extract_html(page(extra=extra), URL)

    ours = [link for link in document.links if "/p" in link]
    assert ours == [f"https://example.test/p{i}" for i in range(5)]


def test_a_link_farm_is_capped() -> None:
    """40,000 anchors should not become 40,000 strings carried through the loop."""
    extra = "".join(f'<a href="/p{i}">{i}</a>' for i in range(MAX_LINKS + 200))
    document = extract_html(page(extra=extra), URL)

    assert len(document.links) == MAX_LINKS


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------


def cites(document: ExtractedDocument) -> set[str]:
    return {f"{c.kind}:{c.value}" for c in document.citations}


def test_a_doi_in_the_text_is_found() -> None:
    document = extract_html(page(f"{BODY} See doi:10.5555/abc-123 for the method."), URL)

    assert "doi:10.5555/abc-123" in cites(document)


def test_a_doi_at_the_end_of_a_sentence_does_not_keep_the_full_stop() -> None:
    """A resolver handed `10.5555/abc.` reports "not found", which reads as
    missing rather than as malformed — the worst kind of wrong answer."""
    document = extract_html(page(f"{BODY} Reported in 10.5555/abc-123."), URL)

    assert "doi:10.5555/abc-123" in cites(document)


def test_a_doi_with_balanced_parentheses_survives() -> None:
    """Wiley's `10.1002/(SICI)...` shape is a real and common DOI."""
    document = extract_html(page(f"{BODY} DOI: 10.1002/(SICI)1097-0258 here."), URL)

    assert "doi:10.1002/(sici)1097-0258" in cites(document)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("arXiv:2401.12345", "arxiv:2401.12345"),
        ("arXiv: 2401.12345v3", "arxiv:2401.12345v3"),
        ("arXiv:math.GT/0309136", "arxiv:math.GT/0309136"),
        ("PMID: 31234567", "pmid:31234567"),
        ("hdl.handle.net/10635/12345", "handle:10635/12345"),
    ],
)
def test_the_other_identifier_schemes(text: str, expected: str) -> None:
    document = extract_html(page(f"{BODY} {text} end."), URL)

    assert expected in cites(document)


def test_an_identifier_that_is_only_in_a_link_is_still_found() -> None:
    """A "view on arXiv" button carries the id in the href and nowhere else.

    Reading only the text misses a predictable population of papers rather than
    a random sample of them, which is the kind of gap that never shows up as an
    error.
    """
    document = extract_html(page(extra='<a href="https://arxiv.org/abs/2305.11111">pdf</a>'), URL)

    assert "arxiv:2305.11111" in cites(document)


def test_the_pages_own_doi_is_not_filed_as_something_it_cites() -> None:
    """`sources.doi` and the reference list answer different questions.

    One makes this source resolvable; the other is a pointer to something else
    to go and fetch (`P1-14`). Conflating them would have the resolver chase the
    document it already has.
    """
    head = '<meta name="citation_doi" content="10.1234/own.2026.1">'
    document = extract_html(page(head=head, body=f"{BODY} cites 10.5555/other."), URL)

    assert document.doi == "10.1234/own.2026.1"
    assert "doi:10.1234/own.2026.1" not in cites(document)
    assert "doi:10.5555/other" in cites(document)


@pytest.mark.parametrize(
    "tag",
    [
        '<meta name="citation_doi" content="10.1234/x">',
        '<meta name="DC.identifier" content="doi:10.1234/x">',
        '<meta property="prism.doi" content="10.1234/x">',
    ],
)
def test_publishers_declare_their_doi_in_several_places(tag: str) -> None:
    assert _own_doi(f"<html><head>{tag}</head><body></body></html>") == "10.1234/x"


def test_no_doi_meta_tag_means_none_rather_than_a_body_doi() -> None:
    """A DOI in a reference list is describing a reference, not the page."""
    assert _own_doi(page(f"{BODY} see 10.5555/other")) is None


def test_citations_are_deduplicated() -> None:
    document = extract_html(page(f"{BODY} 10.5555/abc and again 10.5555/abc."), URL)

    assert len([c for c in document.citations if c.value == "10.5555/abc"]) == 1


def test_a_page_with_no_citations_has_none() -> None:
    """Rather than an empty-string entry, which every consumer would have to guard."""
    assert extract_html(page(), URL).citations == ()


# --------------------------------------------------------------------------
# The browser path
# --------------------------------------------------------------------------


def browser(markdown, *, links=None, metadata=None) -> dict:
    payload = {"markdown": markdown, "links": links or {"internal": [], "external": []}}
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


def test_crawl4ais_pruned_markdown_is_preferred_over_re_extracting() -> None:
    """`PruningContentFilter` saw a rendered DOM this process never had.

    Re-extracting from the HTML that came back would throw that away, which is
    the whole reason §6.6 routes HTML to Crawl4AI in the first place.
    """
    payload = browser({"fit_markdown": "# Pruned\n" + BODY, "raw_markdown": "# Raw\nnav nav nav"})

    document = extract_html(page(), URL, browser_payload=payload)

    assert document.extractor == "crawl4ai"
    assert document.text.startswith("# Pruned")


def test_raw_markdown_is_the_fallback_when_the_filter_produced_nothing() -> None:
    payload = browser({"fit_markdown": "", "raw_markdown": "# Raw\n" + BODY})

    document = extract_html(page(), URL, browser_payload=payload)

    assert document.text.startswith("# Raw")


def test_markdown_as_a_plain_string_is_read_too() -> None:
    """Crawl4AI has shipped both shapes across versions."""
    document = extract_html(page(), URL, browser_payload=browser("# String\n" + BODY))

    assert document.text.startswith("# String")


def test_a_browser_payload_with_no_markdown_falls_through_to_local_extraction() -> None:
    """The browser ran and produced nothing usable. It still has rendered HTML,
    which is more than a static fetch would have had."""
    document = extract_html(page(), URL, browser_payload=browser({"fit_markdown": ""}))

    assert document.extractor == "trafilatura"
    assert "transit planning" in document.text


def test_links_crawl4ai_collected_are_used_and_tidied() -> None:
    payload = browser(
        "# Rendered\n" + BODY,
        links={
            "internal": [{"href": "/inner"}, {"href": "#frag"}],
            "external": [{"href": "https://other.test/x"}, {"href": "mailto:a@b.test"}],
        },
    )

    document = extract_html(page(), URL, browser_payload=payload)

    assert "https://example.test/inner" in document.links
    assert "https://other.test/x" in document.links
    assert not any(link.startswith("mailto:") for link in document.links)


def test_a_browser_page_with_no_links_still_gets_them_from_the_html() -> None:
    """Crawl4AI reporting an empty link set is not the same as the page having none."""
    payload = browser("# Rendered\n" + BODY, links={"internal": [], "external": []})

    document = extract_html(page(extra='<a href="/deeper">x</a>'), URL, browser_payload=payload)

    assert "https://example.test/deeper" in document.links


def test_citations_are_still_extracted_from_a_browser_document() -> None:
    payload = browser("# Rendered\n" + BODY + " See 10.5555/browser-doi.")

    document = extract_html(page(), URL, browser_payload=payload)

    assert "doi:10.5555/browser-doi" in cites(document)


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_the_document_is_immutable() -> None:
    """It crosses from extraction into the persistence step; nothing should edit it."""
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        extract_html(page(), URL).text = "rewritten"  # type: ignore[misc]


def test_a_citation_renders_as_its_identifier() -> None:
    assert str(Citation(kind="doi", value="10.1/x")) == "doi:10.1/x"


def test_whitespace_from_html_indentation_is_collapsed() -> None:
    """Trailing spaces before a newline are a markdown hard break.

    They arrive from HTML indentation far more often than from an author, and
    leaving them makes chunk boundaries (`P2-02`) depend on how the source was
    formatted rather than on its prose.
    """
    document = extract_html(page(), URL)

    assert "  \n" not in document.text
    assert not document.text.startswith(("\n", " "))


# --------------------------------------------------------------------------
# A document is not a citation of itself
# --------------------------------------------------------------------------


ARXIV_URL = "https://arxiv.org/abs/2401.02777"


def test_arxiv_gets_its_own_doi_from_the_url() -> None:
    """arXiv publishes no `citation_doi` tag at all.

    Its DataCite DOI appears only in the body, where it reads as a citation —
    so without this, `sources.doi` stays null for the largest population of
    papers in this corpus. Derived from the identifier already in the URL, which
    is arXiv's own mechanical mapping rather than a guess.
    """
    document = extract_html(page(), ARXIV_URL)

    assert document.doi == "10.48550/arxiv.2401.02777"


def test_a_version_suffix_does_not_change_the_works_doi() -> None:
    """`v2` identifies one revision; the DOI arXiv mints covers the work."""
    assert _own_doi("", "https://arxiv.org/abs/2401.02777v2") == "10.48550/arxiv.2401.02777"


def test_a_meta_tag_beats_the_url() -> None:
    """A publisher stating its DOI is better evidence than a pattern."""
    head = '<meta name="citation_doi" content="10.1234/published">'

    assert _own_doi(page(head=head), ARXIV_URL) == "10.1234/published"


def test_a_paper_does_not_cite_itself() -> None:
    """Its own id appears throughout its abstract page, in several versions.

    Left in, every source in the corpus cites itself and `P1-14` spends a
    resolution attempt per paper fetching the document it already has.
    """
    body = f"{BODY} arXiv:2401.02777v1 and arXiv:2401.02777v2 and 10.48550/arXiv.2401.02777"
    document = extract_html(page(body), ARXIV_URL)

    assert document.citations == ()


def test_a_paper_still_cites_other_papers() -> None:
    """The exclusion must be of this work, not of arXiv identifiers generally."""
    body = f"{BODY} builds on arXiv:2301.00001 and 10.5555/other"
    document = extract_html(page(body), ARXIV_URL)

    assert cites(document) == {"arxiv:2301.00001", "doi:10.5555/other"}


def test_a_non_arxiv_url_gets_no_doi_invented_for_it() -> None:
    """The rule is arXiv's mapping, not a general one."""
    assert _own_doi(page(), "https://example.test/paper") is None
