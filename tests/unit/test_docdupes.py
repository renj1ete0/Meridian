"""Document-level duplicates: the rules at their edges (task B-44)."""

from __future__ import annotations

import numpy as np

from meridian_core.docdupes import (
    COMMON_PASSAGE,
    EXACT_SHARE,
    NEAR_COSINE,
    Pair,
    canonical,
    exact_pairs,
    near_pairs,
    title_key,
)


def unit(*weights: float) -> np.ndarray:
    v = np.zeros(8)
    v[: len(weights)] = weights
    return v / np.linalg.norm(v)


def test_a_url_variant_with_the_same_passages_is_an_exact_copy() -> None:
    text = ["First paragraph of the study.", "Second paragraph, results."]
    pairs = exact_pairs({1: text, 2: ["  first  paragraph of the STUDY. ", *text[1:]]})
    assert pairs == [Pair(2, 1, "exact", 1.0)]


def test_sharing_less_than_the_exact_share_is_not_a_copy() -> None:
    shared = [f"shared passage {i}" for i in range(8)]
    own = [f"own passage {i}" for i in range(3)]
    assert exact_pairs({1: shared, 2: shared + own}) == []
    assert EXACT_SHARE > 8 / 11


def test_a_passage_every_page_carries_does_not_make_pages_copies() -> None:
    footer = "The same footer on every page of the site."
    pages = {i: [footer, f"unique body {i}"] for i in range(COMMON_PASSAGE + 2)}
    pages[1000] = [footer]
    assert exact_pairs(pages) == []


def test_the_copy_is_always_the_later_source() -> None:
    text = ["The same text."]
    assert exact_pairs({7: text, 3: text}) == [Pair(7, 3, "exact", 1.0)]


def test_two_documents_on_one_template_with_different_titles_are_not_near_copies() -> None:
    """Measured: consecutive rules of one court at 0.97 cosine."""
    pairs = near_pairs(
        {1: "Rule 7020. Permissive Joinder of Parties", 2: "Rule 7021. Misjoinder and Nonjoinder"},
        {1: unit(1, 0.1), 2: unit(1, 0.1)},
        {1: 1000, 2: 1000},
    )
    assert pairs == []


def test_a_pdf_and_its_html_page_are_near_copies() -> None:
    pairs = near_pairs(
        {1: "The Impact of Shared Vehicles", 2: "The impact of shared vehicles."},
        {1: unit(1, 0.05), 2: unit(1, 0.06)},
        {1: 10_000, 2: 9_000},
    )
    assert [(p.later, p.earlier, p.reason) for p in pairs] == [(2, 1, "near")]


def test_near_needs_the_cosine_and_the_length() -> None:
    titles = {1: "A long enough title", 2: "A long enough title"}
    far = unit(1, 0), unit(0.8, 0.6)
    assert float(far[0] @ far[1]) < NEAR_COSINE
    assert near_pairs(titles, {1: far[0], 2: far[1]}, {1: 1000, 2: 1000}) == []
    assert near_pairs(titles, {1: unit(1), 2: unit(1)}, {1: 10_000, 2: 1_000}) == []


def test_a_short_generic_title_is_not_comparable() -> None:
    assert title_key("Home") is None
    assert title_key("  The Impact!  ") == "the impact"


def test_a_chain_points_at_its_root() -> None:
    verdicts = canonical([Pair(3, 2, "exact", 1.0), Pair(2, 1, "exact", 1.0)])
    assert verdicts[3].earlier == 1 and verdicts[2].earlier == 1


def test_exact_beats_near_for_one_source() -> None:
    verdicts = canonical([Pair(5, 1, "near", 0.99), Pair(5, 4, "exact", 0.95)])
    assert (verdicts[5].earlier, verdicts[5].reason) == (4, "exact")


def test_a_cited_source_is_never_marked_and_its_copies_stop_at_it() -> None:
    verdicts = canonical([Pair(3, 2, "exact", 1.0), Pair(2, 1, "exact", 1.0)], protected={2})
    assert 2 not in verdicts
    assert verdicts[3].earlier == 2


# -- B-88: reasons that disagree about direction make loops ---------------------------


def test_a_translation_and_a_near_copy_pointing_opposite_ways_mark_no_page_its_own_copy():
    """The live case: a page declares its English version, and the English one
    reads as a near copy of the older page. The walk looped back to its start."""
    verdicts = canonical([Pair(10, 20, "translation", 1.0), Pair(20, 10, "near", 0.99)])
    assert all(pair.later != pair.earlier for pair in verdicts.values())
    assert verdicts == {20: Pair(20, 10, "near", 0.99)}


def test_a_longer_loop_has_one_root_for_all_its_members():
    pairs = [Pair(1, 2, "exact", 1.0), Pair(2, 3, "exact", 1.0), Pair(3, 1, "exact", 1.0)]
    verdicts = canonical(pairs)
    assert {later: p.earlier for later, p in verdicts.items()} == {2: 1, 3: 1}


def test_a_chain_into_a_loop_reaches_the_loops_root():
    pairs = [Pair(7, 5, "near", 0.99), Pair(5, 6, "translation", 1.0), Pair(6, 5, "near", 0.99)]
    verdicts = canonical(pairs)
    assert verdicts[7].earlier == 5 and 5 not in verdicts and verdicts[6].earlier == 5


def test_verdicts_never_mark_a_root_and_never_point_at_themselves():
    """Whatever the pairs, a verdict's target is itself unmarked: search hides
    copies and keeps canonicals, so a target that is also a copy hides both."""
    import random

    rng = random.Random(0)
    reasons = ["exact", "near", "translation"]
    for _ in range(300):
        pairs = [
            Pair(rng.randrange(12), rng.randrange(12), rng.choice(reasons), 1.0)
            for _ in range(rng.randrange(1, 15))
        ]
        pairs = [p for p in pairs if p.later != p.earlier]
        verdicts = canonical(pairs)
        for later, pair in verdicts.items():
            assert pair.later == later != pair.earlier
            assert pair.earlier not in verdicts, (pairs, verdicts)
