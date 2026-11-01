"""Which topic a news headline is about, from its embedding (`B-251`).

A headline is on a topic when it is close to the topic's prototype (the topic labeller's
:class:`~meridian_core.topiclabels.Basis`) *and* closer to the topic's passages than to
the corpus's off-topic passages. Pure scoring plus the two centroid queries; the worker
embeds and caches. No language model (§2.1). See docs/features/discovery.md#headline-filter.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from sqlalchemy import func, select, tablesample
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, ChunkTopics
from .topiclabels import Basis

__all__ = [
    "CENTROID_MARGIN",
    "CENTROID_SAMPLE",
    "MIN_CENTROID_PASSAGES",
    "PROTOTYPE_FLOOR",
    "PROTOTYPE_FLOOR_ALONE",
    "HeadlineBasis",
    "offtopic_centroid",
    "topic_centroids",
]

# Measured on 703 graded headlines, 2026-11-01; re-measure before moving any.
# See docs/features/discovery.md#headline-filter.

#: A headline's centred similarity to a topic's prototype must reach this.
PROTOTYPE_FLOOR = 0.30

#: ...and its similarity to the topic's passage centroid must beat its similarity to the
#: off-topic centroid by this much. Catches headlines that merely share a topic's words.
CENTROID_MARGIN = 0.01

#: The floor for a topic with no centroid yet (too few labelled passages): the prototype
#: alone, held higher because nothing else checks it.
PROTOTYPE_FLOOR_ALONE = 0.34

#: Passages averaged into one centroid: enough that the mean is stable, few enough to be cheap.
CENTROID_SAMPLE = 2000

#: Below this many labelled passages a centroid is noise, and the topic is judged alone.
MIN_CENTROID_PASSAGES = 50

#: Percent of ``chunk_topics`` pages the off-topic sample reads; a full random sort of a
#: million rows took half a minute. A small corpus that yields too few reads it all.
OFFTOPIC_SAMPLE_PERCENT = 1.0


def _unit(vector: Sequence[float] | np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(array))
    return array / norm if norm > 0 else array


@dataclasses.dataclass(frozen=True)
class HeadlineBasis:
    """Everything a headline is scored against, built once and cached by the worker."""

    prototypes: Basis
    #: Unit passage centroid per topic; a topic missing here is judged on its prototype alone.
    centroids: Mapping[str, np.ndarray]
    #: Unit centroid of passages labelled with no topic; None judges every topic alone.
    offtopic: np.ndarray | None

    @classmethod
    def build(
        cls,
        prototypes: Basis,
        centroids: Mapping[str, Sequence[float]],
        offtopic: Sequence[float] | None,
    ) -> HeadlineBasis:
        """From raw vectors; centroids for topics the prototypes do not know are dropped."""
        return cls(
            prototypes=prototypes,
            centroids={t: _unit(v) for t, v in centroids.items() if t in prototypes.topics},
            offtopic=None if offtopic is None else _unit(offtopic),
        )

    @property
    def topics(self) -> tuple[str, ...]:
        return self.prototypes.topics

    def topics_for(self, vector: Sequence[float]) -> tuple[str, ...]:
        """Every topic this headline vector is about, best prototype score first."""
        scores = self.prototypes.scores(vector, language=None)
        unit = _unit(vector)
        away = float(unit @ self.offtopic) if self.offtopic is not None else None
        passed = []
        for topic, score in scores.items():
            centroid = self.centroids.get(topic)
            if centroid is None or away is None:
                if score >= PROTOTYPE_FLOOR_ALONE:
                    passed.append(topic)
            elif score >= PROTOTYPE_FLOOR and float(unit @ centroid) - away >= CENTROID_MARGIN:
                passed.append(topic)
        return tuple(sorted(passed, key=lambda t: (-scores[t], t)))


def _mean_of(rows: Iterable[Sequence[float]]) -> tuple[np.ndarray | None, int]:
    vectors = [_unit(row) for row in rows]
    if not vectors:
        return None, 0
    return np.mean(vectors, axis=0), len(vectors)


def _live_vectors():
    return (
        select(Chunk.embedding)
        .join(ChunkTopics, ChunkTopics.chunk_id == Chunk.chunk_id)
        .where(Chunk.superseded_at.is_(None), Chunk.embedding.is_not(None))
    )


async def topic_centroids(
    sess: AsyncSession, topics: Iterable[str], *, sample: int = CENTROID_SAMPLE
) -> dict[str, np.ndarray]:
    """Each topic's centroid over a random sample of its labelled live passages.

    A topic with fewer than :data:`MIN_CENTROID_PASSAGES` is left out.
    """
    centroids: dict[str, np.ndarray] = {}
    for topic in topics:
        stmt = (
            _live_vectors()
            .where(ChunkTopics.topic_labels.contains([topic]))
            .order_by(func.random())
            .limit(sample)
        )
        mean, count = _mean_of((await sess.scalars(stmt)).all())
        if mean is not None and count >= MIN_CENTROID_PASSAGES:
            centroids[topic] = mean
    return centroids


async def offtopic_centroid(
    sess: AsyncSession, *, sample: int = CENTROID_SAMPLE
) -> np.ndarray | None:
    """The centroid of passages labelled with no topic, or None when there are too few."""
    sampled = tablesample(ChunkTopics, func.system(OFFTOPIC_SAMPLE_PERCENT))
    stmt = (
        select(Chunk.embedding)
        .join(sampled, sampled.c.chunk_id == Chunk.chunk_id)
        .where(
            Chunk.superseded_at.is_(None),
            Chunk.embedding.is_not(None),
            sampled.c.topic_labels == [],
        )
        .limit(sample)
    )
    mean, count = _mean_of((await sess.scalars(stmt)).all())
    if count < MIN_CENTROID_PASSAGES:
        stmt = _live_vectors().where(ChunkTopics.topic_labels == []).limit(sample)
        mean, count = _mean_of((await sess.scalars(stmt)).all())
    return mean if count >= MIN_CENTROID_PASSAGES else None
