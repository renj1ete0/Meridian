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
#: to name an area by; it keeps its term-based name.
MIN_SIMILARITY = 0.05


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


def assign(
    levels: list[int],
    centroids: np.ndarray,
    field_labels: list[FieldLabel],
    field_vecs: np.ndarray,
    subfield_labels: list[FieldLabel],
    subfield_vecs: np.ndarray,
    *,
    parents: list[int | None] | None = None,
    weights: list[int] | None = None,
    min_similarity: float = MIN_SIMILARITY,
) -> list[str | None]:
    """The name for each area from the list, or None where nothing fits.

    Deeper areas take the nearest subfield. A region takes the subfield most of its
    areas were given, weighted by ``weights`` (``parents`` gives each area's parent's index
    in these lists), else the nearest field. Names shared within a level take their second
    choice ("A & B"). See docs/features/map.md#naming.
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
            if sims_row[first] >= min_similarity:
                out[row] = subfield_labels[first].name
                if runner != first:
                    second[row] = subfield_labels[runner].name

    regions = [i for i, level in enumerate(levels) if level == 1]
    votes: dict[int, dict[str, int]] = {i: {} for i in regions}
    if parents is not None:
        for i, parent in enumerate(parents):
            if parent in votes and out[i] and levels[i] == 2:
                weight = weights[i] if weights is not None else 1
                votes[parent][out[i]] = votes[parent].get(out[i], 0) + weight
    for i in regions:
        if votes[i]:
            ranked = sorted(votes[i].items(), key=lambda kv: (-kv[1], kv[0]))
            out[i] = ranked[0][0]
            second[i] = ranked[1][0] if len(ranked) > 1 else None
    unvoted = [i for i in regions if not votes[i]]
    if unvoted:
        best, _ = nearest(centroids[unvoted], field_vecs, centre=len(unvoted) > 2)
        for row, index in zip(unvoted, best, strict=True):
            out[row] = field_labels[int(index)].name

    return _told_apart(out, second, levels, parents)


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
