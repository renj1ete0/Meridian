"""The neighbourhood's pure rules (task P6-33): anchor by name, rings kept apart."""

from __future__ import annotations

from meridian_core.neighbourhood import (
    SIMILAR_FLOOR,
    Cited,
    NameCandidate,
    Similar,
    choose_anchor,
    normalise_name,
    split_rings,
)


def test_normalise_sets_aside_case_punctuation_and_plural() -> None:
    assert normalise_name("Autonomous  Vehicles") == normalise_name("autonomous vehicle")
    assert normalise_name("demand-responsive transport") == "demand responsive transport"
    assert normalise_name("tree canopies") == normalise_name("tree canopy")
    assert normalise_name("electric buses") == normalise_name("electric bus")


def test_normalise_keeps_short_acronyms_and_double_s() -> None:
    # Stripping these would equate unrelated words.
    assert normalise_name("ODS") != normalise_name("OD")
    assert normalise_name("access") == "access"


def test_anchor_requires_a_name_not_a_substring() -> None:
    nodes = [NameCandidate(1, "bus rapid transit"), NameCandidate(2, "electric buses")]
    assert choose_anchor("bus", nodes) is None
    assert choose_anchor("Electric bus", nodes).entity_id == 2


def test_anchor_prefers_canonical_name_over_alias() -> None:
    nodes = [NameCandidate(1, "something else", ("ODD",)), NameCandidate(9, "odd")]
    assert choose_anchor("ODD", nodes).entity_id == 9


def test_anchor_by_alias_when_no_name_matches() -> None:
    nodes = [NameCandidate(5, "operational design domain", ("ODD",))]
    assert choose_anchor("odd", nodes).entity_id == 5


def test_empty_term_names_nothing() -> None:
    assert choose_anchor("  --  ", [NameCandidate(1, "x")]) is None


def test_a_cited_entity_is_never_also_similar() -> None:
    rings = split_rings(
        1,
        [Cited(2, 3)],
        [Similar(2, 0.95), Similar(3, 0.80)],
    )
    assert [c.entity_id for c in rings.cited] == [2]
    assert [s.entity_id for s in rings.similar] == [3]


def test_the_anchor_is_in_neither_ring() -> None:
    rings = split_rings(1, [Cited(1, 9), Cited(2, 1)], [Similar(1, 1.0)])
    assert [c.entity_id for c in rings.cited] == [2]
    assert rings.similar == []


def test_similar_below_the_floor_is_dropped_not_shown() -> None:
    rings = split_rings(None, [], [Similar(4, SIMILAR_FLOOR - 0.01), Similar(5, SIMILAR_FLOOR)])
    assert [s.entity_id for s in rings.similar] == [5]
    assert rings.similar_total == 1


def test_rings_rank_and_cap_but_report_totals() -> None:
    cited = [Cited(i, support=i % 3) for i in range(2, 20)]
    rings = split_rings(1, cited, [], max_cited=4)
    assert len(rings.cited) == 4
    assert rings.cited_total == 18
    supports = [c.support for c in rings.cited]
    assert supports == sorted(supports, reverse=True)


def test_sparse_claims_leave_the_outer_ring_intact() -> None:
    # A graph with no stated links still answers with what reads alike.
    rings = split_rings(1, [], [Similar(2, 0.9), Similar(3, 0.75)])
    assert rings.cited == [] and rings.cited_total == 0
    assert [s.entity_id for s in rings.similar] == [2, 3]
