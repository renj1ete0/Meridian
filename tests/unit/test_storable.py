"""What Postgres cannot store is removed, and nothing else (task B-158)."""

from __future__ import annotations

from meridian_core.storable import storable


def test_nul_characters_go_wherever_they_are() -> None:
    value = {"a\x00": ["x\x00y", ("p\x00", 3)], "n": None, "f": 1.5}
    assert storable(value) == {"a": ["xy", ("p", 3)], "n": None, "f": 1.5}


def test_a_clean_string_is_returned_as_it_is() -> None:
    text = "Ridership rose."
    assert storable(text) is text
