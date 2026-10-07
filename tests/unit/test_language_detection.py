"""Reading a page's language from its text when it declares none (task B-153)."""

from __future__ import annotations

import pytest

from worker.extract.language import MIN_CHARS, detect_language

ENGLISH = (
    "Public transport ridership rose sharply after the new bus lanes opened in the city "
    "centre, and the operator reported fewer delays on the routes that gained a lane. "
) * 3
FRENCH = (
    "La fréquentation des transports publics a fortement augmenté après l'ouverture des "
    "nouvelles voies de bus dans le centre-ville, et les retards ont diminué. "
) * 3


@pytest.mark.parametrize(("text", "language"), [(ENGLISH, "en"), (FRENCH, "fr")])
def test_a_clear_page_gets_its_language(text: str, language: str) -> None:
    assert detect_language(text) == language


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        ENGLISH[: MIN_CHARS // 2],  # a heading's worth is not enough to judge
        "Table 3  1.2  4.5  6.7  n=34  p<0.05 " * 20,  # numbers are not a language
    ],
)
def test_too_little_or_no_language_is_unknown_not_a_guess(text: str | None) -> None:
    assert detect_language(text) is None


def test_only_the_opening_is_read() -> None:
    """A French page with an English appendix far down is French."""
    assert detect_language(FRENCH * 30 + ENGLISH * 30) == "fr"
