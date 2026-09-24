"""A page's declared English version (task B-57)."""

from __future__ import annotations

import pytest

from meridian_core.docdupes import Pair, canonical, translation_pairs
from worker.extract.html import english_alternate

BASE = "https://site.test/de/seite"


def head(*links: str) -> str:
    return f"<html lang='de'><head>{''.join(links)}</head><body><a href='/x' hreflang='en'>x</a></body></html>"


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (
            '<link rel="alternate" hreflang="en" href="https://site.test/en/page">',
            "https://site.test/en/page",
        ),
        ("<link hreflang='en-GB' href='/en/page' rel='alternate'>", "https://site.test/en/page"),
        ('<link rel="alternate" hreflang="EN-sg" href="/en/page" />', "https://site.test/en/page"),
    ],
)
def test_an_english_alternate_is_found_whatever_the_attribute_order_or_region(
    link, expected
) -> None:
    assert english_alternate(head(link), BASE) == expected


@pytest.mark.parametrize(
    "link",
    [
        '<link rel="alternate" hreflang="x-default" href="/en/page">',  # a fallback, not a language
        '<link rel="alternate" hreflang="fr" href="/fr/page">',
        '<link rel="canonical" hreflang="en" href="/en/page">',  # not an alternate
        '<link rel="alternate" hreflang="en" href="https://site.test/de/seite/">',  # itself
        '<link rel="alternate" hreflang="en" href="javascript:void(0)">',
    ],
)
def test_what_is_not_an_english_version_is_ignored(link) -> None:
    assert english_alternate(head(link), BASE) is None


def test_a_link_in_the_body_is_not_a_declaration() -> None:
    assert english_alternate(head(), BASE) is None


def test_a_translation_points_at_its_english_version_whatever_the_ids() -> None:
    pairs = translation_pairs(
        {9: "https://site.test/en/page"},
        {9: ("https://site.test/de/seite", None), 20: ("https://site.test/en/page/", None)},
    )
    assert pairs == [Pair(9, 20, "translation", 1.0)]


def test_a_translation_whose_english_version_is_not_in_the_corpus_is_kept_as_it_is() -> None:
    assert (
        translation_pairs(
            {9: "https://site.test/en/page"}, {9: ("https://site.test/de/seite", None)}
        )
        == []
    )


def test_an_exact_copy_outranks_a_translation_verdict() -> None:
    verdicts = canonical([Pair(9, 20, "translation", 1.0), Pair(9, 3, "exact", 1.0)])
    assert verdicts[9].reason == "exact"
