"""The arithmetic under bridges (task P6-31)."""

from __future__ import annotations

import numpy as np

from meridian_core.bridges import NEIGHBOURS, _Node, anchor, best_pairs, shared_terms, sibling_pairs


def node(area_id: int, vec, parent: int | None = None, level: int = 2) -> _Node:
    v = np.asarray(vec, dtype=np.float64)
    return _Node(area_id, level, parent, v / np.linalg.norm(v), [])


def test_anchor_is_the_most_common_leaf_and_ties_go_low():
    assert anchor([5, 3, 5, 3, 9]) == 3
    assert anchor([7, 7, 2]) == 7
    assert anchor([]) is None


def test_two_siblings_pair_once_and_never_with_themselves():
    """The first live build wrote (x, x): a group smaller than the neighbour
    count reached its own row. The table's CHECK caught it."""
    pairs = sibling_pairs([node(1, [1, 0]), node(2, [0.9, 0.1])])
    assert pairs == {(1, 2)}


def test_only_siblings_pair_and_each_takes_its_nearest():
    areas = [
        node(1, [1, 0, 0, 0], parent=10),
        node(2, [0.9, 0.1, 0, 0], parent=10),
        node(3, [0, 1, 0, 0], parent=10),
        node(4, [0, 0, 1, 0], parent=10),
        node(5, [0, 0, 0, 1], parent=10),
        node(6, [0, 0, 0.9, 0.1], parent=11),  # a cousin, not a sibling
    ]
    pairs = sibling_pairs(areas)
    assert all(a < b for a, b in pairs)
    assert not any(6 in pair for pair in pairs)
    assert (1, 2) in pairs
    per_area = {i: sum(i in p for p in pairs) for i in range(1, 6)}
    assert all(n >= NEIGHBOURS for n in per_area.values())


def test_best_pairs_are_best_first_with_no_passage_reused():
    a = np.eye(3)
    b = np.array([[1.0, 0, 0], [0.99, 0.14, 0], [0, 0, 1.0]])
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    pairs = best_pairs([10, 11, 12], a, [20, 21, 22], b, k=3)
    assert [p["score"] for p in pairs] == sorted((p["score"] for p in pairs), reverse=True)
    assert len({p["chunk_a"] for p in pairs}) == len(pairs) == len({p["chunk_b"] for p in pairs})
    assert pairs[0] == {"chunk_a": 10, "chunk_b": 20, "score": 1.0}


def test_best_pairs_skip_a_passage_on_both_sides_and_empty_sides():
    same = np.eye(2)
    pairs = best_pairs([1, 2], same, [1, 3], same, k=2)
    assert all(p["chunk_a"] != p["chunk_b"] for p in pairs)
    assert best_pairs([], np.zeros((0, 2)), [1], same[:1], k=3) == []


def test_shared_terms_keep_the_first_areas_order():
    assert shared_terms(["heat", "shade", "bus"], ["bus", "heat"]) == ["heat", "bus"]
    assert shared_terms(["a"], []) == []
