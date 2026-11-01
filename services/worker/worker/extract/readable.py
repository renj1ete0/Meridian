r"""Whether a passage is text a reader could read, by a mechanical test (task `B-235`).

A PDF font with no Unicode map gives glyph numbers instead of letters, so "the and" arrives
as ``WKH\x03DQG``: printable letters a fixed distance from the right ones, with the space
glyph as a control character. Such a passage is not words to search or to embed. The test
counts characters no reader can read, by the words that hold them. See
docs/features/extraction.md#unreadable-passages.
"""

from __future__ import annotations

import unicodedata

#: More than this share of a passage's characters sit in words holding an unreadable
#: character, and the passage is not kept. Measured on a real corpus; see
#: docs/features/extraction.md#unreadable-passages before changing it.
UNREADABLE_WORD_SHARE = 0.5

#: Control characters read as a gap between words: layout whitespace, and BEL and BS, which
#: is how a word processor's bullets and tab leaders reach a PDF's text layer. ``str.isspace``
#: is not used: it counts U+001C–U+001F, where an unmapped font puts the digits.
_LAYOUT_CONTROLS = frozenset("\x07\x08\t\n\v\f\r\x85")

#: Categories of separator a reader sees as a gap between words.
_SEPARATORS = frozenset({"Zs", "Zl", "Zp"})


#: Every character :func:`unreadable_char` accepts, as one regular-expression class that
#: Python and Postgres read alike, so a pass over stored text can ask the database for
#: candidates and judge them here. NUL is left out: Postgres text cannot hold it.
UNREADABLE_CLASS = (
    "[\x01-\x06\x0e-\x1f\x7f-\x84\x86-\x9f\ue000-\uf8ff\ufffd"
    "\U000f0000-\U000ffffd\U00100000-\U0010fffd]"
)


def _is_gap(char: str) -> bool:
    return char in _LAYOUT_CONTROLS or unicodedata.category(char) in _SEPARATORS


def unreadable_char(char: str) -> bool:
    """A character no reader can read: a control, a private-use or surrogate code point, U+FFFD.

    A control read as a gap (tab, newline, a bullet's BEL) is not one.
    """
    if char in _LAYOUT_CONTROLS:
        return False
    return unicodedata.category(char) in ("Cc", "Co", "Cs") or char == "\ufffd"


def unreadable_share(text: str) -> float:
    """Share of the text's non-gap characters that sit in a word holding an unreadable one.

    By word rather than by character: an unmapped font's text is a control character every
    word or so, which is a fifth of the characters but all of the words. 0.0 for empty text.
    """
    total = 0
    unreadable = 0
    length = 0
    tainted = False
    for char in text + " ":
        if _is_gap(char):
            total += length
            if tainted:
                unreadable += length
            length = 0
            tainted = False
            continue
        length += 1
        tainted = tainted or unreadable_char(char)
    return unreadable / total if total else 0.0


def is_unreadable(text: str) -> bool:
    """Whether a passage is mostly words no reader could read (:data:`UNREADABLE_WORD_SHARE`)."""
    return unreadable_share(text) > UNREADABLE_WORD_SHARE
