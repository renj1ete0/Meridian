"""Topics per passage without a database (task P2-24).

The thresholds are exercised at their edges with hand-written scores, and the
fingerprint is checked for the two properties staleness depends on: it moves
whenever the source basis or a passage threshold moves, and never otherwise.
"""

from __future__ import annotations

import pytest

from meridian_core import passagetopics
from meridian_core.passagetopics import (
    LISTING_SHARE,
    PASSAGE_FLOOR,
    PASSAGE_MARGIN,
    decide_passage,
    is_listing,
    passage_fingerprint,
)
from meridian_core.topiclabels import LABEL_FLOOR, OFFTOPIC_FLOOR

PROSE = "A paragraph about the subject, written as sentences rather than links."


def test_a_passage_at_the_floor_is_labelled_and_just_under_is_not() -> None:
    assert decide_passage({"a": PASSAGE_FLOOR, "b": 0.1}, PROSE) == ["a"]
    assert decide_passage({"a": PASSAGE_FLOOR - 0.001, "b": 0.1}, PROSE) == []


def test_two_topics_inside_the_margin_are_both_labels_best_first() -> None:
    scores = {"b": 0.6 - PASSAGE_MARGIN + 0.001, "a": 0.6}
    assert decide_passage(scores, PROSE) == ["a", "b"]


def test_a_second_topic_outside_the_margin_is_rejected() -> None:
    scores = {"a": 0.7, "b": 0.7 - PASSAGE_MARGIN - 0.01}
    assert decide_passage(scores, PROSE) == ["a"]


def test_a_listing_is_rejected_however_high_it_scores() -> None:
    listing = " ".join(f"[Item {i}](https://example.test/{i})" for i in range(10))
    assert is_listing(listing)
    assert decide_passage({"a": 0.99}, listing) == []


def test_prose_with_a_few_links_is_not_a_listing() -> None:
    text = PROSE + " See [the report](https://example.test/r) for the method. " + PROSE
    assert not is_listing(text)
    assert decide_passage({"a": 0.9}, text) == ["a"]


def test_no_scores_is_no_labels() -> None:
    assert decide_passage({}, PROSE) == []


def test_the_passage_floor_is_not_below_the_source_floor() -> None:
    """Calibration found the band under the source floor mostly noise for
    single passages; a floor below it would label that band."""
    assert PASSAGE_FLOOR >= LABEL_FLOOR > OFFTOPIC_FLOOR
    assert 0.0 < LISTING_SHARE < 1.0


# --------------------------------------------------------------------------
# The fingerprint
# --------------------------------------------------------------------------


def test_the_fingerprint_is_stable_and_carries_the_source_basis() -> None:
    assert passage_fingerprint("v1:abc") == passage_fingerprint("v1:abc")
    assert passage_fingerprint("v1:abc").startswith("v1:abc/")


def test_a_different_source_basis_is_a_different_passage_basis() -> None:
    assert passage_fingerprint("v1:abc") != passage_fingerprint("v1:abd")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PASSAGE_FLOOR", 0.5),
        ("PASSAGE_MARGIN", 0.08),
        ("LISTING_SHARE", 0.7),
        ("PASSAGE_VERSION", 99),
    ],
)
def test_moving_a_passage_threshold_moves_the_basis(monkeypatch, name, value) -> None:
    before = passage_fingerprint("v1:abc")
    monkeypatch.setattr(passagetopics, name, value)
    assert passage_fingerprint("v1:abc") != before
