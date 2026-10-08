"""Whether a passage is a reference list rather than something a document says (B-171).

A passage counts as one when most of its text is entry lines: a citation as the place
tagger reads one, an author-year entry, a volume-and-pages tail, or a bare reference
number. See docs/features/synthesis.md#reference-lists.
"""

from __future__ import annotations

import re

from .places import is_citation

#: "Banister, D. (1997)." and "ParkinJ.ClarkB. (2018)": a surname with initials, then a
#: year in brackets on the same line.
AUTHOR_YEAR = re.compile(
    r"^[-*•\s\d.\[\]]*[A-Z][\w'’-]+(?:,\s*(?:[A-Z]\.\s?)+|[A-Z]\.(?:[A-Z]\.)?)"
    r".{0,400}?\((?:19|20)\d\d[a-z]?\)"
)

#: "- 57": a reference whose entry the converter dropped, leaving its number.
NUMBER_ONLY = re.compile(r"^[-*•\s]*\d{1,3}\.?\s*$")

#: ", 24, 284–303." ending a line: the volume and pages of an entry wrapped from above.
VOLUME_PAGES = re.compile(r"\b\d{1,4}(?:\(\d+\))?[,:]\s*(?:e?\d+[–-]\d+|e\d+)\.?\s*$")

#: The share of a passage's characters that must be entry lines. Measured: between 0.5 and
#: 0.6 sit review passages that cite as they argue, which carry claims.
MIN_SHARE = 0.6

#: A passage needs at least this many entry lines, so one long citation is not a list.
MIN_ENTRIES = 3


def is_entry(line: str) -> bool:
    """Whether one line reads as part of a reference list."""
    return bool(
        is_citation(line)
        or AUTHOR_YEAR.search(line)
        or NUMBER_ONLY.match(line)
        or VOLUME_PAGES.search(line)
    )


def is_reference_list(text: str) -> bool:
    """Whether most of ``text`` is reference-list entries."""
    lines = [line for line in text.split("\n") if line.strip()]
    total = sum(len(line) for line in lines)
    if total == 0:
        return False
    entries = [line for line in lines if is_entry(line)]
    return len(entries) >= MIN_ENTRIES and sum(map(len, entries)) / total >= MIN_SHARE
