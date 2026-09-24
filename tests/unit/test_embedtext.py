"""The embedding view (task B-49): what a chunk is embedded as."""

from __future__ import annotations

import pytest

from meridian_core.embedtext import embedding_view, link_text_share, view_differs


@pytest.mark.parametrize(
    ("text", "view"),
    [
        ("[Federal Rules](https://example.org/rules/frcp) apply.", "Federal Rules apply."),
        ("See ![Figure 2: ridership](fig2.png) above.", "See Figure 2: ridership above."),
        ("Source: https://doi.org/10.1000/xyz, retrieved.", "Source: , retrieved."),
        ("<https://example.org/a> and text", "and text"),
        ("[[pdf](https://a/1) ,[html](https://a/2) ]", "[pdf ,html ]"),
        ("[5](https://x/5) U. S. C. §§[702](https://x/702)", "5 U. S. C. §§702"),
    ],
)
def test_links_and_urls_become_what_a_reader_sees(text: str, view: str) -> None:
    assert embedding_view(text) == view


@pytest.mark.parametrize(
    "text",
    [
        "Plain prose with  two spaces\nand a line break.",
        "Brackets [1] and (parentheses) that are not links.",
        "An escaped \\[not a link\\] stays byte-identical.",
        "   leading and trailing whitespace   ",
    ],
)
def test_text_without_links_is_embedded_exactly_as_before(text: str) -> None:
    """The property the re-embed pass depends on: only chunks with links change."""
    assert embedding_view(text) == text
    assert not view_differs(text)


def test_a_chunk_that_is_only_a_url_still_embeds_as_itself() -> None:
    # An empty view embeds the same as every other empty view.
    assert embedding_view("https://example.org/only") == "https://example.org/only"
    assert embedding_view("[](https://example.org/x)") == "[](https://example.org/x)"


def test_link_text_is_kept_word_for_word() -> None:
    text = "Read [the 2019 household travel survey](https://x.test/survey.pdf) first."
    assert "the 2019 household travel survey" in embedding_view(text)
    assert "x.test" not in embedding_view(text)


# --------------------------------------------------------------------------
# How much of a chunk is link text (`P2-24` reads it to spot listings)
# --------------------------------------------------------------------------


def test_prose_without_links_has_no_link_text() -> None:
    assert link_text_share("A paragraph that argues something at length.") == 0.0


def test_a_listing_is_almost_all_link_text() -> None:
    listing = " - ".join(
        f"[Chapter {i} - Some Heading](https://example.test/c/{i})" for i in range(20)
    )
    assert link_text_share(listing) > 0.85


def test_link_targets_never_count() -> None:
    """A sentence with one long URL is still a sentence, not a listing."""
    text = (
        "The study found a clear effect across all sites, as reported in "
        "[the appendix](https://example.test/" + "x" * 400 + ")."
    )
    assert link_text_share(text) < 0.2


def test_a_bare_url_is_not_link_text() -> None:
    assert link_text_share("See https://example.test/a/very/long/path for details.") == 0.0


def test_image_alt_text_counts_as_link_text() -> None:
    assert link_text_share("![Figure](f.png)") == 1.0


def test_a_reference_list_of_url_labelled_links_is_a_listing() -> None:
    refs = " - ".join(
        f"Author {i} (2019) A title. [https://doi.org/10.1/{i}](https://doi.org/10.1/{i})"
        for i in range(6)
    )
    assert link_text_share(refs) > 0.5


def test_the_share_never_exceeds_one() -> None:
    assert 0.0 < link_text_share("[a  b   c](https://x/1)") <= 1.0
