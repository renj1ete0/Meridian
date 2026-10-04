"""Times as people read them (task `B-145`, ADR 0009)."""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest
import yaml

from meridian_core import timefmt
from meridian_core.policy import NOT_FETCH_SETTINGS
from meridian_core.schemas.settings import DisplayZoneEdit

# 02:30 on the 5th in Singapore; still the 4th in New York.
LATE = dt.datetime(2026, 10, 4, 18, 30, tzinfo=dt.UTC)
SEED = pathlib.Path(__file__).resolve().parents[2] / "config/fetch_policy.yaml"


def test_the_default_is_the_seeded_zone() -> None:
    """A fresh install (the seed) and an unset row (the code) must agree."""
    assert yaml.safe_load(SEED.read_text())[timefmt.ZONE_KEY] == timefmt.DEFAULT_ZONE


def test_the_setting_is_not_mistaken_for_a_fetch_setting() -> None:
    assert timefmt.ZONE_KEY in NOT_FETCH_SETTINGS


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        ("Asia/Singapore", "2026-10-05 02:30 GMT+8"),
        ("UTC", "2026-10-04 18:30 GMT"),
        ("Asia/Kathmandu", "2026-10-05 00:15 GMT+5:45"),
        ("America/New_York", "2026-10-04 14:30 GMT-4"),
    ],
)
def test_an_instant_reads_in_the_zone_and_says_which(zone, expected) -> None:
    assert timefmt.format_instant(LATE, zone) == expected


def test_a_date_is_the_calendar_day_in_the_zone() -> None:
    assert timefmt.format_instant(LATE, "Asia/Singapore", date_only=True) == "2026-10-05"
    assert timefmt.format_instant(LATE, "America/New_York", date_only=True) == "2026-10-04"


def test_summer_time_moves_the_label() -> None:
    winter = dt.datetime(2026, 1, 15, 12, tzinfo=dt.UTC)
    assert timefmt.zone_label("America/New_York", winter) == "GMT-5"
    assert timefmt.zone_label("America/New_York", LATE) == "GMT-4"


def test_a_naive_datetime_is_refused() -> None:
    """An instant without an offset is the bug this module keeps out."""
    with pytest.raises(ValueError, match="naive"):
        timefmt.format_instant(dt.datetime(2026, 10, 4, 18, 30), "Asia/Singapore")


@pytest.mark.parametrize("raw", [None, "", "   ", 8, "Mars/Olympus_Mons", "GMT+8"])
def test_an_unusable_stored_value_falls_back_to_the_default(raw) -> None:
    assert timefmt.parse_zone(raw) == timefmt.DEFAULT_ZONE


def test_a_stored_zone_is_used_as_written() -> None:
    assert timefmt.parse_zone(" Europe/London ") == "Europe/London"


@pytest.mark.parametrize("bad", ["Mars/Olympus_Mons", "GMT+8", "../etc/passwd", ""])
def test_an_edit_naming_no_real_zone_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        DisplayZoneEdit(display_timezone=bad)


def test_an_edit_refuses_fields_it_does_not_know() -> None:
    with pytest.raises(ValueError):
        DisplayZoneEdit(display_timezone="Asia/Singapore", store_as="local")
