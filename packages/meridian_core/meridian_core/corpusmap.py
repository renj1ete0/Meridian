"""A three-dimensional picture of the embedding space (tasks P6-26, P6-29).

A deterministic sample of the chunks search would return (through
:func:`meridian_core.search._conditions`), placed by PCA with each axis's share of the
variance alongside. See docs/features/map.md#the-3d-projection.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, Source
from .search import SearchFilters, _conditions

#: Enough points for structure to show and few enough to draw on a canvas and
#: ship in one response: at roughly 300 bytes a point, the default is under 1MB.
DEFAULT_SAMPLE = 3000
MAX_SAMPLE = 8000

#: How much of a passage rides along for the hover card. The full text is one
#: click away on the source page; the map is for finding it, not reading it.
SNIPPET_CHARS = 160


@dataclasses.dataclass(frozen=True)
class MapPoint:
    chunk_id: int
    source_id: int
    x: float
    y: float
    z: float
    #: The source's primary topic — the one its content scored highest on — or
    #: None for a source about none of them or not yet examined. One colour per
    #: point, so one topic; ``topics`` carries the rest.
    topic: str | None
    #: Every topic the source's content is about, primary first (`P2-21`).
    #: None when nothing has examined it yet; ``[]`` when it was examined and is
    #: about none — the two look alike on a canvas and are opposite answers.
    topics: list[str] | None
    title: str | None
    url: str
    snippet: str


@dataclasses.dataclass(frozen=True)
class CorpusMap:
    as_of: dt.datetime
    points: list[MapPoint]
    #: How many chunks matched before sampling. ``len(points)`` below this
    #: means the picture is a sample, and the reader is told so.
    eligible: int
    #: Share of the sample's variance each axis carries, in axis order.
    explained_variance: tuple[float, float, float]


#: How many principal components the map is drawn on.
COMPONENTS = 3

#: Subspace iteration settings: a block wider than three so the top three converge, and a
#: fixed seed so every call agrees. See docs/features/map.md#the-3d-projection.
_OVERSAMPLE = 10

#: Knuth's multiplicative hash, modulo 2**32: a deterministic shuffle of chunk ids. A third
#: of the cost of ordering by md5 of the id as text, measured on a real corpus.
_SHUFFLE = 2654435761
_MODULUS = 2**32


def _shuffled(chunk_id):
    """The order the sample is drawn in: fixed for a given id, unrelated to insertion order."""
    return (chunk_id * _SHUFFLE) % _MODULUS


_PASSES = 8
_SEED = 0

_ZERO = (0.0, 0.0, 0.0)


def project(vectors: np.ndarray) -> tuple[np.ndarray, tuple[float, float, float]]:
    """The first three principal components of ``vectors``, scaled into [-1, 1].

    By seeded subspace iteration. Each axis is scaled on its own, and each component's
    sign is fixed so its largest loading is positive. See
    docs/features/map.md#the-3d-projection.
    """
    n, d = vectors.shape if vectors.ndim == 2 else (0, 0)
    if n < COMPONENTS + 1:
        # Three points span a plane and no third direction; zero is the honest
        # position for all of them rather than an axis invented from noise.
        return np.zeros((n, COMPONENTS)), _ZERO

    centred = (vectors - vectors.mean(axis=0)).astype(np.float64)
    total = float((centred**2).sum() / (n - 1))
    if total <= 0:
        return np.zeros((n, COMPONENTS)), _ZERO

    width = min(d, COMPONENTS + _OVERSAMPLE)
    basis = np.random.default_rng(_SEED).standard_normal((d, width))
    for _ in range(_PASSES):
        basis, _r = np.linalg.qr(centred.T @ (centred @ basis))

    # The covariance restricted to the block is small enough to decompose
    # exactly; its top eigenvectors, mapped back, are the components.
    reduced = centred @ basis
    eigenvalues, eigenvectors = np.linalg.eigh(reduced.T @ reduced / (n - 1))
    order = np.argsort(eigenvalues)[::-1][:COMPONENTS]
    components = basis @ eigenvectors[:, order]
    signs = np.sign(components[np.abs(components).argmax(axis=0), np.arange(len(order))])
    components = components * np.where(signs == 0, 1, signs)

    coords = centred @ components
    scale = np.abs(coords).max(axis=0)
    coords = coords / np.where(scale == 0, 1, scale)

    shares = [float(value / total) for value in eigenvalues[order].clip(min=0)]
    # Fewer than three dimensions leaves an axis with nothing on it: a zero
    # column and a zero share, never an index error.
    if coords.shape[1] < COMPONENTS:
        coords = np.pad(coords, ((0, 0), (0, COMPONENTS - coords.shape[1])))
        shares += [0.0] * (COMPONENTS - len(shares))
    return coords, (shares[0], shares[1], shares[2])


async def corpus_map(
    sess: AsyncSession,
    *,
    sample: int = DEFAULT_SAMPLE,
    topics: list[str] | None = None,
    places: list[str] | None = None,
) -> CorpusMap:
    """A deterministic sample of searchable, embedded chunks, projected to 3D.

    Sampled by a hash of the chunk id, so the same corpus yields the same sample.
    """
    if not 1 <= sample <= MAX_SAMPLE:
        raise ValueError(f"sample must be between 1 and {MAX_SAMPLE}")

    conditions = [
        *_conditions(SearchFilters(topics=topics or None, places=places or None)),
        Chunk.embedding.is_not(None),
    ]
    eligible = (
        await sess.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(and_(*conditions))
        )
        or 0
    )
    # Ids first, then rows. Sorting the full rows would drag every candidate's
    # embedding — four kilobytes each — through the sort to keep a few thousand,
    # and on a real corpus that spilled to disk and took seconds.
    chosen = (
        select(Chunk.chunk_id)
        .join(Source, Source.source_id == Chunk.source_id)
        .where(and_(*conditions))
        .order_by(_shuffled(Chunk.chunk_id))
        .limit(sample)
        .scalar_subquery()
    )
    rows = (
        await sess.execute(
            select(
                Chunk.chunk_id,
                Chunk.source_id,
                Chunk.embedding,
                func.left(Chunk.text, SNIPPET_CHARS),
                Source.topic_labels,
                Source.title,
                Source.url,
            )
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id.in_(chosen))
            .order_by(_shuffled(Chunk.chunk_id))
        )
    ).all()

    now = dt.datetime.now(dt.UTC)
    if not rows:
        return CorpusMap(as_of=now, points=[], eligible=eligible, explained_variance=_ZERO)

    vectors = np.asarray([np.asarray(row[2], dtype=np.float32) for row in rows])
    coords, explained = project(vectors)

    points = [
        MapPoint(
            chunk_id=chunk_id,
            source_id=source_id,
            x=round(float(x), 4),
            y=round(float(y), 4),
            z=round(float(z), 4),
            topic=labels[0] if labels else None,
            # Stored best first by the labeller, so order is meaning here.
            topics=list(labels) if labels is not None else None,
            title=title,
            url=url,
            snippet=snippet,
        )
        for (chunk_id, source_id, _, snippet, labels, title, url), (x, y, z) in zip(
            rows, coords, strict=True
        )
    ]
    return CorpusMap(as_of=now, points=points, eligible=eligible, explained_variance=explained)
