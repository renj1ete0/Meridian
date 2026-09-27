"""Which words of a question are looked up as node names (task B-94)."""

from __future__ import annotations

import pytest

from meridian_core.neighbourhood import QUESTION_WORDS, query_words


def test_a_question_keeps_its_subjects_in_order_once_each() -> None:
    assert query_words("How does walkability affect health, and health costs?") == [
        "walkability",
        "health",
        "costs",
    ]


def test_hyphenated_terms_stay_whole() -> None:
    assert query_words("on-demand bus ridership") == ["on-demand", "bus", "ridership"]


@pytest.mark.parametrize("word", sorted(QUESTION_WORDS))
def test_no_question_word_is_ever_looked_up(word: str) -> None:
    """Every entry, not a sample: one that slipped through would offer every
    node whose name happens to contain it."""
    assert query_words(f"{word} {word.upper()}") == []


def test_a_short_subject_is_kept_and_shorter_words_are_not() -> None:
    assert query_words("is it ok? -- a bus, a car, AI") == ["bus", "car"]
