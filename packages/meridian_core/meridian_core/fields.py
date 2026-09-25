"""Naming Map areas by field of work (task B-74).

Areas were named by their most distinctive words, and words drawn from text
let whatever the text carries through: licence strings ("bync", "dd"),
publisher badges ("free article"), repository furniture ("arxiv", "doi"). A
list of words to refuse never ends. So an area is named from a fixed list
instead — ``config/fields.yaml``, the OpenAlex fields and subfields — by the
entry nearest its centroid in the same embedding space as the passages. A name
outside the list cannot appear.

Level-1 areas (regions) take a field; deeper areas take a subfield, whose name
says more ("Transportation" rather than "Social Sciences"). The best phrase of
the area's own terms can follow, so siblings in one subfield stay tellable
apart: "Transportation: road safety".

Pure apart from reading the file: the embedding happens in the build job, which
has the embedder, and passes the vectors in.
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
    """Unit rows after subtracting the mean row.

    Matching raw label vectors to centroids lets a few generic labels win
    everything — measured on a live map, one clinical subfield named five of
    twelve regions — because they sit near the middle of the whole space. With
    the shared direction removed, what is left is what is particular to each.
    """
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
    """The field name for each area, or None where nothing fits.

    Deeper areas take the nearest subfield. A region takes the field most of
    its subfields belong to, weighted by passages (``parents`` gives each
    area's parent's index in these lists): there are too few regions to centre
    on, and a region named apart from its own contents reads as a contradiction
    one click down. A region with no named children falls back to the nearest
    field.
    """
    out: list[str | None] = [None] * len(levels)
    field_of = {label.subfield: label.field for label in subfield_labels}
    deep = [i for i, level in enumerate(levels) if level != 1]
    if deep:
        best, sims = nearest(centroids[deep], subfield_vecs)
        for row, index, sim in zip(deep, best, sims, strict=True):
            if sim >= min_similarity:
                out[row] = subfield_labels[int(index)].name

    regions = [i for i, level in enumerate(levels) if level == 1]
    votes: dict[int, dict[str, int]] = {i: {} for i in regions}
    if parents is not None:
        for i, parent in enumerate(parents):
            if parent in votes and out[i] and levels[i] == 2:
                field = field_of.get(out[i])
                if field:
                    weight = weights[i] if weights is not None else 1
                    votes[parent][field] = votes[parent].get(field, 0) + weight
    unvoted = [i for i in regions if not votes[i]]
    for i in regions:
        if votes[i]:
            out[i] = max(votes[i].items(), key=lambda kv: (kv[1], kv[0]))[0]
    if unvoted:
        best, sims = nearest(centroids[unvoted], field_vecs, centre=len(unvoted) > 2)
        for row, index, sim in zip(unvoted, best, sims, strict=True):
            out[row] = field_labels[int(index)].name
    return out
