"""Naming Map areas by field of work (task B-74).

The claims: a name only ever comes from the list; regions take fields and
deeper areas subfields; nothing that fits poorly is forced on an area; and the
shipped list is the taxonomy it says it is.
"""

from __future__ import annotations

import numpy as np
import pytest

from meridian_core.areaview import area_name
from meridian_core.fields import FIELDS_PATH, FieldLabel, assign, load_fields, nearest


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
        FIELDS,
        FIELD_VECS,
        SUBS,
        SUB_VECS,
        parents=[None, 0, 0],
        weights=[0, 30, 10],
    )
    # The region follows its larger child, not its own centroid, and is named
    # by the subfield — a field named five of twelve live regions alike.
    assert names == ["Transportation", "Transportation", "Oncology"]


def test_a_region_without_named_children_falls_back_to_the_nearest_field() -> None:
    names = assign([1], np.stack([unit(0.1, 1, 0)]), FIELDS, FIELD_VECS, SUBS, SUB_VECS)
    assert names == ["Medicine"]


def test_a_poor_fit_is_not_forced() -> None:
    names = assign(
        [2, 2],
        np.stack([unit(1, 0.1, 0), unit(0, 0.1, 1)]),
        FIELDS,
        FIELD_VECS,
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
    names = assign([2, 2, 2], centroids, FIELDS, FIELD_VECS, subs, vecs, min_similarity=-1)
    assert [subs[i].name for i in raw_best] == ["Nursing"] * 3
    assert not any(n.startswith("Nursing") for n in names)
    assert names[1] == "Oncology"


def test_every_name_comes_from_the_list() -> None:
    rng = np.random.default_rng(0)
    centroids = rng.normal(size=(40, 3))
    names = assign([1, 2] * 20, centroids, FIELDS, FIELD_VECS, SUBS, SUB_VECS, min_similarity=-1)
    allowed = {f.name for f in FIELDS + SUBS}
    pairs = {f"{a} & {b}" for a in allowed for b in allowed if a != b}
    assert set(names) <= allowed | pairs


def test_nearest_is_by_direction_not_length() -> None:
    best, sims = nearest(np.stack([np.array([10.0, 0.5, 0])]), FIELD_VECS, centre=False)
    assert best.tolist() == [0] and sims[0] == pytest.approx(0.9988, abs=1e-3)


def test_nothing_to_name_is_not_an_error() -> None:
    assert assign([], np.zeros((0, 3)), FIELDS, FIELD_VECS, SUBS, SUB_VECS) == []


# -- the shipped list ------------------------------------------------------------------


def test_the_shipped_list_is_the_taxonomy() -> None:
    fields, subfields = load_fields(FIELDS_PATH)
    assert len(fields) == 26
    assert len(subfields) > 200
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
    names = assign([2, 2, 2], centroids, FIELDS, FIELD_VECS, subs, vecs, parents=[9, 9, 9])
    assert len(set(names)) == 3
    assert all(n.split(" & ")[0] in {s.name for s in subs} for n in names)


def test_a_unique_name_gets_no_second_part() -> None:
    names = assign(
        [2, 2],
        np.stack([unit(1, 0.1, 0), unit(0.1, 1, 0)]),
        FIELDS,
        FIELD_VECS,
        SUBS,
        SUB_VECS,
        parents=[7, 7],
    )
    assert names == ["Transportation", "Oncology"]
