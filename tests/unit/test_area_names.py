"""What an area on the Map is called (task B-71).

Names were the top three terms joined by dots, and document furniture —
"nan", "arxiv", "doi", "title" — named live areas. A name is now one phrase,
and furniture is dropped both when an area is built and when it is read.
"""

from __future__ import annotations

import pytest

from meridian_core.areas import FURNITURE, STOPWORDS, distinctive_terms
from meridian_core.areaview import area_name, usable_terms
from worker.areas import distinct_per_level


@pytest.mark.parametrize(
    ("terms", "name"),
    [
        # Live names before the fix, and what they read as now.
        (["model", "arxiv", "title"], "Model"),
        (["nan", "arxiv", "cross-list", "neural"], "Neural"),
        (["vehicle", "road safety", "crash"], "Road safety"),
        (["volume number", "virus", "measles"], "Virus"),
        ([], "(no distinctive terms)"),
        (["nan", "doi"], "(no distinctive terms)"),
    ],
)
def test_an_area_is_named_by_one_phrase_without_furniture(terms, name) -> None:
    assert area_name(terms) == name
    assert " · " not in area_name(terms)


def test_a_phrase_is_only_preferred_from_the_top_terms() -> None:
    """A two-word phrase far down the list does not outrank the area's best term."""
    assert area_name(["shade", "canopy", "heat", "tree cover"]) == "Shade"


def test_furniture_is_dropped_from_the_terms_shown_too() -> None:
    assert usable_terms(["heat", "arxiv", "doi link", "shade"]) == ["heat", "shade"]


def test_furniture_never_becomes_a_term_at_build_time() -> None:
    text = "arXiv nan title doi volume number shade canopy heat shade canopy"
    (terms,) = distinctive_terms([[text, text]], top=5)
    assert not any(word in FURNITURE for term in terms for word in term.split())
    assert "shade" in " ".join(terms)


def test_furniture_is_a_subset_of_the_stopwords() -> None:
    assert FURNITURE <= STOPWORDS


def test_names_still_shared_carry_their_own_phrase() -> None:
    from worker.areas import distinct_per_level

    names = distinct_per_level(
        [2, 2, 2, 1],
        ["Transportation", "Transportation", "Law", "Transportation"],
        [["fares", "transit"], ["arxiv", "vehicle safety"], ["court"], ["x"]],
    )
    assert names == [
        "Transportation (fares)",
        "Transportation (vehicle safety)",  # furniture skipped
        "Law",
        "Transportation",  # a different level: not a clash
    ]


# --------------------------------------------------------------------------
# Shorter names where a phrase already tells areas apart (`B-102`)
# --------------------------------------------------------------------------


def test_a_phrase_makes_the_second_field_redundant() -> None:
    names = distinct_per_level(
        [3, 3],
        ["Transportation & Automotive Engineering"] * 2,
        [["robotaxis", "safety"], ["car ownership", "cars"]],
    )
    assert names == ["Transportation (robotaxis)", "Transportation (car ownership)"]


def test_a_short_name_that_would_collide_keeps_its_long_form() -> None:
    names = distinct_per_level(
        [3, 3, 3, 3],
        ["Transportation & Automotive Engineering"] * 2 + ["Transportation & Urban Studies"] * 2,
        [["robotaxis"], ["fares"], ["robotaxis"], ["cycling"]],
    )
    assert len(set(names)) == 4, names
    assert "Transportation (fares)" in names and "Transportation (cycling)" in names
    assert "Transportation & Automotive Engineering (robotaxis)" in names


def test_shortening_never_makes_a_name_shared_that_was_not() -> None:
    """A property over random names: per level, no more duplicates after than
    before, and every name is either kept or its own short form."""
    import random
    from collections import Counter

    from worker.areas import _shortened

    rng = random.Random(7)
    heads = ["Transportation", "Health", "Law"]
    seconds = ["Urban Studies", "Automotive Engineering", "History"]
    phrases = ["car", "bus", "fares", "cycling"]
    for _ in range(500):
        n = rng.randint(2, 14)
        levels = [rng.choice([2, 3]) for _ in range(n)]
        names = [
            rng.choice(
                [
                    f"{rng.choice(heads)} & {rng.choice(seconds)} ({rng.choice(phrases)})",
                    f"{rng.choice(heads)} ({rng.choice(phrases)})",
                    rng.choice(heads),
                ]
            )
            for _ in range(n)
        ]
        out = _shortened(levels, names)
        for level in (2, 3):
            at = [lv == level for lv in levels]

            def dupes(xs, at=at):
                return sum(
                    c - 1
                    for c in Counter(x for x, keep in zip(xs, at, strict=True) if keep).values()
                )

            assert dupes(out) <= dupes(names), (names, out)
        for o, name in zip(out, names, strict=True):
            short = f"{name.split(' & ')[0]} ({name.rsplit(' (', 1)[1]}" if " & " in name else name
            assert o in (name, short)
