"""The embedding view (task B-49): what a chunk is embedded as."""

from __future__ import annotations

import pytest

from meridian_core.embedtext import embedding_view, view_differs


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
