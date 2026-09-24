"""Building areas: every searchable passage, clustered into one build (task P6-30).

``worker.areas --once`` calls :func:`build_areas` daily. The arithmetic lives
in :mod:`meridian_core.areas`; this module reads the corpus, runs it, and
writes a build — three levels (region › area › sub-area), each area with its
distinctive terms, its stats and a position.

**The passages search would return.** Drawn through
:func:`meridian_core.search._conditions`, as the corpus map is, so an area is
never made of superseded, duplicate or junk text a reader cannot reach.

**Fit on a sample, assign everything.** k-means runs on at most
:data:`FIT_MAX` hash-sampled passages; every passage is then assigned to its
nearest leaf in batches, with each leaf's stats gathered on the way. Memory
stays bounded as the corpus grows, and the same corpus fits the same way.

**Stable positions.** Each group of siblings is laid out from its centroids by
the corpus map's own projection, and then any area whose centroid is close to
one in the previous build (:data:`STABLE_MATCH`) takes that area's position —
so the map moves where the corpus moved and nowhere else.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import time
from collections import Counter

import numpy as np
from sqlalchemy import Text, and_, cast, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from .areas import _normalise_rows, distinctive_terms, nest
from .bridges import build_bridges
from .corpusmap import project
from .models import Area, AreaBuild, AreaMember, Chunk, Source
from .search import SearchFilters, _conditions

#: A leaf holds roughly this many passages. Small enough that a sub-area is
#: one subject, large enough that its name rests on more than a page.
PASSAGES_PER_LEAF = 150
MAX_LEAVES = 400
#: Leaves per area, on average; regions from the square root of the areas.
LEAVES_PER_AREA = 5
MAX_REGIONS = 12
#: The fit runs on at most this many passages.
FIT_MAX = 20_000
#: Below this there is nothing worth clustering, and no build is written.
MIN_PASSAGES = 20
#: Centroid cosine at which an area keeps the previous build's position.
STABLE_MATCH = 0.9
#: The newest build is read; the one before it is kept for stable positions.
KEEP_BUILDS = 2
TERMS_KEPT = 12
ASSIGN_BATCH = 2_000
INSERT_BATCH = 5_000

LEVELS = (1, 2, 3)


def level_sizes(passages: int) -> tuple[int, int, int]:
    """How many regions, areas and leaves a corpus of this size is cut into.

    Each level is no larger than the one below it, so every region holds at
    least one area and every area at least one leaf.
    """
    leaves = max(1, min(MAX_LEAVES, round(passages / PASSAGES_PER_LEAF)))
    areas = max(1, min(leaves, round(leaves / LEAVES_PER_AREA)))
    regions = max(1, min(areas, MAX_REGIONS, round(math.sqrt(areas) * 1.5)))
    return regions, areas, leaves


def group_centroids(centroids: np.ndarray, sizes: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Size-weighted, unit-length centroid of each group of clusters."""
    count = int(groups.max()) + 1 if len(groups) else 0
    out = np.zeros((count, centroids.shape[1]), dtype=np.float64)
    for i, g in enumerate(groups):
        out[int(g)] += centroids[i] * sizes[i]
    return _normalise_rows(out)


def layout(centroids: np.ndarray) -> np.ndarray:
    """Positions in [-1, 1]² for a group of sibling clusters.

    The first two principal components when there are four or more siblings
    (the corpus map's projection, so the two pictures agree); fewer span no
    plane worth fitting, so they sit evenly round a circle.
    """
    n = len(centroids)
    if n <= 1:
        return np.zeros((n, 2))
    if n >= 4:
        coords, _ = project(np.asarray(centroids, dtype=np.float64))
        if np.abs(coords[:, :2]).sum() > 0:
            return coords[:, :2]
    angles = np.arange(n) * (2 * math.pi / n)
    return np.stack([np.cos(angles), np.sin(angles)], axis=1) * 0.6


def inherit_positions(
    centroids: np.ndarray,
    fresh: np.ndarray,
    previous: list[tuple[np.ndarray, float, float]],
) -> tuple[np.ndarray, int]:
    """Fresh positions, with the previous build's wherever an area persists.

    ``previous`` is ``(centroid, x, y)`` for the last build's areas at the same
    level. Greedy on similarity, best first; each old area lends its position
    once, so two new areas never land on the same spot. Returns the positions
    and how many were inherited.
    """
    out = np.array(fresh, dtype=np.float64, copy=True)
    if not previous or len(centroids) == 0:
        return out, 0
    old = _normalise_rows(np.stack([np.asarray(c, dtype=np.float64) for c, _, _ in previous]))
    sims = _normalise_rows(np.asarray(centroids, dtype=np.float64)) @ old.T
    order = np.dstack(np.unravel_index(np.argsort(-sims, axis=None), sims.shape))[0]
    used_new: set[int] = set()
    used_old: set[int] = set()
    for i, j in order:
        if sims[i, j] < STABLE_MATCH:
            break
        if i in used_new or j in used_old:
            continue
        out[i] = (previous[j][1], previous[j][2])
        used_new.add(int(i))
        used_old.add(int(j))
    return out, len(used_new)


def prune(area_of_leaf: np.ndarray, region_of_area: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Renumber areas and regions after leaves were dropped, removing any left empty."""
    areas = sorted(set(area_of_leaf.tolist()))
    area_no = {old: new for new, old in enumerate(areas)}
    regions = sorted({int(region_of_area[a]) for a in areas})
    region_no = {old: new for new, old in enumerate(regions)}
    return (
        np.asarray([area_no[a] for a in area_of_leaf.tolist()], dtype=np.int64),
        np.asarray([region_no[int(region_of_area[a])] for a in areas], dtype=np.int64),
    )


def siblings(parent_of: np.ndarray) -> list[list[int]]:
    """Indices grouped by parent, in parent order."""
    groups: dict[int, list[int]] = {}
    for index, parent in enumerate(parent_of):
        groups.setdefault(int(parent), []).append(index)
    return [groups[key] for key in sorted(groups)]


@dataclasses.dataclass(frozen=True)
class BuildReport:
    build_id: int | None
    passages: int
    regions: int
    areas: int
    leaves: int
    inherited: int
    seconds: float
    bridges: int = 0


@dataclasses.dataclass
class _Leaf:
    sum: np.ndarray
    count: int = 0
    sources: set[int] = dataclasses.field(default_factory=set)
    tiers: Counter[str] = dataclasses.field(default_factory=Counter)
    newest: dt.datetime | None = None


async def build_areas(sess: AsyncSession, *, topics: list[str] | None = None) -> BuildReport:
    """Cluster every searchable, embedded passage and write a new build.

    ``topics`` narrows the passages as search's topic filter does; the
    scheduled job passes none, and tests use it to build over their own rows.

    Not committed here: the caller commits, so a build appears whole or not at
    all, and a reader never sees one with half its areas.
    """
    started = time.monotonic()
    conditions = [*_conditions(SearchFilters(topics=topics or None)), Chunk.embedding.is_not(None)]
    joined = (
        select(func.count()).select_from(Chunk).join(Source, Source.source_id == Chunk.source_id)
    )
    total = await sess.scalar(joined.where(and_(*conditions))) or 0
    if total < MIN_PASSAGES:
        return BuildReport(None, total, 0, 0, 0, 0, time.monotonic() - started)

    regions_k, areas_k, leaves_k = level_sizes(total)

    # The fit sample, with text for naming, in hash order as the map samples.
    sample = (
        await sess.execute(
            select(Chunk.chunk_id, Chunk.source_id, Chunk.embedding, Chunk.text)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(and_(*conditions))
            .order_by(func.md5(cast(Chunk.chunk_id, Text)))
            .limit(FIT_MAX)
        )
    ).all()
    _, fitted, fit_area_of_leaf, fit_region_of_area = nest(
        np.asarray([np.asarray(r[2], dtype=np.float32) for r in sample]),
        regions_k,
        areas_k,
        leaves_k,
    )

    # Every passage to its nearest leaf, gathering each leaf's stats.
    leaves = [_Leaf(sum=np.zeros(fitted.shape[1])) for _ in range(len(fitted))]
    members: list[tuple[int, int]] = []
    stream = await sess.stream(
        select(
            Chunk.chunk_id, Chunk.source_id, Chunk.embedding, Source.source_tier, Chunk.created_at
        )
        .join(Source, Source.source_id == Chunk.source_id)
        .where(and_(*conditions))
        .order_by(Chunk.chunk_id)
        .execution_options(yield_per=ASSIGN_BATCH)
    )
    async for batch in stream.partitions(ASSIGN_BATCH):
        vectors = _normalise_rows(np.asarray([np.asarray(r[2], dtype=np.float32) for r in batch]))
        for row, vector, index in zip(
            batch, vectors, (vectors @ fitted.T).argmax(axis=1), strict=True
        ):
            leaf = leaves[int(index)]
            leaf.sum += vector
            leaf.count += 1
            leaf.sources.add(row[1])
            leaf.tiers[row[3]] += 1
            if leaf.newest is None or row[4] > leaf.newest:
                leaf.newest = row[4]
            members.append((row[0], int(index)))

    # A leaf the whole corpus left empty (the fit was a sample) is dropped,
    # not drawn as a circle holding nothing — and an area or region left with
    # no leaves goes with it.
    kept = [i for i, leaf in enumerate(leaves) if leaf.count]
    renumber = {old: new for new, old in enumerate(kept)}
    leaves = [leaves[i] for i in kept]
    members = [(chunk, renumber[leaf]) for chunk, leaf in members]
    area_of_leaf, region_of_area = prune(fit_area_of_leaf[kept], fit_region_of_area)
    leaf_vecs = _normalise_rows(np.stack([leaf.sum for leaf in leaves]))
    leaf_sizes = np.asarray([leaf.count for leaf in leaves])
    area_vecs = group_centroids(leaf_vecs, leaf_sizes, area_of_leaf)
    area_sizes = np.bincount(area_of_leaf, weights=leaf_sizes).astype(np.int64)
    region_vecs = group_centroids(area_vecs, area_sizes, region_of_area)

    vecs = {1: region_vecs, 2: area_vecs, 3: leaf_vecs}
    #: For each leaf, the index of the cluster holding it at each level.
    holder = {3: np.arange(len(leaves)), 2: area_of_leaf, 1: region_of_area[area_of_leaf]}
    parent_of = {2: region_of_area, 3: area_of_leaf}

    # Names, per level, from the sample's text; each level's clusters compete
    # only with each other, so a region's name says what sets it apart from
    # the other regions.
    leaf_of_chunk = dict(members)
    names: dict[int, list[list[str]]] = {}
    for level in LEVELS:
        texts: list[list[str]] = [[] for _ in vecs[level]]
        sources: list[list[int]] = [[] for _ in vecs[level]]
        for chunk_id, source_id, _, text in sample:
            leaf = leaf_of_chunk.get(chunk_id)
            if leaf is None:
                continue
            index = int(holder[level][leaf])
            texts[index].append(text)
            sources[index].append(source_id)
        names[level] = distinctive_terms(texts, top=TERMS_KEPT, sources_by_cluster=sources)

    previous: dict[int, list[tuple[np.ndarray, float, float]]] = {level: [] for level in LEVELS}
    last = await sess.scalar(select(func.max(AreaBuild.build_id)))
    if last is not None:
        for level, centroid, x, y in await sess.execute(
            select(Area.level, Area.centroid, Area.x, Area.y).where(Area.build_id == last)
        ):
            previous[level].append((np.asarray(centroid), x, y))

    positions: dict[int, np.ndarray] = {}
    inherited = 0
    for level in LEVELS:
        fresh = np.zeros((len(vecs[level]), 2))
        groups = [list(range(len(vecs[1])))] if level == 1 else siblings(parent_of[level])
        for indices in groups:
            fresh[indices] = layout(vecs[level][indices])
        positions[level], count = inherit_positions(vecs[level], fresh, previous[level])
        inherited += count

    build = AreaBuild(
        passages=len(members),
        params={
            "regions": len(region_vecs),
            "areas": len(area_vecs),
            "leaves": len(leaf_vecs),
            "fit_sample": len(sample),
            "passages_per_leaf": PASSAGES_PER_LEAF,
        },
    )
    sess.add(build)
    await sess.flush()

    ids: dict[int, list[int]] = {}
    for level in LEVELS:
        rows = []
        for index in range(len(vecs[level])):
            inside = [leaves[i] for i in np.flatnonzero(holder[level] == index)]
            tiers: Counter[str] = Counter()
            for leaf in inside:
                tiers.update(leaf.tiers)
            dates = [leaf.newest for leaf in inside if leaf.newest is not None]
            rows.append(
                Area(
                    build_id=build.build_id,
                    level=level,
                    parent_id=None if level == 1 else ids[level - 1][int(parent_of[level][index])],
                    terms=names[level][index],
                    passages=sum(leaf.count for leaf in inside),
                    sources=len(set().union(*(leaf.sources for leaf in inside))),
                    tier_mix=dict(sorted(tiers.items())),
                    newest_at=max(dates) if dates else None,
                    centroid=vecs[level][index].astype(np.float32).tolist(),
                    x=round(float(positions[level][index][0]), 4),
                    y=round(float(positions[level][index][1]), 4),
                )
            )
        sess.add_all(rows)
        await sess.flush()
        ids[level] = [row.area_id for row in rows]

    for start in range(0, len(members), INSERT_BATCH):
        await sess.execute(
            insert(AreaMember),
            [
                {"build_id": build.build_id, "chunk_id": chunk, "area_id": ids[3][leaf]}
                for chunk, leaf in members[start : start + INSERT_BATCH]
            ],
        )

    await sess.flush()
    bridges = await build_bridges(sess, build.build_id)

    newest = select(AreaBuild.build_id).order_by(AreaBuild.build_id.desc()).limit(KEEP_BUILDS)
    await sess.execute(delete(AreaBuild).where(AreaBuild.build_id.not_in(newest.scalar_subquery())))
    await sess.flush()
    return BuildReport(
        build.build_id,
        len(members),
        len(region_vecs),
        len(area_vecs),
        len(leaf_vecs),
        inherited,
        round(time.monotonic() - started, 2),
        bridges,
    )
