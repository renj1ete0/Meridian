"""What the fetch loop stores as a new page's title (task B-69)."""

from __future__ import annotations

from worker.extract.base import ExtractedDocument
from worker.main import _bibliography


def doc(title, text="", **fields) -> ExtractedDocument:
    return ExtractedDocument(title=title, text=text, extractor="test", **fields)


def test_a_placeholder_becomes_the_heading_and_is_marked_guessed() -> None:
    fields = _bibliography(
        doc("untitled", "Shared autonomous vehicles in microtransit systems\nBody."),
        url="https://example.org/a.pdf",
    )
    assert fields["title"] == "Shared autonomous vehicles in microtransit systems"
    assert fields["extra"] == {"title_from": "text", "declared_title": "untitled"}


def test_the_site_name_comes_off_the_page_title() -> None:
    fields = _bibliography(
        doc("Autonomous Vehicles | LTA.GOV.SG"), url="https://www.lta.gov.sg/av.html"
    )
    assert fields["title"] == "Autonomous Vehicles"
    assert fields["extra"]["declared_title"] == "Autonomous Vehicles | LTA.GOV.SG"


def test_nothing_usable_stores_none_so_an_earlier_title_survives() -> None:
    """`upsert_source` treats None as "the page did not say"."""
    fields = _bibliography(doc("Home", "We use cookies."), url="https://example.org/")
    assert fields["title"] is None


def test_a_real_title_goes_through_without_a_note() -> None:
    fields = _bibliography(doc("Road Traffic Act - Section 5"), url="https://example.org/")
    assert fields["title"] == "Road Traffic Act - Section 5"
    assert "extra" not in fields
