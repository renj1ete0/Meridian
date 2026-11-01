"""Rules for a source's author, publisher and date (task B-234).

Each rule is measured on a live corpus (docs/features/extraction.md#source-metadata); the
refusals here weigh as much as the acceptances, since a wrong date or a CMS login cited as
an author is worse than none.
"""

from __future__ import annotations

import datetime as dt

import pytest

from meridian_core.bibliography import (
    DECLARED,
    FROM_KEY,
    is_declared,
    join_authors,
    judge_author,
    judge_date,
    judge_publisher,
    parse_day,
)

FETCHED = dt.date(2026, 6, 15)
FIRST = dt.date(2026, 5, 2)


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["jsonld", "citation", "inferred", "pdfinfo"])
def test_an_ordinary_date_is_kept_from_any_source(source: str) -> None:
    day = dt.date(2021, 4, 9)
    assert judge_date(day, source=source, fetched=FETCHED).value == day


@pytest.mark.parametrize("source", sorted(DECLARED | {"inferred"}))
def test_a_date_after_the_fetch_is_refused_whatever_declared_it(source: str) -> None:
    verdict = judge_date(dt.date(2026, 6, 20), source=source, fetched=FETCHED)
    assert (verdict.value, verdict.rule) == (None, "future")


def test_a_day_ahead_is_a_time_zone_not_the_future() -> None:
    """A date written in a zone ahead of UTC can read as tomorrow; it is not refused."""
    day = dt.date(2026, 6, 16)
    assert judge_date(day, source="jsonld", fetched=FETCHED).value == day


@pytest.mark.parametrize("day", [dt.date(1899, 12, 31), dt.date(1970, 1, 1)])
def test_placeholder_dates_are_refused(day: dt.date) -> None:
    assert judge_date(day, source="jsonld", fetched=FETCHED).rule == "placeholder"


def test_an_inferred_first_of_january_is_a_copyright_year() -> None:
    verdict = judge_date(dt.date(2026, 1, 1), source="inferred", fetched=FETCHED)
    assert (verdict.value, verdict.rule) == (None, "year only")


@pytest.mark.parametrize("source", sorted(DECLARED))
def test_a_declared_first_of_january_is_kept(source: str) -> None:
    """A full day the document declares is a fact; only a completed year is refused."""
    day = dt.date(2019, 1, 1)
    assert judge_date(day, source=source, fetched=FETCHED).value == day


@pytest.mark.parametrize("day", [FETCHED, FETCHED - dt.timedelta(days=1), FIRST])
def test_an_inferred_date_on_a_fetch_day_is_the_server_clock(day: dt.date) -> None:
    verdict = judge_date(day, source="inferred", fetched=FETCHED, also_fetched=FIRST)
    assert (verdict.value, verdict.rule) == (None, "fetch day")


def test_a_declared_date_on_the_fetch_day_is_kept() -> None:
    """A news page published the day it was crawled says so in its metadata."""
    assert judge_date(FETCHED, source="article", fetched=FETCHED).value == FETCHED


def test_a_pdf_made_when_it_was_fetched_was_rendered_on_request() -> None:
    verdict = judge_date(FIRST, source="pdfinfo", fetched=FETCHED, also_fetched=FIRST)
    assert (verdict.value, verdict.rule) == (None, "made on request")


def test_a_pdf_cannot_predate_the_format() -> None:
    verdict = judge_date(dt.date(1988, 6, 1), source="pdfinfo", fetched=FETCHED)
    assert verdict.rule == "before pdf"
    # The same date declared by a scholarly page is a paper from 1988.
    assert judge_date(dt.date(1988, 6, 1), source="citation", fetched=FETCHED).value


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-05-01", (2024, 5, 1)),
        ("2024/5/1", (2024, 5, 1)),
        ("2024-05-01T23:30:00-05:00", (2024, 5, 1)),
        ("2017-09-22T14:06:39Z", (2017, 9, 22)),
        ("D:20190314090000+08'00'", (2019, 3, 14)),
    ],
)
def test_full_days_are_parsed_as_written(raw: str, expected: tuple[int, int, int]) -> None:
    assert parse_day(raw) == dt.date(*expected)


@pytest.mark.parametrize(
    "raw", [None, "", "2024", "2024-05", "May 2024", "2024-13-01", "2024-02-30", 20240501, "D:2024"]
)
def test_anything_short_of_a_full_day_is_refused_not_completed(raw: object) -> None:
    assert parse_day(raw) is None


# --------------------------------------------------------------------------
# Authors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Mira Castellan",
        "Castellan, Mira; Odum, Pell",
        "Northwind Department of Health",
        "Velde Transit Authority",
        "北原 誠",
        "J. R. Okafor",
    ],
)
def test_names_and_organisations_are_kept(value: str) -> None:
    assert judge_author(value).value == value


def test_a_byline_prefix_is_dropped() -> None:
    assert judge_author("By Mira Castellan").value == "Mira Castellan"


@pytest.mark.parametrize(
    "value",
    [
        "Author",
        "Authors",
        "Author last name",
        "Admin",
        "Administrator",
        "Webadmin",
        "Super User",
        "Microsoft Office User",
        "User",
        "Test Auth",
        "Staff Writer",
        "Un-named",
        "python-docx",
        "September",
        "[NULL]",
    ],
)
def test_placeholders_are_refused(value: str) -> None:
    verdict = judge_author(value)
    assert (verdict.value, verdict.rule) == (None, "placeholder")


@pytest.mark.parametrize(
    "value", ["Government Websites by CivicFrame", "Powered by Sitebuilder", "Pell Lab template"]
)
def test_a_site_credit_is_refused(value: str) -> None:
    assert judge_author(value).rule == "site credit"


@pytest.mark.parametrize(
    "value", ["https://social.example/pell", "www.example.org", "desk@example.org"]
)
def test_an_address_is_refused(value: str) -> None:
    assert judge_author(value).rule == "address"


def test_a_contact_block_is_refused() -> None:
    value = "Velde Council, 11 Harbour Row, Velde. Telephone: 01234 567 890."
    assert judge_author(value).rule == "contact"


@pytest.mark.parametrize("value", ["jdoe", "first.last", "s141470"])
def test_a_login_name_is_refused(value: str) -> None:
    assert judge_author(value).rule == "login name"


def test_a_bracketed_email_and_an_account_suffix_are_trimmed_not_refused() -> None:
    assert judge_author("Velde Holdings (desk@velde.example)").value == "Velde Holdings"
    assert judge_author("Marlowe, Jan@AGENCY").value == "Marlowe, Jan"


def test_declared_names_are_joined_in_order_without_repeats_or_placeholders() -> None:
    names = ["Odum, Pell", "Castellan, Mira", "Odum, Pell", "Admin", " "]
    assert join_authors(names) == "Odum, Pell; Castellan, Mira"
    assert join_authors(["Admin", "user"]) is None


# --------------------------------------------------------------------------
# Publishers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["Velde Transit Authority", "example.org", "GOV.EXAMPLE", "Ministry of Ports / Docks"]
)
def test_publishers_are_kept(value: str) -> None:
    assert judge_publisher(value).value == value


@pytest.mark.parametrize(
    "value", ["WordPress", "Drupal", "Powered by Joomla!", "My WordPress Site"]
)
def test_the_sites_software_is_not_its_publisher(value: str) -> None:
    assert judge_publisher(value).rule == "platform"


def test_a_publisher_copying_the_generator_is_refused() -> None:
    assert judge_publisher("Pagecraft", generator="Pagecraft 9.2").rule == "platform"
    assert judge_publisher("Velde Council", generator="Pagecraft 9.2").value == "Velde Council"


@pytest.mark.parametrize("value", ["10.12.0.4", "172.16.5.9:8080", "example.orghttps:"])
def test_an_address_is_not_a_publisher(value: str) -> None:
    assert judge_publisher(value).rule == "address"


# --------------------------------------------------------------------------
# Provenance names
# --------------------------------------------------------------------------


def test_inferred_sources_are_not_declared() -> None:
    for source in ("inferred", "host", "trafilatura", "text", None):
        assert not is_declared(source)
    assert all(is_declared(s) for s in DECLARED)


def test_provenance_keys_follow_the_title_convention() -> None:
    """`extra['title_from']` and `['language_from']` predate this; the new keys match them."""
    assert FROM_KEY["title"] == "title_from"
    assert FROM_KEY["language"] == "language_from"
    assert all(key.endswith("_from") for key in FROM_KEY.values())
