"""Whether a passage is text a reader could read (task `B-235`).

The unreadable samples are built the way an unmapped PDF font produces them: each character
moved down a fixed number of code points, so the space lands on a control character. The
readable ones are the near misses found on a real corpus: bullets and tab leaders written
as control characters, symbol-font glyphs in private use, a CJK passage.
"""

from __future__ import annotations

import re

import pytest

from worker.extract.readable import (
    UNREADABLE_CLASS,
    UNREADABLE_WORD_SHARE,
    is_unreadable,
    unreadable_char,
    unreadable_share,
)

PROSE = (
    "The authority published the results of the trial, and residents said the new "
    "crossings made the walk to the station shorter and safer than before."
)


def glyph_shift(text: str, offset: int = 29) -> str:
    """Text as a font with no Unicode map gives it: code points moved down by ``offset``."""
    return "".join(chr(ord(c) - offset) if ord(c) > offset else c for c in text)


def test_the_class_names_exactly_the_characters_the_test_counts() -> None:
    """Drift: the backfill asks Postgres for candidates with `UNREADABLE_CLASS` and judges
    them with `unreadable_char`. A character one accepts and the other does not is a passage
    the pass never sees, or one it fetches for nothing. NUL is the one exception: Postgres
    text cannot hold it."""
    pattern = re.compile(UNREADABLE_CLASS)
    disagree = [
        hex(cp)
        for cp in range(1, 0x110000)
        if not 0xD800 <= cp <= 0xDFFF
        and bool(pattern.fullmatch(chr(cp))) != unreadable_char(chr(cp))
    ]
    assert disagree == []


@pytest.mark.parametrize("offset", [29, 30])
def test_an_unmapped_font_is_unreadable(offset: int) -> None:
    """Glyph number = code point - 29 (or 30): the space becomes U+0003 (U+0002) and every
    word runs into the next. Both offsets were in the stored corpus."""
    garbled = glyph_shift(PROSE, offset)
    assert garbled.startswith("7KH" if offset == 29 else "6JG")
    assert unreadable_share(garbled) == 1.0
    assert is_unreadable(garbled)


def test_it_is_unreadable_with_real_spaces_between_the_words_too() -> None:
    """A variant on the corpus keeps a real space after each control: ``OHDG\\x03 DQG\\x03``.
    Every word still ends in a control, which a character count puts at a fifth of the text
    and under the extractor's page threshold."""
    garbled = " ".join(glyph_shift(w + " ") for w in PROSE.split())
    controls = sum(1 for c in garbled if unreadable_char(c)) / len(garbled.replace(" ", ""))
    assert controls < 0.2
    assert is_unreadable(garbled)


def test_digits_in_the_separator_range_are_not_taken_for_gaps() -> None:
    """`str.isspace` counts U+001C–U+001F as whitespace; an unmapped font puts 9, :, ;, <
    there, so a year would read as gaps rather than as one unreadable word."""
    year = glyph_shift("2019")
    assert any(c.isspace() for c in year)
    assert unreadable_share(year) == 1.0


@pytest.mark.parametrize(
    "text",
    [
        PROSE,
        # A bullet written as BEL, the commonest control in the corpus, spaced and glued.
        f"\x07 {PROSE}\n\x07{PROSE}\n\x07Introduction\n\x07Methods",
        # A table of contents with backspace as the tab leader.
        "Introduction\x085\nMethods\x0812\nThe results of the trial\x0820\nDiscussion\x0831",
        # Symbol-font glyphs in private use: a bullet and a less-than-or-equal sign.
        f"\uf0b7 {PROSE}\n\uf0b7 Emissions \uf0a3 0.024 g/km in the urban cycle.",
        # One replacement character in a long passage.
        PROSE.replace("station", "st\ufffdtion"),
        "在手术前，您需要禁食，以防止胃中有食物和液体所可能引发生的呕吐和窒息风险。",
        "",
    ],
)
def test_readable_text_with_stray_controls_is_kept(text: str) -> None:
    assert not is_unreadable(text)


def test_a_passage_half_garbled_is_kept_and_one_mostly_garbled_is_not() -> None:
    """The line is "more than half": a passage whose readable half is a real paragraph keeps
    it searchable; the threshold is strict, so exactly half is kept."""
    word = "abcd"
    half = " ".join([word] * 10 + [glyph_shift(word + " ")] * 10)
    assert unreadable_share(half) == pytest.approx(UNREADABLE_WORD_SHARE, abs=0.06)
    exact = " ".join(["abcd"] * 5 + ["abc\x03"] * 5)
    assert unreadable_share(exact) == UNREADABLE_WORD_SHARE
    assert not is_unreadable(exact)
    assert is_unreadable(" ".join(["abcd"] * 4 + ["abc\x03"] * 6))


def test_layout_whitespace_and_bullet_controls_are_never_unreadable() -> None:
    for char in "\x07\x08\t\n\v\f\r\x85 \u00a0\u2003\u2028":
        assert not unreadable_char(char)
    assert unreadable_share("\n\t  \r\n") == 0.0
