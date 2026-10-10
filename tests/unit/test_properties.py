"""Properties of the pure functions search and synthesis rest on (`Q-03`).

Example tests pin cases somebody thought of; these state what must hold for any input, and
Hypothesis looks for the input that breaks it. Each property is one the code's own docstring
or the feature doc promises.
"""

from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_core.references import MIN_ENTRIES, is_reference_list
from meridian_core.search import RRF_K, cap_per_source, fuse

#: A ranked list: distinct ids, best first.
ranked = st.lists(st.integers(0, 60), unique=True, max_size=40)


# --------------------------------------------------------------------------
# Reciprocal rank fusion
# --------------------------------------------------------------------------


@given(st.lists(ranked, min_size=1, max_size=4))
def test_fusion_scores_exactly_the_ids_it_was_given(lists) -> None:
    scores = fuse(*lists)
    assert set(scores) == {i for ids in lists for i in ids}
    assert all(0 < s <= len(lists) / (RRF_K + 1) for s in scores.values())


@given(st.lists(ranked, min_size=1, max_size=4), st.randoms(use_true_random=False))
def test_fusion_does_not_depend_on_which_arm_came_first(lists, rnd: random.Random) -> None:
    shuffled = lists[:]
    rnd.shuffle(shuffled)
    a, b = fuse(*lists), fuse(*shuffled)
    assert a.keys() == b.keys()
    assert all(abs(a[i] - b[i]) < 1e-12 for i in a)


@given(st.lists(ranked.filter(bool), min_size=1, max_size=4), st.integers(1000, 2000))
def test_an_id_first_in_every_arm_scores_highest(lists, top: int) -> None:
    scores = fuse(*([top, *ids] for ids in lists))
    assert max(scores, key=lambda i: (scores[i], i == top)) == top


@given(ranked, ranked)
def test_another_arm_never_lowers_a_score(a, b) -> None:
    alone, both = fuse(a), fuse(a, b)
    assert all(both[i] >= alone[i] for i in alone)


# --------------------------------------------------------------------------
# The per-source cap
# --------------------------------------------------------------------------


@st.composite
def capped(draw):
    ordered = draw(st.lists(st.integers(0, 200), unique=True, max_size=60))
    sources = draw(st.integers(1, 6))
    source_of = {i: draw(st.integers(0, sources - 1)) for i in ordered}
    return ordered, source_of, draw(st.integers(1, 5)), draw(st.integers(1, 30))


@settings(max_examples=300)
@given(capped())
def test_the_cap_fills_the_page_from_what_it_was_given(case) -> None:
    ordered, source_of, cap, limit = case
    kept, displaced = cap_per_source(ordered, source_of, cap=cap, limit=limit)
    # A full page whenever there is enough; nothing invented, nothing twice.
    assert len(kept) == min(limit, len(ordered))
    assert len(set(kept)) == len(kept)
    assert set(kept) <= set(ordered)
    assert 0 <= displaced <= limit


@settings(max_examples=300)
@given(capped())
def test_no_source_passes_the_cap_while_others_wait(case) -> None:
    """Backfill takes held-back hits only when the page would otherwise be short."""
    ordered, source_of, cap, limit = case
    kept, _ = cap_per_source(ordered, source_of, cap=cap, limit=limit)
    counts: dict[int, int] = {}
    for i in kept:
        counts[source_of[i]] = counts.get(source_of[i], 0) + 1
    if any(n > cap for n in counts.values()):
        unused = [i for i in ordered if i not in kept]
        assert all(counts.get(source_of[i], 0) >= cap for i in unused)


@given(capped())
def test_a_cap_nothing_reaches_changes_nothing(case) -> None:
    ordered, source_of, _, limit = case
    kept, displaced = cap_per_source(ordered, source_of, cap=len(ordered) + 1, limit=limit)
    assert kept == ordered[:limit]
    assert displaced == 0


# --------------------------------------------------------------------------
# Reference lists
# --------------------------------------------------------------------------

line = st.one_of(
    st.sampled_from(
        [
            "Banister, D. (1997). Reducing the need to travel. Environment and Planning B.",
            "- 57",
            "Transportation Research Part A, 95, 49–63.",
            "https://doi.org/10.1016/j.tra.2017.01.004",
            "Trips fell by a third once the service moved closer to homes.",
            "The study measured three districts over two years.",
        ]
    ),
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\n"), max_size=60
    ),
)


@given(st.lists(line, max_size=12), st.randoms(use_true_random=False))
def test_the_verdict_does_not_depend_on_line_order_or_blank_lines(
    lines, rnd: random.Random
) -> None:
    """Counted in entries and characters, so neither order nor spacing can change it."""
    verdict = is_reference_list("\n".join(lines))
    shuffled = lines[:]
    rnd.shuffle(shuffled)
    assert is_reference_list("\n".join(shuffled)) == verdict
    assert is_reference_list("\n\n".join(lines) + "\n \n") == verdict


@given(st.lists(line, max_size=MIN_ENTRIES - 1))
def test_fewer_lines_than_the_minimum_is_never_a_list(lines) -> None:
    assert not is_reference_list("\n".join(lines))


# --------------------------------------------------------------------------
# The answer page's grouping by country
# --------------------------------------------------------------------------

COUNTRIES = ["DE", "FR", "JP", "SG"]


@st.composite
def answer_hits(draw):
    """Several passages for each of several sources, each source about zero to three countries."""
    from itertools import count

    from meridian_core.search import SearchHit

    ids = count(1)
    hits = []
    for source_id in range(1, draw(st.integers(1, 12)) + 1):
        places = draw(
            st.one_of(st.none(), st.lists(st.sampled_from(COUNTRIES), unique=True, max_size=3))
        )
        for _ in range(draw(st.integers(1, 3))):
            names = draw(st.lists(st.sampled_from(COUNTRIES), unique=True, max_size=2))
            hits.append(
                SearchHit(
                    chunk_id=next(ids),
                    source_id=source_id,
                    text=" ".join(names) or "nothing named",
                    page_or_offset=None,
                    chunk_index=0,
                    url=f"https://pub{source_id}.example/doc",
                    title=None,
                    source_tier="press",
                    publication_date=None,
                    language="en",
                    topic_labels=None,
                    passage_topics=None,
                    page_unit=None,
                    media_type=None,
                    duplicate_of=None,
                    score=draw(st.floats(0.001, 1)),
                    lexical_rank=1,
                    vector_rank=None,
                    places=places,
                )
            )
    return hits


def _named(text: str) -> set[str]:
    return {code for code in COUNTRIES if code in text.split()}


@settings(max_examples=200)
@given(answer_hits())
def test_without_names_a_source_counts_once_in_each_of_its_countries(hits) -> None:
    from meridian_core.answer import group_hits

    groups, rest = group_hits(hits, named=None)
    places = {h.source_id: h.places for h in hits}
    expected = sum(max(1, len(set(p or []))) for p in places.values())
    assert sum(g.sources for g in groups) + (rest.sources if rest else 0) == expected
    assert all(g.sources > 0 for g in groups)
    assert (rest is None) == all(places[s] for s in places)


@settings(max_examples=200)
@given(answer_hits())
def test_a_several_country_source_counts_only_where_a_passage_names_the_country(hits) -> None:
    """`B-168`: with names, a source about several countries is filed under a country only
    through a matching passage that names it; one naming none of them is unplaced."""
    from meridian_core.answer import group_hits

    groups, rest = group_hits(hits, named=_named)
    by_source: dict[int, list] = {}
    for h in hits:
        by_source.setdefault(h.source_id, []).append(h)
    for group in groups:
        for item in group.items:
            source_places = set(by_source[item.source_id][0].places or [])
            if len(source_places) > 1:
                assert group.code in _named(item.text), (group.code, item.text)
    several = sum(
        1
        for passages in by_source.values()
        if len(set(passages[0].places or [])) > 1
        and not any(set(passages[0].places) & _named(p.text) for p in passages)
    )
    assert (rest.several_places if rest else 0) == several


@given(answer_hits())
def test_strong_groups_come_first(hits) -> None:
    from meridian_core.answer import group_hits

    groups, _ = group_hits(hits, named=_named)
    strong = [g.coverage == "strong" for g in groups]
    assert strong == sorted(strong, reverse=True)
