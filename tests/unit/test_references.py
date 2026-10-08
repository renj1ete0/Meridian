"""Telling a reference list from a passage that cites as it argues (`B-171`).

The samples are shaped like what the corpus holds: an author-year list, a journal's
numbered list whose entries the converter dropped, a review that cites inline.
"""

from __future__ import annotations

from meridian_core import references
from meridian_core.references import MIN_ENTRIES, is_entry, is_reference_list

AUTHOR_YEAR_LIST = """\
Bai, W., Quan, J., Fu, L., & Wang, X. (2017). Online fair allocation in vehicle sharing.
Banister, D. (1997). Reducing the need to travel. Environment and Planning B, 24, 437–449.
Bansal, P., & Kockelman, K. M. (2017). Forecasting long-term adoption of vehicle technologies.
Transportation Research Part A, 95, 49–63.
Beirão, G., & Cabral, J. S. (2007). Attitudes towards public transport and private car."""

NUMBERED_LIST = """- 55
- 56 ParkinJ.ClarkB.ClaytonW. (2018). Vehicle interactions in the urban street: a research agenda.
- 57
- 58
- 59 ReedN.LeimanT.PaladeP. (2021). Ethics of automated vehicles: breaking traffic rules for safety.
- 60
- 61"""

REVIEW = """Eleven of the twelve low risk-of-bias studies reported an effect at or below 20 ppm
(Adedara et al. 2017; Bartos et al. 2019). Two found none at any dose, and the remaining study
measured only one concentration, which the authors describe as a limitation of the design.
Taken together, the evidence points to an effect at the lower concentrations, with the caveat
that most studies used a single strain (Wu et al. 2008)."""


def test_an_author_year_list_is_a_reference_list() -> None:
    assert is_reference_list(AUTHOR_YEAR_LIST)


def test_a_numbered_list_whose_entries_were_dropped_is_a_reference_list() -> None:
    assert is_reference_list(NUMBERED_LIST)


def test_a_review_that_cites_as_it_argues_is_not() -> None:
    """The rejection that matters: these passages carry the claims synthesis is for."""
    assert not is_reference_list(REVIEW)


def test_one_citation_is_not_a_list() -> None:
    one = (
        "Banister, D. (1997). Reducing the need to travel. Environment and Planning B, 24, 437–449."
    )
    assert is_entry(one)
    assert not is_reference_list(one), f"a list needs at least {MIN_ENTRIES} entries"


def test_empty_and_blank_text_is_not_a_list() -> None:
    assert not is_reference_list("")
    assert not is_reference_list("\n \n")


def test_the_share_is_measured_in_characters_not_lines() -> None:
    """Three short entries under a long paragraph are a paragraph with a footnote."""
    passage = REVIEW + "\n- 1\n- 2\n- 3"
    assert not is_reference_list(passage)
    assert references.MIN_SHARE > 0.5, "below 0.6 sat review passages with claims (measured)"


def test_prose_lines_with_numbers_are_not_entries() -> None:
    for line in [
        "Ridership rose by 12% in 2019, the operator said.",
        "The pilot ran from 2018 to 2020 in three districts.",
        "Fares were $2.50, the same as the fixed-route system.",
    ]:
        assert not is_entry(line), line
