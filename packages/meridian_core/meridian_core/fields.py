"""Naming Map areas by field of work (task B-74).

Names come from ``config/fields.yaml`` (OpenAlex fields and subfields), by the entry
nearest an area's centroid. Pure apart from reading the file: the build job embeds the
labels and passes the vectors in. See docs/features/map.md#naming.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import yaml

FIELDS_PATH = Path(__file__).resolve().parents[3] / "config" / "fields.yaml"

#: Below this cosine similarity *after centring*, no subfield fits well enough
#: to name an area by; it keeps its term-based name. Was 0.05 and then 0.20 (`B-157`,
#: `B-159`); see docs/features/map.md#naming-floor.
MIN_SIMILARITY = 0.25

#: At or above this, the nearest subfield names an area however close its rivals: near ties
#: up here are siblings that both fit ("Ecology", "Nature and Landscape Conservation").
CONFIDENT_SIMILARITY = 0.33

#: Between the floor and the confident level, the nearest subfield must beat the third by
#: this much (`B-159`): a near three-way tie low down is noise, and named statute text
#: "Pharmacy". Measured on hand-judged areas, see docs/features/map.md#naming-floor.
MIN_MARGIN = 0.03

#: Share of a region's passages its areas must give one subfield, or one field, for the
#: region to take that name (`B-157`). A plurality of a mixed region named it after a
#: seventh of its contents.
REGION_MAJORITY = 0.5

#: Share the two largest subfields, or fields, must hold together to name a mixed region
#: "A & B".
REGION_PAIR = 0.4


@dataclasses.dataclass(frozen=True)
class FieldLabel:
    field: str
    subfield: str | None = None

    @property
    def name(self) -> str:
        return self.subfield or self.field

    def text(self) -> str:
        """What is embedded: the subfield in its field's context, or the field."""
        return f"{self.subfield} ({self.field})" if self.subfield else self.field


def load_fields(path: Path = FIELDS_PATH) -> tuple[list[FieldLabel], list[FieldLabel]]:
    """``(fields, subfields)`` from the config file."""
    doc = yaml.safe_load(path.read_text()) or {}
    fields: list[FieldLabel] = []
    subfields: list[FieldLabel] = []
    for field, subs in (doc.get("fields") or {}).items():
        fields.append(FieldLabel(field))
        subfields.extend(FieldLabel(field, sub) for sub in subs or ())
    return fields, subfields


def _centred(vectors: np.ndarray) -> np.ndarray:
    """Unit rows after subtracting the mean row, so generic labels stop winning everything."""
    centred = vectors - vectors.mean(axis=0, keepdims=True)
    return centred / np.maximum(np.linalg.norm(centred, axis=1, keepdims=True), 1e-12)


def nearest(
    centroids: np.ndarray, labels: np.ndarray, *, centre: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """For each centroid, the index of the nearest label and its cosine similarity."""
    if len(centroids) == 0 or len(labels) == 0:
        return np.zeros(len(centroids), dtype=int), np.zeros(len(centroids))
    if centre and len(centroids) > 1:
        a, b = _centred(centroids), _centred(labels)
    else:
        a = centroids / np.maximum(np.linalg.norm(centroids, axis=1, keepdims=True), 1e-12)
        b = labels / np.maximum(np.linalg.norm(labels, axis=1, keepdims=True), 1e-12)
    sims = a @ b.T
    best = sims.argmax(axis=1)
    return best, sims[np.arange(len(best)), best]


def _top2(sims_row: np.ndarray) -> tuple[int, int]:
    order = np.argsort(-sims_row)
    return int(order[0]), int(order[1]) if len(order) > 1 else int(order[0])


def fits(
    sims_row: np.ndarray,
    *,
    min_similarity: float = MIN_SIMILARITY,
    confident: float = CONFIDENT_SIMILARITY,
    min_margin: float = MIN_MARGIN,
) -> bool:
    """Whether the nearest label fits well enough, and clearly enough, to name an area."""
    ranked = np.sort(sims_row)[::-1]
    best = float(ranked[0])
    # A floor raised above the confident level is still a floor.
    if best >= max(confident, min_similarity):
        return True
    third = float(ranked[min(2, len(ranked) - 1)])
    return best >= min_similarity and best - third >= min_margin


def assign(
    levels: list[int],
    centroids: np.ndarray,
    subfield_labels: list[FieldLabel],
    subfield_vecs: np.ndarray,
    *,
    parents: list[int | None] | None = None,
    weights: list[int] | None = None,
    min_similarity: float = MIN_SIMILARITY,
    confident: float = CONFIDENT_SIMILARITY,
    min_margin: float = MIN_MARGIN,
) -> list[str | None]:
    """The name for each area from the list, or None where nothing fits.

    Deeper areas take the nearest subfield when it fits (:func:`fits`). A region is named
    from what its areas were given, weighted by ``weights`` (``parents`` gives each area's
    parent's index in these lists): a subfield or field holding a majority of its passages,
    else its two largest subfields together, else nothing. Names shared within a level take
    their second choice ("A & B"). See docs/features/map.md#naming.
    """
    out: list[str | None] = [None] * len(levels)
    second: list[str | None] = [None] * len(levels)
    deep = [i for i, level in enumerate(levels) if level != 1]
    if deep:
        if len(deep) > 1:
            a, b = _centred(centroids[deep]), _centred(subfield_vecs)
        else:
            a = centroids[deep] / np.linalg.norm(centroids[deep], axis=1, keepdims=True)
            b = subfield_vecs / np.linalg.norm(subfield_vecs, axis=1, keepdims=True)
        sims = a @ b.T
        for row, sims_row in zip(deep, sims, strict=True):
            first, runner = _top2(sims_row)
            if fits(
                sims_row, min_similarity=min_similarity, confident=confident, min_margin=min_margin
            ):
                out[row] = subfield_labels[first].name
                if runner != first:
                    second[row] = subfield_labels[runner].name

    field_of = {label.subfield: label.field for label in subfield_labels}
    regions = [i for i, level in enumerate(levels) if level == 1]
    votes: dict[int, dict[str, int]] = {i: {} for i in regions}
    totals: dict[int, int] = dict.fromkeys(regions, 0)
    if parents is not None:
        for i, parent in enumerate(parents):
            if parent in votes and levels[i] == 2:
                weight = weights[i] if weights is not None else 1
                totals[parent] += weight
                if out[i]:
                    votes[parent][out[i]] = votes[parent].get(out[i], 0) + weight
    for i in regions:
        out[i], second[i] = _region_name(votes[i], totals[i], field_of)

    return _told_apart(out, second, levels, parents)


def _region_name(
    votes: dict[str, int], total: int, field_of: dict[str | None, str]
) -> tuple[str | None, str | None]:
    """A region's name and second choice from its areas' names, weighted by passages.

    Only what holds a majority of the region names it alone; a mixed region is named by its
    two largest subfields, or else its two largest fields, if they hold enough together; and
    otherwise by its terms. Areas named by nothing count against every share. See
    docs/features/map.md#naming.
    """
    if not votes or total <= 0:
        return None, None
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    runner = ranked[1][0] if len(ranked) > 1 else None
    if ranked[0][1] >= REGION_MAJORITY * total:
        return ranked[0][0], runner
    by_field: dict[str, int] = {}
    for name, weight in votes.items():
        field = field_of.get(name)
        if field:
            by_field[field] = by_field.get(field, 0) + weight
    fields = sorted(by_field.items(), key=lambda kv: (-kv[1], kv[0]))
    if fields and fields[0][1] >= REGION_MAJORITY * total:
        return fields[0][0], ranked[0][0]
    if runner is not None and ranked[0][1] + ranked[1][1] >= REGION_PAIR * total:
        return f"{ranked[0][0]} & {runner}", None
    if len(fields) > 1 and fields[0][1] + fields[1][1] >= REGION_PAIR * total:
        return f"{fields[0][0]} & {fields[1][0]}", None
    return None, None


def _told_apart(
    names: list[str | None],
    second: list[str | None],
    levels: list[int],
    parents: list[int | None] | None,
) -> list[str | None]:
    groups: dict[tuple[int, int | None], dict[str, list[int]]] = {}
    for i, name in enumerate(names):
        if name is None:
            continue
        # Across the whole level, not only among siblings: the map is read a level at a time.
        key = (levels[i], None)
        groups.setdefault(key, {}).setdefault(name, []).append(i)
    out = list(names)
    for by_name in groups.values():
        for name, members in by_name.items():
            if len(members) < 2:
                continue
            for i in members:
                if second[i] and second[i] != name:
                    out[i] = f"{name} & {second[i]}"
    return out
