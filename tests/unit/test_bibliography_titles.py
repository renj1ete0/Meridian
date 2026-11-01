"""What the fetch loop stores as a new page's title (task B-69)."""

from __future__ import annotations

import datetime as dt

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


def test_where_author_publisher_and_date_came_from_is_stored_beside_them() -> None:
    """`B-234`: provenance in `extra`, under the names `title_from` set the pattern for."""
    fields = _bibliography(
        doc(
            "Ferry waits fall",
            author="Mira Castellan",
            publisher="Velde Gazette",
            publication_date=dt.date(2021, 3, 9),
            metadata_from={"author": "jsonld", "publisher": "meta", "publication_date": "jsonld"},
            metadata_rejected={"publication_date": "2026-01-01"},
        ),
        url="https://news.velde.example/a",
    )
    assert fields["extra"]["author_from"] == "jsonld"
    assert fields["extra"]["publisher_from"] == "meta"
    assert fields["extra"]["date_from"] == "jsonld"
    assert fields["extra"]["rejected_metadata"] == {"publication_date": "2026-01-01"}


def test_no_provenance_is_written_for_a_field_left_empty() -> None:
    fields = _bibliography(
        doc("Ferry waits fall", metadata_from={"publication_date": "inferred"}),
        url="https://news.velde.example/a",
    )
    assert "extra" not in fields


def test_a_title_filled_from_declared_metadata_says_which() -> None:
    fields = _bibliography(
        doc("Wait times on a harbour ferry", metadata_from={"title": "citation"}),
        url="https://news.velde.example/a",
    )
    assert fields["extra"]["title_from"] == "citation"
