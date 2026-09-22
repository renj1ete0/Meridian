"""Reciprocal rank fusion (task P2-06, spec §12.5).

No database. The arithmetic is the part of hybrid search most likely to be
subtly wrong and the part a Postgres fixture tells you least about — a fusion
that silently favoured one arm would still return plausible results, in a
plausible order, and nothing would look broken.
"""

from __future__ import annotations

import pytest

from meridian_core.search import RRF_K, cap_per_source, fuse


def test_an_empty_search_fuses_to_nothing() -> None:
    assert fuse([], []) == {}


def test_rank_one_scores_more_than_rank_two() -> None:
    scores = fuse([10, 20, 30])
    assert scores[10] > scores[20] > scores[30]


def test_the_formula_is_the_published_one() -> None:
    """`1 / (k + rank)`, rank 1-based. Pinned because an off-by-one here is
    invisible: 0-based ranks still order correctly, they just weight the top
    result far too heavily."""
    scores = fuse([7])
    assert scores[7] == pytest.approx(1.0 / (RRF_K + 1))


def test_agreement_between_arms_beats_a_better_rank_in_one() -> None:
    """The property that justifies using RRF at all.

    A chunk both arms found at rank 2 should outrank one that only the lexical
    arm found at rank 1. If it does not, fusion is adding nothing that taking
    the better arm alone would not — and the second query is wasted work.
    """
    lexical = [100, 200]
    vector = [300, 200]

    scores = fuse(lexical, vector)

    assert scores[200] > scores[100], "a chunk found by both arms lost to a single-arm hit"
    assert scores[200] > scores[300]


def test_a_chunk_in_one_arm_only_still_scores() -> None:
    """Lexical and semantic retrieval disagree constantly, and the disagreements
    are often the point — an exact term match with an unrelated embedding is
    still the passage that contains the term."""
    scores = fuse([1], [2])
    assert set(scores) == {1, 2}
    assert scores[1] == scores[2]


def test_k_damps_the_gap_between_ranks() -> None:
    """Larger k compresses the distance between adjacent ranks, which is what
    makes the constant the knob for "how much does being first matter"."""
    tight = fuse([1, 2], k=1)
    loose = fuse([1, 2], k=1000)

    assert tight[1] / tight[2] > loose[1] / loose[2]


def test_fusion_takes_any_number_of_arms() -> None:
    """Signature-level: a third retrieval arm (a graph walk, a title match) is a
    plausible addition, and it should not require rewriting the fusion."""
    scores = fuse([1], [1], [1])
    assert scores[1] == pytest.approx(3.0 / (RRF_K + 1))


# --------------------------------------------------------------------------
# What a page number counts (task P2-18, spec §5.3)
# --------------------------------------------------------------------------


def test_a_pdf_counts_pages_and_a_web_page_counts_characters() -> None:
    """§5.3: "page number for paginated documents, character offset otherwise".

    A rule every consumer previously had to know and apply for itself, from a
    media type that was not on the hit — so the UI either re-derived it or
    labelled every citation "page/offset". Derived once, here.
    """
    from meridian_core.search import page_unit_for

    assert page_unit_for("application/pdf") == "page"
    assert page_unit_for("text/html") == "offset"


def test_an_unrecorded_media_type_is_unknown_rather_than_guessed() -> None:
    """Guessing "offset" mislabels every PDF and guessing "page" mislabels every
    web page. A source whose media type was never recorded is genuinely unknown,
    and saying so beats a confident wrong label on a citation someone will try
    to follow."""
    from meridian_core.search import page_unit_for

    assert page_unit_for(None) is None


# --------------------------------------------------------------------------
# Capping how much of a page one document may fill (task `B-30`, §12.5)
# --------------------------------------------------------------------------
#
# Measured on the first real corpus: the vector arm drew 42% of its top ten
# from the probe chunk's own document, and ten hits spanned about four sources.
# Adjacent chunks of a document genuinely are its nearest neighbours, so this
# is not a bug in the arm — it is a question about what a page of results
# should look like, and the answer is a decision rather than a fix. The cap is
# therefore off by default, exactly as `P2-20`'s ageing decay is, so that
# `P2-04`'s benchmark and `P2-09`'s go/no-go keep measuring one thing.


def test_the_cap_spreads_a_page_across_sources() -> None:
    ordered = [1, 2, 3, 4, 5, 6]
    source_of = {1: 100, 2: 100, 3: 100, 4: 200, 5: 300, 6: 100}

    kept, _ = cap_per_source(ordered, source_of, cap=2, limit=4)

    assert kept == [1, 2, 4, 5]


def test_rank_order_survives_the_cap() -> None:
    """The cap decides what appears, never what leads. A reordering that
    promoted a worse hit would be answering a different question than the one
    that was asked."""
    ordered = [10, 11, 12, 13]
    source_of = {10: 1, 11: 1, 12: 2, 13: 2}

    kept, _ = cap_per_source(ordered, source_of, cap=1, limit=2)

    assert kept == [10, 12]


def test_a_short_page_is_backfilled_rather_than_left_short() -> None:
    """Six results where twenty exist is worse than repeating a source. The cap
    is about what leads, not about withholding the corpus."""
    ordered = [1, 2, 3, 4]
    source_of = dict.fromkeys(ordered, 42)

    kept, _ = cap_per_source(ordered, source_of, cap=1, limit=3)

    assert kept == [1, 2, 3], "the page should fill from what the cap set aside"


def test_the_displacement_is_countable() -> None:
    """`P2-20`'s rule: an adjustment that changed the result is shown. "Why is
    that paper not here" has no answer otherwise."""
    ordered = [1, 2, 3, 4, 5, 6]
    source_of = {1: 1, 2: 1, 3: 1, 4: 2, 5: 3, 6: 4}

    _, displaced = cap_per_source(ordered, source_of, cap=1, limit=3)

    # 2 and 3 would have been on the page and were pushed off it by the cap.
    assert displaced == 2


def test_a_chunk_whose_source_is_unknown_is_never_dropped() -> None:
    """A missing source id is missing information, not a licence to discard a
    hit the arms both ranked."""
    kept, _ = cap_per_source([1, 2], {1: 5}, cap=1, limit=2)

    assert kept == [1, 2]
