"""Passages whose words another page already gave, recognised at query time (`B-236`).

A document fetched as both an HTML page and a PDF, or mirrored on another host, is cut into
passages at different boundaries, so its copies escape the novelty gate's vector threshold
and the document-copy rules, and then take one result slot each. :func:`fold_copies` keeps
the best-ranked passage and folds a later one into it when most of the later one's words are
already in passages kept from one other source. See
docs/features/search.md#copies-and-reference-lists.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

#: Words per shingle. Five is long enough that shared shingles mean shared sentences, not
#: shared phrases; short enough that a passage boundary costs only a few.
SHINGLE_WORDS = 5

#: The share of a passage's shingles that passages kept from one other source must already
#: hold. Measured on a real corpus; see docs/features/search.md#copies-and-reference-lists.
MIN_COVERAGE = 0.5

#: Passages with fewer shingles than this are never folded: a heading or a table row is
#: shared by unrelated documents.
MIN_SHINGLES = 12

_WORD = re.compile(r"\w+")


def shingles(text: str, n: int = SHINGLE_WORDS) -> frozenset[int]:
    """The hashed word ``n``-grams of ``text``, case and punctuation ignored."""
    words = _WORD.findall(text.lower())
    return frozenset(hash(tuple(words[i : i + n])) for i in range(len(words) - n + 1))


def fold_copies(
    ordered: Sequence[int],
    texts: Mapping[int, str],
    source_of: Mapping[int, int],
    *,
    limit: int,
) -> tuple[list[int], dict[int, int]]:
    """The first ``limit`` of ``ordered`` whose words another source has not already given.

    A passage is folded when at least :data:`MIN_COVERAGE` of its shingles appear in the
    passages kept so far from one other source, together: a copy cut at other boundaries
    straddles two of them. It is folded into the kept passage it shares most with. Passages
    of one source never fold into each other (the per-source cap is for that), and ids
    without text or source are kept as they are. Returns the kept ids and, per kept id that
    absorbed any, how many were folded into it.
    """
    kept: list[int] = []
    folded: dict[int, int] = {}
    # Shingle -> kept passages holding it, so a candidate is compared only with passages it
    # shares words with rather than with the whole page.
    postings: dict[int, list[int]] = {}

    for chunk_id in ordered:
        if len(kept) >= limit:
            break
        text = texts.get(chunk_id)
        source = source_of.get(chunk_id)
        grams = shingles(text) if text and source is not None else frozenset()
        if len(grams) < MIN_SHINGLES:
            kept.append(chunk_id)
            continue

        by_source: dict[int, int] = {}
        by_passage: dict[int, int] = {}
        for gram in grams:
            sources_here = set()
            for other in postings.get(gram, ()):
                other_source = source_of[other]
                if other_source == source:
                    continue
                by_passage[other] = by_passage.get(other, 0) + 1
                sources_here.add(other_source)
            for other_source in sources_here:
                by_source[other_source] = by_source.get(other_source, 0) + 1

        best = max(by_source, key=by_source.__getitem__, default=None)
        if best is not None and by_source[best] / len(grams) >= MIN_COVERAGE:
            original = max(
                (other for other in by_passage if source_of[other] == best),
                key=lambda other: (by_passage[other], -kept.index(other)),
            )
            folded[original] = folded.get(original, 0) + 1
            continue

        kept.append(chunk_id)
        for gram in grams:
            postings.setdefault(gram, []).append(chunk_id)

    return kept, folded


def set_back(ordered: Sequence[int], flagged: set[int]) -> list[int]:
    """``ordered`` with the ``flagged`` ids moved after every other, each part in its order."""
    return [i for i in ordered if i not in flagged] + [i for i in ordered if i in flagged]
