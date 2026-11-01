"""Which edges contradict, decided without a database (task `P7-05`, §9).

`contradicts` is the rule the write path, merges and the sweep all apply. These tests
hold it to §9's two cases and to the cases it must not mark, and hold the table of
opposite relations to the shapes the rest of the system can match.
"""

from __future__ import annotations

import itertools

import pytest

from meridian_core import contested
from meridian_core.contested import OPPOSITE_RELATIONS, contradicts, opposed, opposites
from meridian_core.models import Edge
from meridian_core.models.graph import STANCE
from meridian_core.validation import ValidationError, check_relation_type


def edge(edge_id, relation, stance=None, ends=(1, 2)) -> Edge:
    return Edge(
        edge_id=edge_id,
        from_node=ends[0],
        to_node=ends[1],
        relation_type=relation,
        stance=stance,
    )


def test_only_the_opposing_stance_takes_the_other_side() -> None:
    """Drift: the side is read from the schema's stance vocabulary, so a stance added
    later is checked here without editing this test."""
    assert contested.AGAINST in STANCE.enums, "the opposing stance must be one the schema allows"
    sides = {stance: opposed(stance) for stance in [*STANCE.enums, None]}
    assert [s for s, against in sides.items() if against] == [contested.AGAINST]


@pytest.mark.parametrize("stance", [s for s in STANCE.enums if s != contested.AGAINST] + [None])
def test_one_claim_argued_for_and_against_contradicts(stance) -> None:
    """§9's first case: the same subject, relation and object, from opposite sides."""
    assert contradicts(edge(1, "reduces", stance), edge(2, "reduces", "opposes"))
    assert contradicts(edge(2, "reduces", "opposes"), edge(1, "reduces", stance))


def test_one_claim_on_the_same_side_does_not_contradict() -> None:
    assert not contradicts(edge(1, "reduces", "supports"), edge(2, "reduces", "mixed"))
    assert not contradicts(edge(1, "reduces", "opposes"), edge(2, "reduces", "opposes"))


@pytest.mark.parametrize("pair", OPPOSITE_RELATIONS)
def test_opposite_relations_argued_for_contradict(pair) -> None:
    a, b = pair
    assert contradicts(edge(1, a, "supports"), edge(2, b, None))
    assert contradicts(edge(2, b, None), edge(1, a, "supports"))


@pytest.mark.parametrize("pair", OPPOSITE_RELATIONS)
def test_a_denial_of_an_opposite_relation_agrees(pair) -> None:
    """ "Does not increase" sits with "reduces"; two denials can both be true."""
    a, b = pair
    assert not contradicts(edge(1, a, "opposes"), edge(2, b, "supports"))
    assert not contradicts(edge(1, a, "opposes"), edge(2, b, "opposes"))


@pytest.mark.parametrize(
    "other",
    [
        edge(2, "reduces", "opposes", ends=(2, 1)),  # the other direction
        edge(2, "reduces", "opposes", ends=(1, 3)),  # another object
        edge(2, "funds", "opposes"),  # another, unrelated claim
    ],
)
def test_edges_that_are_not_about_one_claim_do_not_contradict(other) -> None:
    assert not contradicts(edge(1, "reduces", "supports"), other)


def test_an_edge_does_not_contradict_itself() -> None:
    row = edge(1, "reduces", "supports")
    assert not contradicts(row, row)


def test_the_opposites_table_is_symmetric_and_never_pairs_a_relation_with_itself() -> None:
    relations = {r for pair in OPPOSITE_RELATIONS for r in pair}
    for a, b in itertools.permutations(relations, 2):
        assert (b in opposites(a)) == (a in opposites(b)), (a, b)
    assert all(r not in opposites(r) for r in relations)
    assert len(set(map(frozenset, OPPOSITE_RELATIONS))) == len(OPPOSITE_RELATIONS), "repeated"
    assert opposites("zz_unlisted") == frozenset()


@pytest.mark.parametrize("relation", sorted({r for pair in OPPOSITE_RELATIONS for r in pair}))
def test_every_listed_relation_is_one_the_write_path_accepts(relation) -> None:
    """A listed relation `add_edge` would refuse could never be matched: the table
    would silently detect nothing for it."""
    try:
        check_relation_type(relation)
    except ValidationError as exc:  # pragma: no cover - the failure being guarded
        pytest.fail(f"{relation!r} is refused by the write path: {exc}")
    assert relation == relation.strip().lower()
