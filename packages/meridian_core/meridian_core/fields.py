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

#: Below this cosine similarity, no field fits well enough to name an area by;
#: the area keeps its term-based name. Measured against bge-m3 centroids, a
#: fitting subfield scores well above it.
MIN_SIMILARITY = 0.35


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


def nearest(centroids: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each centroid, the index of the nearest label and its cosine similarity."""
    if len(centroids) == 0 or len(labels) == 0:
        return np.zeros(len(centroids), dtype=int), np.zeros(len(centroids))
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
    min_similarity: float = MIN_SIMILARITY,
) -> list[str | None]:
    """The field name for each area, or None where nothing fits."""
    out: list[str | None] = [None] * len(levels)
    for top, labels, vecs in (
        (True, field_labels, field_vecs),
        (False, subfield_labels, subfield_vecs),
    ):
        rows = [i for i, level in enumerate(levels) if (level == 1) == top]
        if not rows:
            continue
        best, sims = nearest(centroids[rows], vecs)
        for row, index, sim in zip(rows, best, sims, strict=True):
            if sim >= min_similarity:
                out[row] = labels[int(index)].name
    return out
