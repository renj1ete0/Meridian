"""Sites as Find's filter reads them, and the new filters in saved views (task B-254)."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from meridian_core.boilerplate import host_key
from meridian_core.models import SavedView
from meridian_core.search import SearchFilters, site_key
from meridian_core.watch import _narrows, filters_of


@pytest.mark.parametrize(
    "value,expected",
    [
        ("example.org", "example.org"),
        ("WWW.Example.Org", "example.org"),
        ("https://www.example.org/news/a?b=1", "example.org"),
        ("http://news.example.org:8080/", "news.example.org"),
        ("example.org.", "example.org"),
        ("  example.org  ", "example.org"),
    ],
)
def test_what_a_reader_types_or_pastes_reads_as_a_host(value, expected) -> None:
    assert site_key(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "localhost", "not a host", "https://", "www."])
def test_something_with_no_host_is_refused(value) -> None:
    assert site_key(value) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.example.org/a",
        "https://News.Example.org:443/b",
        "http://example.org./c",
    ],
)
def test_a_site_key_agrees_with_how_hosts_are_counted(url) -> None:
    """The filter matches against `chunks.host_of`, which mirrors `host_key`; a pasted URL
    must land on the same host, or a site picked from the list would not match itself."""
    assert site_key(url) == host_key(url)


def test_saved_view_filters_carry_kinds_and_sites() -> None:
    view = SavedView(
        name="v",
        filters={"doc_kinds": ["news"], "sites": ["example.org"]},
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    filters = filters_of(view)

    assert filters is not None
    assert (list(filters.doc_kinds), list(filters.sites)) == (["news"], ["example.org"])


@pytest.mark.parametrize("field,value", [("doc_kinds", ["news"]), ("sites", ["example.org"])])
def test_a_kind_or_a_site_alone_is_enough_to_watch(field, value) -> None:
    """A view with no words and only one of these still counts what is new for it."""
    assert not _narrows(SearchFilters())
    assert _narrows(dataclasses.replace(SearchFilters(), **{field: value}))
