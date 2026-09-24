"""Diversity seeds read off the graph (task P5-05, spec §7.4 mechanisms 2 and 4)."""

from __future__ import annotations

import dataclasses

import pytest

from meridian_core import diversity as d
from meridian_core.models.queue import SEED_MECHANISMS
from meridian_core.searchseeds import (
    MECHANISM_BY_KIND,
    WIDENING_KINDS,
    TopicSeedInput,
    candidates,
)


def node(i: int, tiers: dict[str, int] | None = None, **kw) -> d.NodeEvidence:
    kw.setdefault("name", f"subject number {i}")
    kw.setdefault("node_type", "concept")
    kw.setdefault("topic", "sometopic")
    kw.setdefault("degree", 5)
    return d.NodeEvidence(entity_id=i, tiers=tiers or {}, **kw)


GOV_ONLY = {"government": 4}


# --- drift ------------------------------------------------------------------


def test_every_mechanism_written_is_one_the_column_accepts() -> None:
    """Both writers' mechanism names must pass the CHECK on `queue.seed_mechanism`."""
    written = set(MECHANISM_BY_KIND.values()) | {"tier_imbalance", "distant_walk", "counter_seed"}
    assert written <= set(SEED_MECHANISMS)
    # And every mechanism the column names has a writer (§7.4 lists five).
    assert set(SEED_MECHANISMS) == written


def test_every_topic_shape_is_classified_as_a_mechanism_or_as_widening() -> None:
    shapes = {
        q.kind
        for q in candidates(
            TopicSeedInput(
                "some-topic",
                "a topic described in plain words here",
                ("aaaa", "bbbb"),
                (("de", "wort"),),
            )
        )
    }
    assert shapes <= set(MECHANISM_BY_KIND) | WIDENING_KINDS
    assert not set(MECHANISM_BY_KIND) & WIDENING_KINDS


def test_target_tiers_are_real_tiers_and_informal_is_not_one() -> None:
    assert set(d.TARGET_TIERS) <= set(d.TIERS)
    assert "informal" not in d.TARGET_TIERS
    assert all(all("{}" in p for p in ps) for ps in d.TIER_PHRASINGS.values())


# --- mechanism 2: tier imbalance ---------------------------------------------


def test_a_single_tier_node_is_asked_for_every_missing_target_tier() -> None:
    gap = d.tier_gap(node(1, GOV_ONLY))
    assert gap is not None and gap.dominant == "government"
    assert set(gap.missing) == set(d.TARGET_TIERS) - {"government"}


@pytest.mark.parametrize("tiers", [{"government": 1}, {"government": 2}, {}])
def test_a_node_with_too_little_evidence_gets_nothing(tiers) -> None:
    n = node(1, tiers)
    assert d.tier_gap(n) is None
    assert d.plan([n], already=[], seed=1, walks=0) == []


@pytest.mark.parametrize(
    "tiers",
    [
        {"government": 2, "press": 1},  # 2/3, below the dominant share
        {"government": 3, "peer_reviewed": 1},  # 3/4
        {"government": 1, "press": 1, "peer_reviewed": 1, "institutional": 1},
    ],
)
def test_a_balanced_node_gets_nothing(tiers) -> None:
    n = node(1, tiers)
    assert d.tier_gap(n) is None
    assert d.plan([n], already=[], seed=1, walks=0) == []


def test_four_of_five_is_overwhelming_and_only_absent_tiers_are_targeted() -> None:
    gap = d.tier_gap(node(1, {"government": 4, "press": 1}))
    assert gap is not None
    assert "press" not in gap.missing and "government" not in gap.missing
    assert set(gap.missing) == {"peer_reviewed", "institutional"}


def test_a_node_backed_only_by_informal_sources_is_asked_for_strong_tiers() -> None:
    gap = d.tier_gap(node(1, {"informal": 5}))
    assert gap is not None and set(gap.missing) == set(d.TARGET_TIERS)


def test_queries_use_the_nodes_name_and_aim_at_the_missing_tier() -> None:
    n = node(1, GOV_ONLY, name="sample subject")
    got = d.plan([n], already=[], seed=1, walks=0)
    assert got and all("sample subject" in q.text for q in got)
    assert {q.mechanism for q in got} == {"tier_imbalance"}
    assert all(q.topic == "sometopic" and q.entity_id == 1 for q in got)
    assert all("government" in q.reason and "4 sources" in q.reason for q in got)


def test_aliases_are_used_once_the_canonical_phrasings_are_spent() -> None:
    n = node(1, {"government": 3}, name="first name", aliases=("second name", "abc"))
    spent = [p.format("first name") for ps in d.TIER_PHRASINGS.values() for p in ps]
    got = d.plan([n], already=spent, seed=1, walks=0)
    assert got and all("second name" in q.text for q in got)
    # "abc" is too short to search for.
    assert not any("abc" in q.text for q in got)


def test_per_node_and_per_run_caps_hold() -> None:
    nodes = [node(i, GOV_ONLY) for i in range(20)]
    got = d.plan(nodes, already=[], seed=1, tier_cap=5, per_node=2, walks=0)
    assert len(got) == 5
    per_node = {q.entity_id for q in got}
    assert all(sum(q.entity_id == i for q in got) <= 2 for i in per_node)


def test_no_query_repeats_what_is_already_queued_or_itself() -> None:
    nodes = [node(i, GOV_ONLY, name="the same name") for i in range(5)]
    first = d.plan(nodes, already=[], seed=1, tier_cap=50, per_node=4, walks=0)
    texts = [q.text.lower() for q in first]
    assert len(texts) == len(set(texts))
    second = d.plan(nodes, already=[q.text.upper() for q in first], seed=1, tier_cap=50, walks=0)
    assert not {q.text.lower() for q in second} & set(texts)


def test_once_every_phrasing_is_spent_a_node_stops_producing() -> None:
    n = node(1, GOV_ONLY)
    already: list[str] = []
    for _ in range(20):
        got = d.plan([n], already=already, seed=1, walks=0)
        already += [q.text for q in got]
    assert d.plan([n], already=already, seed=1, walks=0) == []
    assert len(already) == 3 * 2  # three missing tiers, two phrasings, one name


def test_nodes_with_more_evidence_are_served_first() -> None:
    small = node(1, {"government": 3})
    big = node(2, {"government": 9})
    got = d.plan([small, big], already=[], seed=7, tier_cap=2, per_node=2, walks=0)
    assert {q.entity_id for q in got} == {2}


@pytest.mark.parametrize(
    "change",
    [
        {"topic": None},
        {"node_type": "place"},
        {"node_type": "source"},
        {"name": "abc"},
        {"name": "a name that runs on for far too many words to be a query"},
    ],
)
def test_a_node_that_cannot_be_searched_gets_nothing(change) -> None:
    n = dataclasses.replace(node(1, {"government": 9}, degree=0, distance=1.0), **change)
    assert d.plan([n], already=[], seed=1) == []


def test_bad_caps_are_refused() -> None:
    with pytest.raises(ValueError):
        d.plan([], already=[], seed=1, per_node=0)
    with pytest.raises(ValueError):
        d.plan([], already=[], seed=1, walks=-1)


# --- mechanism 1: the hook ---------------------------------------------------


def test_no_stance_means_no_node_level_counter_seed() -> None:
    """Stance is a model's output (§8) that nothing extracts yet: unknown is not one-sided."""
    n = node(1, {"government": 1, "press": 1, "peer_reviewed": 1})
    assert n.stances is None
    assert d.stance_counter(n) == []


def test_the_hook_fires_only_when_every_sided_source_agrees() -> None:
    one_sided = node(1, {"press": 1}, stances={"supports": 3, "neutral": 2})
    two_sided = node(2, {"press": 1}, stances={"supports": 3, "opposes": 1})
    thin = node(3, {"press": 1}, stances={"supports": 2})
    assert {q.mechanism for q in d.stance_counter(one_sided)} == {"counter_seed"}
    assert d.stance_counter(two_sided) == []
    assert d.stance_counter(thin) == []


# --- mechanism 4: distant walks ----------------------------------------------


def test_far_is_unconnected_or_in_the_most_distant_quarter() -> None:
    nodes = [node(i, degree=5, distance=i / 10) for i in range(8)]  # 0.0 .. 0.7
    nodes.append(node(99, degree=0, distance=0.0))
    far = {n.entity_id: why for n, why in d.far_nodes(nodes)}
    # A quarter of nine, rounded up, is three: 0.7, 0.6 and 0.5.
    assert set(far) == {7, 6, 5, 99}
    assert "0 edges" in far[99] and "distance" in far[7]


def test_without_vectors_only_unconnected_nodes_are_far() -> None:
    nodes = [node(1, degree=0), node(2, degree=1)]
    assert [n.entity_id for n, _ in d.far_nodes(nodes)] == [1]


def test_walks_are_capped_and_deterministic_by_seed() -> None:
    nodes = [node(i, degree=0) for i in range(30)]
    a = d.plan(nodes, already=[], seed=42, walks=3)
    b = d.plan(nodes, already=[], seed=42, walks=3)
    assert a == b and len(a) == 3
    assert {q.mechanism for q in a} == {"distant_walk"}
    others = [d.plan(nodes, already=[], seed=s, walks=3) for s in range(1, 6)]
    assert any(o != a for o in others)


def test_a_walk_never_starts_from_a_central_node() -> None:
    central = [node(i, degree=10) for i in range(10)]
    assert d.plan(central, already=[], seed=1) == []


def test_a_walked_node_moves_on_to_its_topic_phrasing_then_stops() -> None:
    n = node(1, degree=0, name="lonely subject")
    first = d.plan([n], already=[], seed=1)
    second = d.plan([n], already=[q.text for q in first], seed=1)
    third = d.plan([n], already=[q.text for q in first + second], seed=1)
    assert [q.text for q in first] == ["lonely subject"]
    assert [q.text for q in second] == ["lonely subject sometopic"]
    assert third == []


def test_a_node_counter_seeded_this_run_is_not_also_walked() -> None:
    n = node(1, GOV_ONLY, degree=0)
    got = d.plan([n], already=[], seed=1)
    assert {q.mechanism for q in got} == {"tier_imbalance"}


# --- filing a node under a topic ---------------------------------------------


def test_a_nodes_own_active_label_wins() -> None:
    assert d.file_under(["b", "a", "gone"], ["c", "c", "c"], ["a", "b", "c"]) == "a"


def test_otherwise_the_topic_most_of_its_sources_carry() -> None:
    assert d.file_under(None, ["b", "c", "c", "gone", "gone", "gone"], ["b", "c"]) == "c"
    assert d.file_under([], ["c", "b"], ["b", "c"]) == "b"  # a tie goes alphabetically


def test_no_active_topic_means_no_topic() -> None:
    assert d.file_under(["gone"], ["gone"], ["a"]) is None


def test_summary_counts_what_the_report_prints() -> None:
    nodes = [
        node(1, GOV_ONLY),
        node(2, {"government": 1}),
        node(3, {"government": 2, "press": 2}),
        node(4, GOV_ONLY, topic=None),
        node(5, degree=0),
    ]
    s = d.summarise(nodes)
    assert (s.nodes, s.eligible, s.no_topic) == (5, 4, 1)
    assert (s.enough_evidence, s.imbalanced, s.far) == (2, 1, 1)
