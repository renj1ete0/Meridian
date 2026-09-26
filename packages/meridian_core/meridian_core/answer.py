"""A question answered as grouped evidence, with coverage stated per place.

A search returns a ranked list of passages, and a reader asking how places
compare has to do the grouping in their head: which country is this about, have
I seen it already, is this the third page from the same publisher. This module
does that grouping mechanically over one search's candidate pool, so the answer
page shows *where the evidence is*, *how much of it there is*, and *where it is
thin* — with nothing written by a model. No model is called, and nothing here
summarises: every item on the page is a passage a source actually contains.

**Grouping is by country.** A hit counts towards every country its source is
tagged with (`sources.places`), and a city rolls up into its country, so a
source about two cities in one country is one source for that country, not two.
Hits whose source carries no place land in ``unplaced``, which is split into
sources examined and found about no place, and sources never examined — two
different facts a reader should not have to guess between.

**One item per source.** A document's best-scoring passage stands for it; how
many of its passages matched rides along. A group of five items is five
documents, never one document five times.

**Coverage is a count, not a verdict on credibility** (design-system.md §4:
report structure, not verdicts). :data:`COVERAGE_RULE` is the sentence the
interface shows, and it is built from the same constants the rule uses, so the
words cannot drift from the arithmetic.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from .placenames import country_of, display_name
from .search import SearchHit
from .tiering import registrable_domain

#: How many distinct publishers make a group's coverage strong.
STRONG_MIN_PUBLISHERS = 3

#: At least one source in a strong group must be from one of these tiers. They
#: are the tiers whose material is primary (a regulator's own text) or reviewed
#: (a paper) rather than reported.
PRIMARY_TIERS: frozenset[str] = frozenset({"government", "peer_reviewed"})

#: Items shown per group, by default.
DEFAULT_TOP = 5
MAX_TOP = 20

#: How deep the answer's single search reaches. Deeper than a results page
#: (100), because grouping by place needs the long tail: the tenth country is
#: usually past the first hundred passages.
DEFAULT_ANSWER_CANDIDATES = 300

#: How much each tier weighs when choosing which items lead a group. A lean,
#: not a filter: a press item with a much better passage still outranks a
#: government one with a weak one. Unknown tiers weigh 1.
TIER_WEIGHT: Mapping[str, float] = {
    "government": 1.5,
    "peer_reviewed": 1.5,
    "institutional": 1.2,
    "press": 1.0,
    "informal": 0.8,
}

#: Each further item from a publisher already shown in the group is worth this
#: fraction of the one before, so independent publishers lead.
REPEAT_PUBLISHER_FACTOR = 0.5

#: The rule in words a reader can be shown. Built from the constants above.
COVERAGE_RULE = (
    f"Strong means at least {STRONG_MIN_PUBLISHERS} sources from different publishers, "
    "including at least one government or peer-reviewed source. Anything less is thin. "
    "A country with no matching source is not listed."
)

Coverage = Literal["strong", "thin"]


@dataclasses.dataclass(frozen=True)
class AnswerItem:
    """One source, represented by its best-matching passage."""

    source_id: int
    chunk_id: int
    title: str | None
    url: str
    #: The registrable domain, which is what "independent" is counted by.
    publisher: str
    source_tier: str
    publication_date: dt.date | None
    text: str
    score: float
    #: How many of this source's passages were in the pool.
    passages: int


@dataclasses.dataclass(frozen=True)
class AnswerGroup:
    """The evidence about one country, or the evidence about none."""

    #: ISO 3166-1 alpha-2, or None for the unplaced group.
    code: str | None
    name: str
    sources: int
    publishers: int
    #: Distinct sources per tier.
    tier_mix: dict[str, int]
    newest: dt.date | None
    coverage: Coverage
    items: list[AnswerItem]
    #: Unplaced only: how many of its sources were never examined for places.
    unexamined: int = 0


def coverage_of(tiers_by_publisher: Mapping[str, Iterable[str]]) -> Coverage:
    """The rule in :data:`COVERAGE_RULE`, over publishers and their tiers."""
    if len(tiers_by_publisher) < STRONG_MIN_PUBLISHERS:
        return "thin"
    has_primary = any(
        tier in PRIMARY_TIERS for tiers in tiers_by_publisher.values() for tier in tiers
    )
    return "strong" if has_primary else "thin"


def countries_of(places: Sequence[str] | None) -> list[str]:
    """The countries a source is about: cities folded into theirs, each once."""
    seen: dict[str, None] = {}
    for code in places or []:
        if code:
            seen.setdefault(country_of(code).upper(), None)
    return list(seen)


def _best_per_source(hits: Iterable[SearchHit]) -> tuple[dict[int, SearchHit], dict[int, int]]:
    best: dict[int, SearchHit] = {}
    counts: dict[int, int] = {}
    for hit in hits:
        counts[hit.source_id] = counts.get(hit.source_id, 0) + 1
        held = best.get(hit.source_id)
        if held is None or (hit.score, -hit.chunk_id) > (held.score, -held.chunk_id):
            best[hit.source_id] = hit
    return best, counts


def _item(hit: SearchHit, passages: int) -> AnswerItem:
    return AnswerItem(
        source_id=hit.source_id,
        chunk_id=hit.chunk_id,
        title=hit.title,
        url=hit.url,
        publisher=registrable_domain(hit.url),
        source_tier=hit.source_tier,
        publication_date=hit.publication_date,
        text=hit.text,
        score=hit.score,
        passages=passages,
    )


def lead_items(items: Sequence[AnswerItem], top: int) -> list[AnswerItem]:
    """Choose ``top`` items, favouring primary tiers and unseen publishers.

    Greedy: at each step the item with the best weighted score wins, where the
    weight is its tier's and a discount for every item already chosen from the
    same publisher. Ties go to the lower source id, so the order is stable.
    """
    chosen: list[AnswerItem] = []
    remaining = list(items)
    shown: dict[str, int] = {}
    while remaining and len(chosen) < top:

        def strength(item: AnswerItem) -> tuple[float, int]:
            weight = TIER_WEIGHT.get(item.source_tier, 1.0)
            repeat = REPEAT_PUBLISHER_FACTOR ** shown.get(item.publisher, 0)
            return (item.score * weight * repeat, -item.source_id)

        pick = max(remaining, key=strength)
        remaining.remove(pick)
        chosen.append(pick)
        shown[pick.publisher] = shown.get(pick.publisher, 0) + 1
    return chosen


def _group(
    code: str | None,
    name: str,
    source_ids: Iterable[int],
    best: Mapping[int, SearchHit],
    counts: Mapping[int, int],
    top: int,
    unexamined: int = 0,
) -> AnswerGroup:
    items = [_item(best[sid], counts[sid]) for sid in source_ids]
    tiers_by_publisher: dict[str, set[str]] = {}
    tier_mix: dict[str, int] = {}
    for item in items:
        tiers_by_publisher.setdefault(item.publisher, set()).add(item.source_tier)
        tier_mix[item.source_tier] = tier_mix.get(item.source_tier, 0) + 1
    dates = [item.publication_date for item in items if item.publication_date is not None]
    return AnswerGroup(
        code=code,
        name=name,
        sources=len(items),
        publishers=len(tiers_by_publisher),
        tier_mix=dict(sorted(tier_mix.items())),
        newest=max(dates) if dates else None,
        coverage=coverage_of(tiers_by_publisher),
        items=lead_items(items, top),
        unexamined=unexamined,
    )


def leading_topics(hits: Iterable[SearchHit], n: int = 3) -> list[str]:
    """The topics most of the pool's sources carry, most first.

    What a "find more" search is filed under when the reader chose no topic:
    a seed with no topic belongs to whatever the matcher makes of it.
    """
    counts: dict[str, int] = {}
    seen: set[int] = set()
    for hit in hits:
        if hit.source_id in seen:
            continue
        seen.add(hit.source_id)
        for topic in hit.topic_labels or []:
            counts[topic] = counts.get(topic, 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t))[:n]


def group_hits(
    hits: Sequence[SearchHit], *, top: int = DEFAULT_TOP
) -> tuple[list[AnswerGroup], AnswerGroup | None]:
    """Group one search's hits by country; return the groups and the unplaced.

    Groups are ordered strong first, then by independent publishers, then by
    sources, then by name — the order a reader scanning for where the evidence
    is would want. ``unplaced`` is None when every hit's source has a place.
    """
    best, counts = _best_per_source(hits)

    by_country: dict[str, list[int]] = {}
    unplaced: list[int] = []
    unexamined = 0
    # Walk sources in best-score order, so each group's source list starts in
    # search order before `lead_items` re-weighs it.
    for sid in sorted(best, key=lambda s: (-best[s].score, s)):
        countries = countries_of(best[sid].places)
        if not countries:
            unplaced.append(sid)
            if best[sid].places is None:
                unexamined += 1
            continue
        for country in countries:
            by_country.setdefault(country, []).append(sid)

    groups = [
        _group(code, display_name(code), sids, best, counts, top)
        for code, sids in by_country.items()
    ]
    groups.sort(key=lambda g: (g.coverage != "strong", -g.publishers, -g.sources, g.name))

    rest = (
        _group(None, "No place named", unplaced, best, counts, top, unexamined=unexamined)
        if unplaced
        else None
    )
    return groups, rest
