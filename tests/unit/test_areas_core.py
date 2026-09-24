"""The arithmetic under areas (task P6-30): clustering, grouping, naming."""

from __future__ import annotations

import numpy as np
import pytest

from meridian_core.areas import distinctive_terms, kmeans, nest, readable, tokens


def _blobs(
    seed: int = 1, per: int = 40, dims: int = 32, groups: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centres = rng.standard_normal((groups, dims)) * 5
    vectors = np.concatenate([c + rng.standard_normal((per, dims)) * 0.3 for c in centres])
    truth = np.repeat(np.arange(groups), per)
    return vectors, truth


def _same_partition(a: np.ndarray, b: np.ndarray) -> bool:
    pairs = set(zip(a.tolist(), b.tolist(), strict=True))
    return len(pairs) == len(set(a.tolist())) == len(set(b.tolist()))


def test_kmeans_recovers_separated_groups():
    vectors, truth = _blobs()
    labels, centroids = kmeans(vectors, 4)
    assert _same_partition(labels, truth)
    assert np.allclose(np.linalg.norm(centroids, axis=1), 1.0, atol=1e-5)


def test_kmeans_is_deterministic():
    vectors, _ = _blobs()
    first, _ = kmeans(vectors, 5)
    second, _ = kmeans(vectors, 5)
    assert np.array_equal(first, second)


def test_kmeans_leaves_no_cluster_empty_and_clamps_k():
    vectors, _ = _blobs(per=3, groups=2)
    labels, centroids = kmeans(vectors, 50)
    assert len(centroids) == len(vectors)
    assert set(labels.tolist()) == set(range(len(vectors)))


@pytest.mark.parametrize("bad", [np.zeros((0, 4)), np.zeros(4)])
def test_kmeans_refuses_unusable_input(bad):
    with pytest.raises(ValueError):
        kmeans(bad, 2)


def test_kmeans_refuses_k_below_one():
    with pytest.raises(ValueError):
        kmeans(np.ones((3, 2)), 0)


def test_nest_gives_three_levels_that_nest_and_follow_the_groups():
    vectors, truth = _blobs(groups=3, per=60)
    leaf_of, centroids, area_of_leaf, region_of_area = nest(vectors, 3, 6, 12)
    assert len(centroids) == len(area_of_leaf) and area_of_leaf.max() < len(region_of_area)
    region_of = region_of_area[area_of_leaf[leaf_of]]
    assert _same_partition(region_of, truth)
    # Numbered consecutively, every one used.
    assert set(leaf_of.tolist()) == set(range(len(centroids)))
    assert set(area_of_leaf.tolist()) == set(range(len(region_of_area)))
    assert set(region_of_area.tolist()) == {0, 1, 2}


def test_nest_does_not_let_one_region_swallow_the_corpus():
    """What centroid linkage did on a real corpus: one dense mass took 97%."""
    rng = np.random.default_rng(3)
    dense = rng.standard_normal((1, 32)) * 5 + rng.standard_normal((400, 32)) * 1.0
    far = rng.standard_normal((1, 32)) * 5 + rng.standard_normal((40, 32)) * 0.3
    leaf_of, _, area_of_leaf, region_of_area = nest(np.concatenate([dense, far]), 4, 8, 20)
    sizes = np.bincount(region_of_area[area_of_leaf[leaf_of]])
    assert sizes.max() < 0.8 * sizes.sum()


def test_nest_is_deterministic():
    vectors, _ = _blobs()
    first = nest(vectors, 2, 4, 8)
    second = nest(vectors, 2, 4, 8)
    assert all(np.array_equal(a, b) for a, b in zip(first, second, strict=True))


def test_nest_refuses_unusable_input():
    with pytest.raises(ValueError):
        nest(np.zeros((0, 4)), 1, 1, 1)
    with pytest.raises(ValueError):
        nest(np.ones((5, 4)), 1, 0, 2)


def test_addresses_are_not_words():
    text = (
        "See [the rule](https://law.example.edu/rule/7) at example.gov/x and arxiv.org/abs/1 today"
    )
    assert "edu" not in tokens(text) and "gov" not in tokens(text)
    assert "rule" in tokens(text) and "today" in tokens(text)
    assert "http" not in readable(text)


def test_a_cluster_with_one_source_still_gets_a_name():
    names = distinctive_terms(
        [["canopy shade", "canopy shade"], ["other"]], sources_by_cluster=[[1, 1], [2]]
    )
    assert names[0]


def test_tokens_drop_stopwords_numbers_and_short_words():
    assert tokens("The 2024 study of bus shelters, at 3pm, in the city") == [
        "study",
        "bus",
        "shelters",
        "city",
    ]


def test_distinctive_terms_prefer_what_sets_a_cluster_apart():
    shared = "report findings policy"
    clusters = [
        [f"{shared} shelter design shade", f"{shared} shelter shade canopy"],
        [f"{shared} enzyme protein folding", f"{shared} protein enzyme binding"],
    ]
    names = distinctive_terms(clusters, top=2)
    assert "report" not in " ".join(names[0]) and "report" not in " ".join(names[1])
    assert any("shelter" in t or "shade" in t for t in names[0])
    assert any("protein" in t or "enzyme" in t for t in names[1])


def test_distinctive_terms_ignore_a_single_sources_phrasing():
    clusters = [
        ["quux quux quux street", "street crossing", "street crossing"],
        ["other words here"],
    ]
    sources = [[1, 2, 3], [4]]
    names = distinctive_terms(clusters, top=3, sources_by_cluster=sources, min_sources=2)
    assert "quux" not in names[0]
    assert "street" in " ".join(names[0])


def test_distinctive_terms_do_not_repeat_a_word_through_a_bigram():
    names = distinctive_terms([["bus shelter bus shelter bus shelter"], ["tree"]], top=3)
    words = [w for term in names[0] for w in term.split()]
    assert len(words) == len(set(words))


# ---------------------------------------------------------------------------
# The build's pure parts (areabuild)

from meridian_core.areabuild import (  # noqa: E402
    STABLE_MATCH,
    inherit_positions,
    layout,
    level_sizes,
    siblings,
)


@pytest.mark.parametrize("passages", [1, 20, 150, 1_000, 22_000, 50_000, 10_000_000])
def test_each_level_is_no_larger_than_the_one_below(passages):
    regions, areas, leaves = level_sizes(passages)
    assert 1 <= regions <= areas <= leaves


def test_level_sizes_are_capped():
    regions, _, leaves = level_sizes(10_000_000)
    assert leaves == 400 and regions <= 12


def test_an_area_keeps_its_old_position_only_when_it_persists():
    old = [(np.eye(4)[0], 0.5, -0.5), (np.eye(4)[1], -0.2, 0.3)]
    same = np.eye(4)[:1]
    other = np.eye(4)[2:3]
    fresh = np.zeros((1, 2))

    kept, count = inherit_positions(same, fresh, old)
    assert count == 1 and kept[0].tolist() == [0.5, -0.5]

    moved, count = inherit_positions(other, fresh, old)
    assert count == 0 and moved[0].tolist() == [0.0, 0.0]


def test_one_old_position_is_lent_once():
    old = [(np.eye(3)[0], 0.9, 0.9)]
    twins = np.stack([np.eye(3)[0], np.eye(3)[0] * 0.999 + np.eye(3)[1] * 0.01])
    placed, count = inherit_positions(twins, np.zeros((2, 2)), old)
    assert count == 1
    assert placed.tolist().count([0.9, 0.9]) == 1


def test_the_match_threshold_is_a_real_threshold():
    below = np.array([[STABLE_MATCH - 0.05, np.sqrt(1 - (STABLE_MATCH - 0.05) ** 2)]])
    _, count = inherit_positions(below, np.zeros((1, 2)), [(np.array([1.0, 0.0]), 1.0, 1.0)])
    assert count == 0


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 9])
def test_layout_places_every_sibling_inside_the_frame(n):
    rng = np.random.default_rng(n)
    placed = layout(rng.standard_normal((n, 16)))
    assert placed.shape == (n, 2)
    assert np.all(np.abs(placed) <= 1.0 + 1e-9)
    if n >= 2:
        assert len({tuple(np.round(p, 6)) for p in placed}) == n, "no two siblings on one spot"


def test_siblings_group_by_parent_in_parent_order():
    assert siblings(np.array([1, 0, 1, 0, 2])) == [[1, 3], [0, 2], [4]]


def test_prune_drops_emptied_areas_and_regions_and_renumbers():
    from meridian_core.areabuild import prune

    # Leaves kept belong to areas 0 and 2; area 1 (region 1) lost its leaves.
    area_of_leaf, region_of_area = prune(np.array([0, 2, 2]), np.array([0, 1, 2]))
    assert area_of_leaf.tolist() == [0, 1, 1]
    assert region_of_area.tolist() == [0, 1]


def test_a_word_and_its_plural_do_not_both_name_an_area():
    names = distinctive_terms(
        [["vehicle vehicles vehicle vehicles driver", "vehicle driver"], ["other things"]], top=3
    )
    assert not {"vehicle", "vehicles"} <= set(names[0])
    assert "driver" in names[0]
