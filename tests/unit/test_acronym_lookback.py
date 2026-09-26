"""The acronym harvest reads a bounded clause (`B-85`) and must agree with the
full read it replaced, which scanned from the start of the document for every
bracket and made long reports quadratic."""

from __future__ import annotations

import random
import time

import pytest

from meridian_core import gazetteer
from meridian_core.gazetteer import (
    _BOUNDARY,
    boundary_ends,
    clause_words,
    find_acronyms,
    tokenise,
)


def full_read(text: str, end: int, needed: int) -> list[str]:
    """The implementation before `B-85`, kept as the reference."""
    before = text[:end]
    boundary = 0
    for hit in _BOUNDARY.finditer(before):
        boundary = hit.end()
    return tokenise(before[boundary:])[-needed:]


PIECES = [
    "Land",
    "Transport",
    "Authority",
    "of",
    "the",
    "and",
    "for",
    "Urban",
    "Agency",
    "multi-agent",
    "2026",
    ",",
    ".",
    ";",
    ":",
    " - ",
    " — ",
    "\n\n",
    "\n",
    "   ",
    " " * 300,
    "(LTA)",
    "(ii)",
    "(URA)",
    "•",
    "Science,",
    "Technology",
    "Research",
]


def random_text(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(PIECES) for _ in range(words))


@pytest.mark.parametrize("seed", range(16))
def test_the_bounded_read_agrees_with_the_full_read(seed):
    rng = random.Random(seed)
    text = random_text(rng, rng.choice([50, 800, 3000]))
    brackets = [m.start() for m in gazetteer._CANDIDATE.finditer(text)]
    ends = rng.sample(brackets, min(len(brackets), 15)) + [len(text)]
    ends += [rng.randrange(len(text) + 1) for _ in range(10)]
    for end in ends:
        for needed in (6, 12, 20):
            expected = full_read(text, end, needed)
            assert clause_words(text, end, needed) == expected, (seed, end)
            # And with the boundaries found once for the whole document.
            assert clause_words(text, end, needed, boundary_ends(text)) == expected, (seed, end)


def test_a_boundary_straddling_the_window_edge_is_not_missed():
    """A blank line across the edge ends the clause; the window alone cannot see it."""
    head = "Old Clause Words " * 50
    for pad in range(0, 8):
        tail = " " * pad + "\n  \n" + "Few Words Here " * 2
        text = head + tail + " " * (gazetteer._LOOKBACK - len(tail) + 2) + "(FWH)"
        end = text.index("(FWH)")
        assert clause_words(text, end, 20, boundary_ends(text)) == full_read(text, end, 20)


def test_long_whitespace_falls_back_rather_than_guessing():
    text = "Land Transport Authority" + " " * 5000 + "(LTA)"
    end = text.index("(LTA)")
    assert clause_words(text, end, 20) == full_read(text, end, 20)
    assert find_acronyms(text)[0].expansion == "Land Transport Authority"


def test_a_long_report_is_no_longer_quadratic():
    """Thousands of brackets in one report: the old read took seconds, growing
    with the square of the length."""
    sentence = "The Land Transport Authority (LTA) said the Urban Agency (UA) and (ii) more; "
    text = sentence * 4000  # about 300k characters, 12k brackets
    started = time.perf_counter()
    found = find_acronyms(text)
    assert time.perf_counter() - started < 3.0
    assert {d.acronym for d in found} >= {"LTA", "UA"}
