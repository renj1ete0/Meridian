"""Bridges between areas (task P6-31).

What connects two areas, in three kinds that are **never merged**, because
they are different claims about the corpus:

- **cited** — a claim in the graph (an edge) whose evidence spans the two:
  the passages it cites fall in both, or it links a concept anchored in one
  to a concept anchored in the other. A concept's anchor is the area most of
  its own supporting passages sit in. This is the only kind that says a
  source *states* a connection.
- **similar** — the most similar pair of passages across the two, drawn from
  the passages of each nearest the other's centre. Near in meaning, and
  nothing more: two passages can be close and disagree, or be close because
  they share boilerplate.
- **shared terms** — distinctive terms both areas carry.

Bridges are computed for siblings only (regions with regions, and areas under
one parent with each other), since those are what the map draws together: for
every pair with a cited claim, and for each area's :data:`NEIGHBOURS` most
similar siblings. Written with the build, and pruned with it.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Area, AreaBridge, AreaMember, Chunk, Edge, Entity

#: Each area's most similar siblings get a similar-passage bridge.
NEIGHBOURS = 3
#: Passages of each side nearest the other's centre, compared pairwise.
CANDIDATES = 40
SIMILAR_PAIRS = 3


def exact_distance(column, vector):
    """Cosine distance the HNSW index cannot serve, for exact nearest-in-a-subset.

    ``ORDER BY embedding <=> v LIMIT n`` is planned as an index scan whenever
    the table is large enough, and that scan yields at most ``ef_search``
    rows *before* the WHERE clause (see handover, "An HNSW scan returns at
    most ef_search rows"). Asking for the passages of one area nearest a point
    then returns the few that happen to be among the corpus-wide nearest —
    often none. ``+ 0`` makes the sort key an expression the index does not
    match, so the rows are filtered first and sorted exactly.
    """
    return column.cosine_distance(vector) + 0


@dataclasses.dataclass
class _Node:
    area_id: int
    level: int
    parent_id: int | None
    centroid: np.ndarray
    terms: list[str]


def anchor(leaves: list[int]) -> int | None:
    """The leaf most of a concept's passages sit in; ties to the smaller id."""
    if not leaves:
        return None
    counts = Counter(leaves)
    return min(counts, key=lambda leaf: (-counts[leaf], leaf))


def sibling_pairs(areas: list[_Node]) -> set[tuple[int, int]]:
    """Each area with its :data:`NEIGHBOURS` most similar siblings."""
    groups: dict[tuple[int, int | None], list[_Node]] = defaultdict(list)
    for area in areas:
        groups[(area.level, area.parent_id)].append(area)
    pairs: set[tuple[int, int]] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        mat = np.stack([a.centroid for a in group])
        sims = mat @ mat.T
        np.fill_diagonal(sims, -np.inf)
        for i, area in enumerate(group):
            # Itself last (its diagonal is -inf), and never at all: in a
            # group smaller than the neighbour count it would otherwise
            # reach its own row.
            others = [int(j) for j in np.argsort(-sims[i]) if int(j) != i]
            for j in others[:NEIGHBOURS]:
                other = group[j]
                pairs.add(tuple(sorted((area.area_id, other.area_id))))  # type: ignore[arg-type]
    return pairs


def best_pairs(
    ids_a: list[int], vecs_a: np.ndarray, ids_b: list[int], vecs_b: np.ndarray, *, k: int
) -> list[dict]:
    """The ``k`` most similar cross pairs, no passage used twice."""
    if not ids_a or not ids_b:
        return []
    sims = vecs_a @ vecs_b.T
    order = np.dstack(np.unravel_index(np.argsort(-sims, axis=None), sims.shape))[0]
    used_a: set[int] = set()
    used_b: set[int] = set()
    out: list[dict] = []
    for i, j in order:
        if i in used_a or j in used_b or ids_a[i] == ids_b[j]:
            continue
        out.append({"chunk_a": ids_a[i], "chunk_b": ids_b[j], "score": round(float(sims[i, j]), 4)})
        used_a.add(int(i))
        used_b.add(int(j))
        if len(out) == k:
            break
    return out


def shared_terms(a: list[str], b: list[str]) -> list[str]:
    """Terms in both, in the first area's order."""
    other = set(b)
    return [t for t in a if t in other]


async def build_bridges(sess: AsyncSession, build_id: int) -> int:
    """Write every bridge of a build. Returns how many."""
    areas = [
        _Node(a.area_id, a.level, a.parent_id, np.asarray(a.centroid, dtype=np.float64), a.terms)
        for a in await sess.scalars(select(Area).where(Area.build_id == build_id))
    ]
    by_id = {a.area_id: a for a in areas}

    def ancestors(leaf: int) -> dict[int, int]:
        """``{level: area_id}`` from a leaf up."""
        out = {}
        node: _Node | None = by_id[leaf]
        while node is not None:
            out[node.level] = node.area_id
            node = by_id.get(node.parent_id) if node.parent_id is not None else None
        return out

    # Claims, and the leaves their evidence sits in.
    edges = (
        await sess.execute(
            select(Edge.edge_id, Edge.from_node, Edge.to_node, Edge.supporting_chunk_ids)
        )
    ).all()
    annotations = set(await sess.scalars(select(Entity.entity_id).where(Entity.is_annotation)))
    entities = (
        dict(
            (
                await sess.execute(
                    select(Entity.entity_id, Entity.supporting_chunk_ids).where(
                        Entity.entity_id.in_({e for _, f, t, _ in edges for e in (f, t)})
                    )
                )
            ).all()
        )
        if edges
        else {}
    )
    wanted = {c for _, _, _, chunks in edges for c in chunks or ()}
    wanted |= {c for chunks in entities.values() for c in chunks or ()}
    leaf_of: dict[int, int] = {}
    source_of: dict[int, int] = {}
    if wanted:
        for chunk_id, area_id, source_id in await sess.execute(
            select(AreaMember.chunk_id, AreaMember.area_id, Chunk.source_id)
            .join(Chunk, Chunk.chunk_id == AreaMember.chunk_id)
            .where(AreaMember.build_id == build_id, AreaMember.chunk_id.in_(wanted))
        ):
            leaf_of[chunk_id] = area_id
            source_of[chunk_id] = source_id

    cited: dict[tuple[int, int], list[int]] = defaultdict(list)
    cited_sources: dict[tuple[int, int], set[int]] = defaultdict(set)
    for edge_id, from_node, to_node, chunks in edges:
        if from_node in annotations or to_node in annotations:
            # A reader's note links what they chose to link; it is not a
            # claim any source makes.
            continue
        leaves = {leaf_of[c] for c in chunks or () if c in leaf_of}
        for node in (from_node, to_node):
            home = anchor([leaf_of[c] for c in entities.get(node) or () if c in leaf_of])
            if home is not None:
                leaves.add(home)
        chains = [ancestors(leaf) for leaf in leaves]
        for level in (1, 2, 3):
            for a, b in itertools.combinations(sorted({c[level] for c in chains}), 2):
                if by_id[a].parent_id != by_id[b].parent_id:
                    continue
                cited[(a, b)].append(edge_id)
                cited_sources[(a, b)].update(source_of[c] for c in chunks or () if c in source_of)

    pairs = sibling_pairs(areas) | set(cited)

    # Leaves under each area, for drawing its passages.
    leaves_under: dict[int, list[int]] = defaultdict(list)
    for area in areas:
        if area.level == 3:
            for ancestor in ancestors(area.area_id).values():
                leaves_under[ancestor].append(area.area_id)

    async def nearest(area: _Node, towards: _Node) -> tuple[list[int], np.ndarray]:
        rows = (
            await sess.execute(
                select(Chunk.chunk_id, Chunk.embedding)
                .join(AreaMember, AreaMember.chunk_id == Chunk.chunk_id)
                .where(
                    AreaMember.build_id == build_id,
                    AreaMember.area_id.in_(leaves_under[area.area_id]),
                )
                .order_by(exact_distance(Chunk.embedding, towards.centroid.tolist()))
                .limit(CANDIDATES)
            )
        ).all()
        if not rows:
            return [], np.zeros((0, 1))
        vecs = np.asarray([np.asarray(r[1], dtype=np.float64) for r in rows])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return [r[0] for r in rows], vecs / np.where(norms == 0, 1, norms)

    rows = []
    for a, b in sorted(pairs):
        left, right = by_id[a], by_id[b]
        ids_a, vecs_a = await nearest(left, right)
        ids_b, vecs_b = await nearest(right, left)
        rows.append(
            AreaBridge(
                build_id=build_id,
                level=left.level,
                area_a=a,
                area_b=b,
                similarity=round(float(left.centroid @ right.centroid), 4),
                cited_edge_ids=sorted(set(cited.get((a, b), []))),
                cited_sources=len(cited_sources.get((a, b), ())),
                similar_pairs=best_pairs(ids_a, vecs_a, ids_b, vecs_b, k=SIMILAR_PAIRS),
                shared_terms=shared_terms(left.terms, right.terms),
            )
        )
    sess.add_all(rows)
    await sess.flush()
    return len(rows)
