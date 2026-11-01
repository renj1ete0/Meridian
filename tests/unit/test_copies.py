"""Folding copies of a passage at query time, and setting reference lists back (`B-236`).

The copies are shaped like what mirrors produce on a real corpus: the same document as an
HTML page and a PDF, cut into passages at other boundaries, with another header line, or
with case and spacing changed by another converter.
"""

from __future__ import annotations

from meridian_core.copies import (
    MIN_COVERAGE,
    MIN_SHINGLES,
    SHINGLE_WORDS,
    fold_copies,
    set_back,
    shingles,
)

FIRST_HALF = (
    "The pilot ran for eighteen months across three districts of Velmora and carried "
    "forty-one thousand passengers. Average wait times fell from twenty-two minutes to "
    "eleven once the booking window was shortened."
)
SECOND_HALF = (
    "The operating cost per trip settled near nine units after the second fleet expansion. "
    "Riders over sixty-five made up a third of all bookings, most of them for medical "
    "appointments, and complaints fell by half in the second year."
)
REPORT = FIRST_HALF + " " + SECOND_HALF

#: The last sentence of the first half and the first of the second: a passage cut across
#: the boundary another converter put between them.
STRADDLING = FIRST_HALF.split(". ", 1)[1] + " " + SECOND_HALF.split(". ", 1)[0] + "."

#: The same report as another host serves it: another first line, case and spacing changed.
MIRRORED = "Posted by the Velmora transit office\n" + REPORT.upper().replace(" ", "  ")

#: A different report on the same subject, sharing words but no sentences.
OTHER = (
    "In Quorran the on-demand service was withdrawn after nine months. The council found "
    "that wait times rose to thirty minutes at peak and that the cost per trip exceeded "
    "the fixed route it replaced by a factor of four, while ridership never passed eight "
    "hundred a week despite two rounds of fare cuts and a marketing campaign."
)


def fold(order, texts, sources, limit=10):
    return fold_copies(order, texts, sources, limit=limit)


def test_a_mirrored_passage_folds_into_the_first_copy() -> None:
    kept, folded = fold([1, 2, 3], {1: REPORT, 2: MIRRORED, 3: OTHER}, {1: 10, 2: 20, 3: 30})

    assert kept == [1, 3]
    assert folded == {1: 1}


def test_the_better_ranked_copy_is_the_one_kept() -> None:
    """Whichever came first in the ranking survives; the fold never reorders."""
    kept, folded = fold([2, 3, 1], {1: REPORT, 2: MIRRORED, 3: OTHER}, {1: 10, 2: 20, 3: 30})

    assert kept == [2, 3]
    assert folded == {2: 1}


def test_a_copy_cut_at_other_boundaries_folds_across_two_kept_passages() -> None:
    """A PDF's passage straddling two of the HTML page's: neither alone holds enough of its
    words, both together do. The fold goes to the one it shares most with."""
    straddling = STRADDLING
    texts = {1: FIRST_HALF, 2: SECOND_HALF, 3: straddling}
    sources = {1: 10, 2: 10, 3: 20}
    for kept_one in (1, 2):
        alone, _ = fold([kept_one, 3], texts, sources)
        assert alone == [kept_one, 3], "one half alone is under the coverage"

    kept, folded = fold([1, 2, 3], texts, sources)

    assert kept == [1, 2]
    assert sum(folded.values()) == 1


def test_coverage_from_two_different_sources_does_not_add_up() -> None:
    """Words found half in one page and half in another are a passage combining two sources,
    not a copy of either."""
    straddling = STRADDLING
    kept, folded = fold(
        [1, 2, 3], {1: FIRST_HALF, 2: SECOND_HALF, 3: straddling}, {1: 10, 2: 11, 3: 20}
    )

    assert kept == [1, 2, 3] and folded == {}


def test_passages_of_one_source_never_fold_into_each_other() -> None:
    """A document repeating itself is the per-source cap's concern, not a copy elsewhere."""
    kept, folded = fold([1, 2], {1: REPORT, 2: MIRRORED}, {1: 10, 2: 10})

    assert kept == [1, 2] and folded == {}


def test_passages_on_one_subject_that_share_no_sentences_are_not_copies() -> None:
    """The rejection that matters: two reports on one subject are two pieces of evidence."""
    kept, folded = fold([1, 3], {1: REPORT, 3: OTHER}, {1: 10, 3: 30})

    assert kept == [1, 3] and folded == {}


def test_a_passage_quoting_one_sentence_of_another_is_not_a_copy() -> None:
    quoting = (
        "A review of Velmora's scheme noted that 'average wait times fell from twenty-two "
        "minutes to eleven once the booking window was shortened', but argued the cost per "
        "trip was never compared with the bus routes the service shadowed, and that the "
        "medical bookings would have been made by any means available to the riders."
    )
    kept, folded = fold([1, 2], {1: REPORT, 2: quoting}, {1: 10, 2: 20})

    assert kept == [1, 2] and folded == {}


def test_a_longer_passage_holding_a_kept_one_and_more_is_not_folded() -> None:
    """Coverage is of the later passage's own words: when most of them are new, it stays."""
    longer = FIRST_HALF + " " + OTHER
    assert len(shingles(FIRST_HALF)) / len(shingles(longer)) < MIN_COVERAGE

    kept, folded = fold([1, 2], {1: FIRST_HALF, 2: longer}, {1: 10, 2: 20})

    assert kept == [1, 2] and folded == {}


def test_short_passages_are_never_folded() -> None:
    """A heading or a table row is shared by unrelated documents."""
    heading = "Results and discussion of the pilot"
    assert len(shingles(heading)) < MIN_SHINGLES

    kept, folded = fold([1, 2], {1: heading, 2: heading}, {1: 10, 2: 20})

    assert kept == [1, 2] and folded == {}


def test_the_limit_counts_kept_passages_not_candidates_walked() -> None:
    texts = {1: REPORT, 2: MIRRORED, 3: OTHER, 4: "a fourth passage"}
    kept, folded = fold([1, 2, 3, 4], texts, {1: 10, 2: 20, 3: 30, 4: 40}, limit=2)

    assert kept == [1, 3]
    assert folded == {1: 1}


def test_a_passage_without_text_or_source_is_kept_as_it_is() -> None:
    kept, folded = fold([1, 9, 2, 8], {1: REPORT, 2: MIRRORED, 8: MIRRORED}, {1: 10, 2: 20})

    assert kept == [1, 9, 8] and folded == {1: 1}


def test_shingles_ignore_case_and_punctuation_but_not_word_order() -> None:
    a = "The cost per trip fell, sharply; after the second expansion of the fleet."
    assert shingles(a) == shingles(a.upper().replace(",", "").replace(";", " "))
    assert shingles(a) != shingles(" ".join(reversed(a.split())))
    assert len(shingles("one two three four")) == 0, f"fewer than {SHINGLE_WORDS} words"


def test_set_back_moves_flagged_ids_after_the_rest_in_their_order() -> None:
    assert set_back([5, 1, 4, 2, 3], {1, 2}) == [5, 4, 3, 1, 2]
    assert set_back([5, 1], set()) == [5, 1]
    assert set_back([5, 1], {5, 1}) == [5, 1]
