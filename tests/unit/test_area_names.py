"""What an area on the Map is called (task B-71).

Names were the top three terms joined by dots, and document furniture —
"nan", "arxiv", "doi", "title" — named live areas. A name is now one phrase,
and furniture is dropped both when an area is built and when it is read.
"""

from __future__ import annotations

import pytest

from meridian_core.areas import FURNITURE, STOPWORDS, distinctive_terms
from meridian_core.areaview import area_name, usable_terms


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
