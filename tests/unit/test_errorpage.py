"""Pages that say they are not there (task B-45)."""

from __future__ import annotations

import pytest

from worker.extract.errorpage import BODY_WINDOW, error_page_reason, title_says_missing


@pytest.mark.parametrize(
    "title",
    [
        "Page not found – A School of Public Health",
        "Page not found - A Dental Faculty",
        "404 Not Found",
        "Error 404",
        "404",
        "Not Found | Some Agency",
        "Oops! That page can't be found.",
        "Some Site | Page does not exist",
        "Sorry, this page is no longer available",
    ],
)
def test_an_error_title_is_recognised(title: str) -> None:
    assert title_says_missing(title)


@pytest.mark.parametrize(
    "title",
    [
        "Rule 9005. Harmless Error",  # real statute text
        "Rule 404. Character Evidence; Other Crimes, Wrongs, or Acts",
        "Human error as a cause of vehicle crashes",
        "Error messages in HTML papers",
        "Inborn Errors of Metabolism - Signs/Symptoms",
        "Beyond trial and error: precision therapy",
        "Found in translation: a study",
        "Route 404 bus timetable changes",
        None,
        "",
    ],
)
def test_a_real_document_with_an_error_word_is_not(title) -> None:
    assert not title_says_missing(title)


def test_the_body_confirms_when_the_title_is_generic() -> None:
    text = "Skip to content\nThe page you are looking for could not be found.\nTry the search box."
    assert error_page_reason("A Council", text) == "body"


def test_the_body_sentence_counts_only_near_the_top() -> None:
    text = "A long article about link rot. " * 40 + "The page you requested could not be found."
    assert len(text) > BODY_WINDOW
    assert error_page_reason("On link rot", text) is None


def test_an_ordinary_page_is_not_an_error_page() -> None:
    assert error_page_reason("Walking and health", "People who walk more are healthier.") is None
