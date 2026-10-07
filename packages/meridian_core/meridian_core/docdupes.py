"""The same document fetched twice (task `B-44`).

Finds a source that is a copy of another (exact, near or translation rule) and points
the copy straight at its canonical source. A source anything cites is never marked.
See docs/features/duplicates.md for the rules and why mean vectors alone are not enough.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence

import numpy as np

#: Share of the later source's passages that must appear in the earlier one.
EXACT_SHARE = 0.9

#: A passage carried by more sources than this is template, not content; it is
#: left out of the exact comparison so one shared footer does not make every
#: page of a site a candidate for every other.
COMMON_PASSAGE = 50

#: Mean-vector cosine for the near rule. Measured: distinct same-template
#: documents reached 0.97; same-document pairs under different formats sat at
#: 0.99 and above.
NEAR_COSINE = 0.985

#: The shorter of the pair must be at least this share of the longer.
NEAR_LENGTH_RATIO = 0.6

_SPACE = re.compile(r"\s+")
_TITLE_NOISE = re.compile(r"[^\w]+", re.UNICODE)


def passage_hash(text: str) -> str:
    return hashlib.blake2b(
        _SPACE.sub(" ", text).strip().casefold().encode(), digest_size=12
    ).hexdigest()


def title_key(title: str | None) -> str | None:
    """A title for comparison: case, punctuation and spacing ignored."""
    if not title:
        return None
    key = _TITLE_NOISE.sub(" ", title).strip().casefold()
    return key if len(key) >= 8 else None


@dataclasses.dataclass(frozen=True)
class Pair:
    later: int
    earlier: int
    reason: str
    #: The share (exact) or cosine (near) that decided it.
    score: float


def exact_pairs(passages: Mapping[int, Sequence[str]]) -> list[Pair]:
    """Pairs whose later source's passages are (almost all) the earlier's."""
    hashes = {sid: {passage_hash(t) for t in texts if t.strip()} for sid, texts in passages.items()}
    carriers: dict[str, list[int]] = collections.defaultdict(list)
    for sid, hs in hashes.items():
        for h in hs:
            carriers[h].append(sid)
    out: list[Pair] = []
    for later in sorted(hashes):
        own = hashes[later]
        if not own:
            continue
        shared: collections.Counter[int] = collections.Counter()
        for h in own:
            holders = carriers[h]
            if len(holders) > COMMON_PASSAGE:
                continue
            for other in holders:
                if other < later:
                    shared[other] += 1
        if not shared:
            continue
        earlier, count = min(shared.items(), key=lambda kv: (-kv[1], kv[0]))
        share = count / len(own)
        if share >= EXACT_SHARE:
            out.append(Pair(later, earlier, "exact", round(share, 3)))
    return out


def near_pairs(
    titles: Mapping[int, str | None],
    vectors: Mapping[int, np.ndarray],
    lengths: Mapping[int, int],
) -> list[Pair]:
    """Pairs with the same title, near-identical meaning and comparable length."""
    groups: dict[str, list[int]] = collections.defaultdict(list)
    for sid, title in titles.items():
        key = title_key(title)
        if key and sid in vectors:
            groups[key].append(sid)
    out: list[Pair] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        unit = {sid: vectors[sid] / (np.linalg.norm(vectors[sid]) or 1.0) for sid in members}
        for i, later in enumerate(members):
            best: tuple[float, int] | None = None
            for earlier in members[:i]:
                cos = float(unit[later] @ unit[earlier])
                a, b = lengths.get(later, 0), lengths.get(earlier, 0)
                ratio = min(a, b) / max(a, b) if max(a, b) else 0.0
                close = cos >= NEAR_COSINE and ratio >= NEAR_LENGTH_RATIO
                if close and (best is None or cos > best[0]):
                    best = (cos, earlier)
            if best is not None:
                out.append(Pair(later, best[1], "near", round(best[0], 4)))
    return out


def translation_pairs(
    alternates: Mapping[int, str], urls: Mapping[int, Iterable[str]]
) -> list[Pair]:
    """A non-English source whose declared English version is in the corpus (`B-57`).

    ``alternates`` maps a source to the English URL it declares; ``urls`` maps
    each source to the addresses it is known by. The English source is canonical
    whatever the ids, so ``later``/``earlier`` mean copy and canonical, not fetch order.
    """
    by_url: dict[str, int] = {}
    for sid, addresses in urls.items():
        for address in addresses:
            if address:
                by_url.setdefault(address.split("#")[0].rstrip("/"), sid)
    out: list[Pair] = []
    for sid, english in alternates.items():
        target = by_url.get(english.split("#")[0].rstrip("/"))
        if target is not None and target != sid:
            out.append(Pair(sid, target, "translation", 1.0))
    return out


def canonical(pairs: Iterable[Pair], *, protected: set[int] = frozenset()) -> dict[int, Pair]:
    """One verdict per later source, pointing at the chain's root.

    Exact beats near for the same source. A protected (cited) source is never
    marked, and a chain through one stops there: its copies point at it.
    """
    order = {"exact": 0, "translation": 1, "near": 2}

    def rank(pair: Pair) -> tuple[int, int]:
        return (order.get(pair.reason, 3), pair.earlier)

    best: dict[int, Pair] = {}
    for pair in pairs:
        if pair.later in protected:
            continue
        current = best.get(pair.later)
        if current is None or rank(pair) < rank(current):
            best[pair.later] = pair
    resolved: dict[int, Pair] = {}
    for later, pair in best.items():
        path, root = [later], pair.earlier
        while root in best and root not in path:
            path.append(root)
            root = best[root].earlier
        if root in path:
            # A loop (`B-88`): its smallest id becomes the root and stays unmarked.
            # See docs/features/duplicates.md#loops.
            root = min(path[path.index(root) :])
            if root == later:
                continue
        resolved[later] = dataclasses.replace(pair, earlier=root)
    return resolved
