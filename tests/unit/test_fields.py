"""Naming Map areas by field of work (tasks B-74, B-157).

The claims: a name only ever comes from the list; deeper areas take subfields and a
region is named from what its areas were given, never from its own centroid; nothing
that fits poorly is forced on an area; and the shipped list is the taxonomy it says it is.
"""

from __future__ import annotations

import numpy as np
import pytest

from meridian_core.areaview import area_name
from meridian_core.fields import (
    FIELDS_PATH,
    MIN_SIMILARITY,
    REGION_MAJORITY,
    REGION_PAIR,
    FieldLabel,
    assign,
    load_fields,
    nearest,
)


def unit(*values: float) -> np.ndarray:
    v = np.asarray(values, dtype=np.float64)
    return v / np.linalg.norm(v)


FIELDS = [FieldLabel("Social Sciences"), FieldLabel("Medicine")]
FIELD_VECS = np.stack([unit(1, 0, 0), unit(0, 1, 0)])
SUBS = [FieldLabel("Social Sciences", "Transportation"), FieldLabel("Medicine", "Oncology")]
SUB_VECS = np.stack([unit(1, 0.2, 0), unit(0, 1, 0.2)])


def test_deeper_areas_take_subfields_and_regions_the_largest_of_their_children() -> None:
    centroids = np.stack([unit(0.2, 0.2, 1), unit(1, 0.1, 0), unit(0.1, 1, 0)])
    names = assign(
        [1, 2, 2],
        centroids,
        SUBS,
        SUB_VECS,
        parents=[None, 0, 0],
        weights=[0, 30, 10],
    )
    # The region follows its larger child, not its own centroid, and is named
    # by the subfield — a field named five of twelve live regions alike.
    assert names == ["Transportation", "Transportation", "Oncology"]


def test_a_region_whose_areas_fit_nothing_keeps_its_terms() -> None:
    """`B-157`: matching a region's centroid to a field named a live region of legislation
    "Economics" and another "Dentistry"; a region with nothing named below it is unnamed."""
    region_on_a_field = unit(0.1, 1, 0)
    names = assign([1], np.stack([region_on_a_field]), SUBS, SUB_VECS)
    assert names == [None]


def region_of(children: list[tuple[str | None, int]]) -> str | None:
    """The name a region gets from children named ``name`` with ``passages`` each."""
    subs = [
        FieldLabel("Social Sciences", "Transportation"),
        FieldLabel("Social Sciences", "Urban Studies"),
        FieldLabel("Social Sciences", "Law"),
        FieldLabel("Medicine", "Oncology"),
        FieldLabel("Medicine", "Epidemiology"),
        FieldLabel("Engineering", "Automotive Engineering"),
    ]
    axes = {s.name: i for i, s in enumerate(subs)}
    dim = len(subs) + 1
    vecs = np.eye(dim)[: len(subs)]
    centroids = [np.eye(dim)[-1]]  # the region itself: along no label
    for name, _ in children:
        # An area exactly on its label, or along no label at all.
        centroids.append(np.eye(dim)[axes[name]] if name else np.eye(dim)[-1])
    names = assign(
        [1] + [2] * len(children),
        np.stack(centroids),
        subs,
        vecs,
        parents=[None] + [0] * len(children),
        weights=[0] + [w for _, w in children],
    )
    return names[0]


def test_a_region_takes_a_subfield_only_with_a_majority_of_its_passages() -> None:
    assert region_of([("Transportation", 60), ("Oncology", 40)]) == "Transportation"


def test_a_plurality_of_a_mixed_region_does_not_name_it() -> None:
    """The live failure: a broad science region named "Geometry and Topology" after a
    seventh of its passages."""
    mixed = [("Transportation", 15), ("Oncology", 14), ("Automotive Engineering", 14)]
    mixed += [(None, 57)]
    assert region_of(mixed) is None


def test_a_majority_of_one_field_names_the_region_by_field() -> None:
    children = [("Oncology", 30), ("Epidemiology", 25), ("Transportation", 45)]
    assert region_of(children) == "Medicine"


def test_two_subfields_holding_enough_together_name_a_mixed_region() -> None:
    children = [("Transportation", 25), ("Oncology", 20), ("Automotive Engineering", 15)]
    children += [(None, 40)]
    assert region_of(children) == "Transportation & Oncology"


def test_two_fields_holding_enough_together_name_a_mixed_region() -> None:
    children = [("Transportation", 12), ("Law", 10), ("Urban Studies", 9), ("Oncology", 12)]
    children += [("Epidemiology", 10), (None, 47)]
    assert region_of(children) == "Social Sciences & Medicine"


def test_areas_named_by_nothing_count_against_every_share() -> None:
    """Most of the region unnamed: one named child is not its majority however alone it is."""
    assert region_of([("Transportation", 30), (None, 70)]) is None


def test_the_floor_and_shares_are_the_measured_ones() -> None:
    """Moved only with a new measurement (docs/features/map.md#naming-floor)."""
    assert (MIN_SIMILARITY, REGION_MAJORITY, REGION_PAIR) == (0.20, 0.5, 0.4)


def test_a_poor_fit_is_not_forced() -> None:
    names = assign(
        [2, 2],
        np.stack([unit(1, 0.1, 0), unit(0, 0.1, 1)]),
        SUBS,
        SUB_VECS,
        min_similarity=0.9,
    )
    assert names[1] is None


def test_a_label_near_everything_does_not_name_everything() -> None:
    """The live failure: one generic subfield sat near the middle of the space
    and named most regions. Centring removes the shared direction first."""
    hub = FieldLabel("Medicine", "Nursing")
    subs = [*SUBS, hub]
    # Every centroid shares one strong direction (the third axis), and the hub
    # label lies along it, so in raw cosine terms it is nearest to all of them.
    vecs = np.stack([unit(1, 0.2, 0), unit(0, 1, 0.2), unit(0, 0, 1)])
    centroids = np.stack([unit(1, 0, 3), unit(0, 1, 3), unit(1, 1, 3)])
    raw_best, _ = nearest(centroids, vecs, centre=False)
    names = assign([2, 2, 2], centroids, subs, vecs, min_similarity=-1)
    assert [subs[i].name for i in raw_best] == ["Nursing"] * 3
    assert not any(n.startswith("Nursing") for n in names)
    assert names[1] == "Oncology"


def test_every_name_comes_from_the_list() -> None:
    rng = np.random.default_rng(0)
    centroids = rng.normal(size=(40, 3))
    names = assign([1, 2] * 20, centroids, SUBS, SUB_VECS, min_similarity=-1)
    allowed = {f.name for f in FIELDS + SUBS}
    pairs = {f"{a} & {b}" for a in allowed for b in allowed if a != b}
    assert set(names) <= allowed | pairs | {None}
    assert all(names[i] for i in range(1, 40, 2)), "with no floor, every deeper area is named"


def test_nearest_is_by_direction_not_length() -> None:
    best, sims = nearest(np.stack([np.array([10.0, 0.5, 0])]), FIELD_VECS, centre=False)
    assert best.tolist() == [0] and sims[0] == pytest.approx(0.9988, abs=1e-3)


def test_nothing_to_name_is_not_an_error() -> None:
    assert assign([], np.zeros((0, 3)), SUBS, SUB_VECS) == []


# -- the shipped list ------------------------------------------------------------------


def test_the_shipped_list_is_the_taxonomy() -> None:
    fields, subfields = load_fields(FIELDS_PATH)
    # OpenAlex's 26, and the kinds of public document no research list names (`B-157`).
    assert len(fields) == 27
    assert len(subfields) > 200
    records = {s.name for s in subfields if s.field == "Public Records"}
    assert {"Legislation and Statutes", "Budgets and Appropriations"} <= records
    names = {s.name for s in subfields}
    assert {"Transportation", "Urban Studies", "Automotive Engineering"} <= names
    # Names that say nothing were left out on purpose.
    assert not any(n.startswith("General ") for n in names)
    assert all(s.field in {f.field for f in fields} for s in subfields)


def test_a_subfield_is_embedded_in_its_fields_context() -> None:
    assert SUBS[0].text() == "Transportation (Social Sciences)"
    assert FIELDS[0].text() == "Social Sciences"


# -- the name people read ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("terms", "field", "name"),
    [
        (["road safety", "crash"], "Transportation", "Transportation"),
        # The operator's examples: a licence string and a badge never reach a name.
        (["bync", "free article"], "Oncology", "Oncology"),
        (["bync", "free article", "tumour growth"], None, "Tumour growth"),
        ([], "Transportation", "Transportation"),
        (["road safety"], None, "Road safety"),
    ],
)
def test_a_field_is_the_whole_name(terms, field, name) -> None:
    assert area_name(terms, field) == name


def test_siblings_that_would_share_a_name_are_told_apart() -> None:
    subs = [*SUBS, FieldLabel("Social Sciences", "Urban Studies")]
    vecs = np.stack([unit(1, 0.2, 0), unit(0, 1, 0.2), unit(1, 0, 0.6)])
    centroids = np.stack([unit(1, 0.1, 0.1), unit(1, 0.1, 0.5), unit(0, 1, 0.1)])
    names = assign([2, 2, 2], centroids, subs, vecs, parents=[9, 9, 9])
    assert len(set(names)) == 3
    assert all(n.split(" & ")[0] in {s.name for s in subs} for n in names)


def test_a_unique_name_gets_no_second_part() -> None:
    names = assign(
        [2, 2],
        np.stack([unit(1, 0.1, 0), unit(0.1, 1, 0)]),
        SUBS,
        SUB_VECS,
        parents=[7, 7],
    )
    assert names == ["Transportation", "Oncology"]


def test_names_are_told_apart_across_a_level_not_only_among_siblings() -> None:
    names = assign(
        [2, 2],
        np.stack([unit(1, 0.1, 0.1), unit(1, 0.1, 0.4)]),
        [*SUBS, FieldLabel("Social Sciences", "Urban Studies")],
        np.stack([unit(1, 0.2, 0), unit(0, 1, 0.2), unit(1, 0, 0.6)]),
        parents=[1, 2],  # different parents
    )
    assert len(set(names)) == 2
