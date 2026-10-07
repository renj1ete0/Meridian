"""Titles that are not titles (task B-69).

Every case here was found on a live corpus: the placeholders PDF exporters
write, the site name standing in for the page, the numbered heading. The
refusals matter as much as the keeps — a real title mangled is worse than a
placeholder left alone.
"""

from __future__ import annotations

import pytest

from meridian_core.titles import GENERIC, clean_title, title_from_text


@pytest.mark.parametrize(
    "placeholder",
    [
        "untitled",
        "Untitled Document",
        "(untitled)",
        "nan",
        "NaN",
        "None",
        "null",
        "N/A",
        "Microsoft Word - final report v3.docx",
        "PowerPoint Presentation",
        "Document1",
        "report_2024_final.pdf",
        "Slide 1",
        "",
        "  ",
        "ab",
    ],
)
def test_a_placeholder_is_no_title(placeholder: str) -> None:
    assert clean_title(placeholder) is None


@pytest.mark.parametrize("generic", sorted(GENERIC))
def test_a_generic_page_name_is_no_title(generic: str) -> None:
    assert clean_title(generic.title()) is None


@pytest.mark.parametrize(
    ("declared", "publisher", "host", "expected"),
    [
        ("Autonomous Vehicles | LTA.GOV.SG", None, "www.lta.gov.sg", "Autonomous Vehicles"),
        ("VA.gov | Veterans Affairs", None, "va.gov", "Veterans Affairs"),
        ("Results | Kinder Institute", "Kinder Institute", None, None),
        ("LTA.GOV.SG", None, "lta.gov.sg", None),
        ("Home - City of Seattle", "City of Seattle", None, None),
        ("Fares – Transport for London", "Transport for London", None, "Fares"),
    ],
)
def test_the_site_is_taken_off_the_page(declared, publisher, host, expected) -> None:
    assert clean_title(declared, publisher=publisher, host=host) == expected


@pytest.mark.parametrize(
    "real",
    [
        "A User-driven Design Framework for Robotaxis",
        "Road Traffic Act - Section 5",  # both halves are the page's
        "Level-4 automated shuttles in mixed traffic",
        "COVID-19 and urban walking",
        "2030 Land Transport Master Plan",  # a leading number that is part of the name
        "ISO 26262:2018",
    ],
)
def test_a_real_title_is_kept_as_it_was(real: str) -> None:
    assert clean_title(real) == real


def test_a_section_number_glued_to_a_heading_is_not_kept() -> None:
    assert clean_title("1Introduction") is None
    assert clean_title("2.3Methods of demand estimation") == "Methods of demand estimation"


def test_whitespace_is_normalised() -> None:
    assert clean_title("  Shared   autonomous\n vehicles  ") == "Shared autonomous vehicles"


# -- falling back to the text --------------------------------------------------------


def test_the_first_heading_like_line_is_used() -> None:
    text = (
        "Transportation Research Part D | Vol 12\n"
        "Received: 3 March 2020\n"
        "Shared autonomous vehicles in microtransit systems\n"
        "Abstract. We study…"
    )
    assert title_from_text(text) == "Shared autonomous vehicles in microtransit systems"


@pytest.mark.parametrize(
    "text",
    [
        "Skip to main content\nMenu\nHome",
        "This paper studies demand. It finds that fares matter.",
        "https://example.org/paper.pdf",
        "© 2024 Elsevier Ltd. All rights reserved",
        # Found in the live report as bad guesses.
        "Read the LTA Annual Report 2024/25 here!",
        "TRID is an integrated database that combines the records from",
        "Submitted to Transportation Science",
        "Mohd. Hafiz Hasan · Pascal Van Hentenryck",
        "Ann Smith, Bo Jones, Cy Lee",
        "",
        None,
    ],
)
def test_no_heading_means_no_title(text) -> None:
    assert title_from_text(text) is None


def test_a_wrapped_sentence_is_not_a_heading() -> None:
    """Found in the live report: the rejected sentence's second line was taken."""
    text = (
        "TRID is an integrated database that combines the records from\n"
        "Database and the OECD's Joint Transport Research Centre's International Transport\n"
    )
    assert title_from_text(text) is None


def test_a_heading_after_a_blank_line_still_counts() -> None:
    text = (
        "TRID is an integrated database that combines the records from\n"
        "\n"
        "Planning for Autonomous Cars"
    )
    assert title_from_text(text) == "Planning for Autonomous Cars"


def test_a_title_case_title_with_commas_is_still_a_title() -> None:
    assert (
        title_from_text("Planes, Trains and Automobiles in the City")
        == "Planes, Trains and Automobiles in the City"
    )


def test_only_the_start_of_the_text_is_read() -> None:
    text = "\n".join(["x"] * 40 + ["Shared autonomous vehicles in microtransit systems"])
    assert title_from_text(text) is None


def test_small_capitals_in_a_declared_title_are_read_as_letters() -> None:
    """A PDF's metadata title in Adobe's private-use small capitals (found on a live corpus)."""
    from meridian_core.titles import plain_letters

    declared = "T J  L U"
    assert clean_title(declared) == "The Journal of Land Use"
    assert plain_letters("V. 9 ") == "Vol. 9 ", "other private use is kept"
