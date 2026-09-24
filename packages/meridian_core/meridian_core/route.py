"""A route from one subject to another, across claims and resemblance (task P6-32).

`graphview.shortest_path` (`P6-03`) answers "how do two nodes connect by
stated links". This module widens that in two directions and keeps the two
kinds of step apart:

- **cited** hops are edges: a passage states the link, and the hop carries the
  edge, its relation and its first passage;
- **similar** hops join two things that only read alike: two nodes whose name
  vectors clear `SIMILAR_FLOOR`, or a free-text term and the nodes nearest it.

**Every hop is labelled, and the claims-only answer is always computed
beside the mixed one.** "No cited route within N hops" is a finding, not a
failure: two subjects the corpus never links are a gap worth crawling. So the
result carries it as a value (`RouteRead.cited_only`), which the Gaps screen
(`P6-36`) can read without re-running anything.

**Hop sources are pluggable.** A search asks each `HopSource` for the hops
leaving its frontier. Cited edges and name resemblance are the two that exist
today. Areas (`P6-30`) are a third kind of stop: a source that yields hops to
and from ("area", id) keys joins the search without changing it.

**Which route is best.** Fewest hops first, as in `P6-03`. Among equally
short routes, the one with fewer similar hops wins, and then the one whose
steps are better supported. A route through resemblance is therefore never
preferred to an equally short route of claims.

**What a route does not pass through.** A node merged into another is a
redirect (§5.5), never a stop. A reader's note is an entity too, but a route
through the reader's own note is not the corpus connecting two things, so
annotation nodes are not stops either.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import graphview
from .models import Edge, Entity
from .neighbourhood import SIMILAR_FLOOR, find_anchor
from .schemas.route import (
    Allow,
    HopKind,
    HopRead,
    RouteRead,
    RouteSummaryRead,
    StopRead,
)

#: How far a route searches. The same bounds as path mode, for its reason:
#: past a few hops a "connection" is two things in the same corpus.
DEFAULT_ROUTE_DEPTH = graphview.DEFAULT_PATH_DEPTH
MAX_ROUTE_DEPTH = graphview.MAX_PATH_DEPTH

#: Nodes a search may reach before it stops and says so.
MAX_VISITED = graphview.MAX_PATH_VISITED

#: Nearest names asked for per node on a similar hop, and how many frontier
#: nodes are expanded by resemblance per level. Bounds on work: resemblance
#: connects everything to something, so an unbounded similar expansion is a
#: scan of the graph.
SIMILAR_K = 5
MAX_SIMILAR_FRONTIER = 200

#: A stop in the search: ("entity", id), ("term", text) or ("area", id).
Key = tuple[str, int | str]

Embed = Callable[[str], Awaitable[Sequence[float] | None]]


@dataclasses.dataclass(frozen=True)
class Hop:
    src: Key
    dst: Key
    kind: HopKind
    #: Support (distinct passages) for a cited hop, cosine for a similar one.
    score: float
    edge: Edge | None = None


class HopSource(Protocol):
    """Anything that can say which hops leave a set of stops."""

    async def hops(self, sess: AsyncSession, frontier: Sequence[Key]) -> list[Hop]: ...


def _entity_ids(frontier: Sequence[Key]) -> list[int]:
    return sorted(int(k[1]) for k in frontier if k[0] == "entity")


class CitedHops:
    """Edges: a passage states the link."""

    async def hops(self, sess: AsyncSession, frontier: Sequence[Key]) -> list[Hop]:
        ids = _entity_ids(frontier)
        if not ids:
            return []
        edges = (
            await sess.scalars(
                select(Edge)
                .where(or_(Edge.from_node.in_(ids), Edge.to_node.in_(ids)))
                .where(Edge.from_node != Edge.to_node)
            )
        ).all()
        ends = {e.from_node for e in edges} | {e.to_node for e in edges}
        stoppable = await _stoppable(sess, ends)
        on = set(ids)
        out: list[Hop] = []
        for edge in edges:
            support = float(len(set(edge.supporting_chunk_ids or ())))
            if support == 0:
                continue
            for here, there in ((edge.from_node, edge.to_node), (edge.to_node, edge.from_node)):
                if here in on and there in stoppable:
                    out.append(Hop(("entity", here), ("entity", there), "cited", support, edge))
        return out


async def _stoppable(sess: AsyncSession, ids: set[int]) -> set[int]:
    """The ids a route may stop at: live, and not a reader's note."""
    if not ids:
        return set()
    return set(
        (
            await sess.scalars(
                select(Entity.entity_id).where(
                    Entity.entity_id.in_(sorted(ids)),
                    Entity.redirects_to.is_(None),
                    Entity.is_annotation.is_(False),
                )
            )
        ).all()
    )


class SimilarHops:
    """Resemblance: nodes whose name vectors are near, and free-text terms.

    `terms` holds the vectors of the route's ends that name no node, so a
    search can leave a term for the nodes nearest it and arrive at a term from
    any node near enough to it.
    """

    def __init__(
        self,
        terms: dict[str, Sequence[float]] | None = None,
        *,
        floor: float = SIMILAR_FLOOR,
        k: int = SIMILAR_K,
    ) -> None:
        self.terms = {t: list(v) for t, v in (terms or {}).items()}
        self.floor = floor
        self.k = k

    async def hops(self, sess: AsyncSession, frontier: Sequence[Key]) -> list[Hop]:
        out: list[Hop] = []
        ids = _entity_ids(frontier)[:MAX_SIMILAR_FRONTIER]
        if ids:
            rows = await sess.execute(
                text(
                    """
                    SELECT f.entity_id AS src, n.entity_id AS dst,
                           1 - (n.embedding <=> f.embedding) AS sim
                    FROM entities f
                    CROSS JOIN LATERAL (
                        SELECT e.entity_id, e.embedding FROM entities e
                        WHERE e.embedding IS NOT NULL
                          AND e.redirects_to IS NULL
                          AND NOT e.is_annotation
                          AND e.entity_id <> f.entity_id
                        ORDER BY e.embedding <=> f.embedding, e.entity_id
                        LIMIT :k
                    ) n
                    WHERE f.entity_id = ANY(CAST(:ids AS bigint[]))
                      AND f.embedding IS NOT NULL
                    """
                ),
                {"ids": ids, "k": self.k},
            )
            out += [
                Hop(("entity", src), ("entity", dst), "similar", float(sim))
                for src, dst, sim in rows
                if float(sim) >= self.floor
            ]
            for term, vector in self.terms.items():
                distance = Entity.embedding.cosine_distance(vector)
                near = await sess.execute(
                    select(Entity.entity_id, distance).where(
                        Entity.entity_id.in_(ids), Entity.embedding.is_not(None)
                    )
                )
                out += [
                    Hop(("entity", entity_id), ("term", term), "similar", 1.0 - float(d))
                    for entity_id, d in near
                    if 1.0 - float(d) >= self.floor
                ]
        for key in frontier:
            if key[0] != "term" or key[1] not in self.terms:
                continue
            distance = Entity.embedding.cosine_distance(self.terms[str(key[1])])
            near = await sess.execute(
                select(Entity.entity_id, distance)
                .where(
                    Entity.embedding.is_not(None),
                    Entity.redirects_to.is_(None),
                    Entity.is_annotation.is_(False),
                )
                .order_by(distance, Entity.entity_id)
                .limit(self.k)
            )
            out += [
                Hop(key, ("entity", entity_id), "similar", 1.0 - float(d))
                for entity_id, d in near
                if 1.0 - float(d) >= self.floor
            ]
        return out


@dataclasses.dataclass(frozen=True)
class Found:
    hops: list[Hop]
    truncated: bool


def _better(candidate: tuple[int, float], current: tuple[int, float] | None) -> bool:
    """Fewer similar hops, then more support or similarity on the step."""
    return current is None or candidate < current


async def search(
    sess: AsyncSession,
    source: Key,
    target: Key,
    sources: Sequence[HopSource],
    *,
    max_depth: int,
) -> tuple[Found | None, bool]:
    """The best route, level by level: fewest hops, then fewest similar hops.

    Returns the route (None when there is none within `max_depth`) and whether
    the work bound stopped the search early — in which case "none" is weaker
    than it looks, and the caller is told.
    """
    if source == target:
        return Found([], False), False

    parent: dict[Key, Hop] = {}
    similar_so_far: dict[Key, int] = {source: 0}
    seen: set[Key] = {source}
    frontier: list[Key] = [source]
    truncated = False
    for _ in range(max_depth):
        if not frontier:
            break
        if len(seen) > MAX_VISITED:
            truncated = True
            break
        on = set(frontier)
        reached: dict[Key, tuple[tuple[int, float], Hop]] = {}
        for hop_source in sources:
            for hop in await hop_source.hops(sess, frontier):
                if hop.src not in on or hop.dst in seen:
                    continue
                rank = (similar_so_far[hop.src] + (hop.kind == "similar"), -hop.score)
                current = reached.get(hop.dst)
                if _better(rank, current[0] if current else None):
                    reached[hop.dst] = (rank, hop)
        for key, (rank, hop) in reached.items():
            parent[key] = hop
            similar_so_far[key] = rank[0]
        seen.update(reached)
        if target in reached:
            route: list[Hop] = []
            at = target
            while at != source:
                hop = parent[at]
                route.append(hop)
                at = hop.src
            route.reverse()
            return Found(route, truncated), truncated
        # Sorted, so a capped similar expansion takes the same nodes each time.
        frontier = sorted(reached, key=lambda k: (k[0], str(k[1])))
    return None, truncated


@dataclasses.dataclass(frozen=True)
class Endpoint:
    """One end of a route: a node the reader picked, or text they typed."""

    entity_id: int | None = None
    term: str | None = None


@dataclasses.dataclass(frozen=True)
class _End:
    key: Key
    stop: StopRead
    vector: Sequence[float] | None = None


async def _resolve(sess: AsyncSession, end: Endpoint, embed: Embed | None) -> _End:
    entity: Entity | None = None
    if end.entity_id is not None:
        entity = await sess.get(Entity, end.entity_id)
        if entity is None:
            raise graphview.NodeNotFound(end.entity_id)
        if entity.redirects_to is not None:
            entity = await sess.get(Entity, entity.redirects_to) or entity
    elif end.term and end.term.strip():
        entity = await find_anchor(sess, end.term)
    else:
        raise ValueError("An end of a route needs a node or a term.")

    if entity is not None:
        return _End(
            ("entity", entity.entity_id),
            StopRead(
                kind="entity",
                id=entity.entity_id,
                name=entity.canonical_name,
                node_type=entity.node_type,
            ),
        )
    term = (end.term or "").strip()
    vector = await embed(term) if embed is not None else None
    return _End(("term", term), StopRead(kind="term", id=None, name=term), vector)


async def route(
    sess: AsyncSession,
    source: Endpoint,
    target: Endpoint,
    *,
    max_depth: int = DEFAULT_ROUTE_DEPTH,
    allow: Allow = "cited_and_similar",
    embed: Embed | None = None,
    extra_sources: Sequence[HopSource] = (),
) -> RouteRead:
    """The best route between two ends, every hop labelled (task P6-32).

    `extra_sources` is where another kind of stop joins — areas, once they
    exist. They take part in the mixed search only; the claims-only answer is
    edges and nothing else, because that is what "cited" means.
    """
    max_depth = max(1, min(max_depth, MAX_ROUTE_DEPTH))
    a = await _resolve(sess, source, embed)
    b = await _resolve(sess, target, embed)

    cited_sources: list[HopSource] = [CitedHops()]
    terms = {str(e.key[1]): e.vector for e in (a, b) if e.key[0] == "term" and e.vector}

    # Claims alone. An end that names no node cannot be reached by a claim,
    # and the summary says so rather than reporting an empty search.
    unnamed = [e.stop.name for e in (a, b) if e.key[0] == "term"]
    if unnamed:
        cited_found, cited_truncated = None, False
        reason = f"“{unnamed[0]}” names no node, so no stated link can reach it."
    else:
        cited_found, cited_truncated = await search(
            sess, a.key, b.key, cited_sources, max_depth=max_depth
        )
        reason = None
    cited_only = RouteSummaryRead(
        found=cited_found is not None,
        hops=len(cited_found.hops) if cited_found is not None else None,
        reason=reason,
    )

    if allow == "cited":
        found, truncated = cited_found, cited_truncated
    else:
        mixed: list[HopSource] = [*cited_sources, SimilarHops(terms), *extra_sources]
        found, truncated = await search(sess, a.key, b.key, mixed, max_depth=max_depth)

    hops = found.hops if found is not None else []
    stops, reads = await _describe(sess, a, b, hops)
    return RouteRead(
        source=a.stop,
        target=b.stop,
        max_depth=max_depth,
        allow=allow,
        found=found is not None,
        hops=len(hops) if found is not None else None,
        stops=stops,
        route=reads,
        cited_hops=sum(1 for h in hops if h.kind == "cited"),
        similar_hops=sum(1 for h in hops if h.kind == "similar"),
        cited_only=cited_only,
        truncated=truncated or cited_truncated,
        similar_floor=SIMILAR_FLOOR,
    )


async def _describe(
    sess: AsyncSession, a: _End, b: _End, hops: Sequence[Hop]
) -> tuple[list[StopRead], list[HopRead]]:
    if not hops:
        return ([a.stop] if a.key == b.key else []), []
    keys = [hops[0].src, *(h.dst for h in hops)]
    ids = sorted({int(k[1]) for k in keys if k[0] == "entity"})
    entities = {
        e.entity_id: e
        for e in (await sess.scalars(select(Entity).where(Entity.entity_id.in_(ids)))).all()
    }
    by_key = {a.key: a.stop, b.key: b.stop}

    def stop(key: Key) -> StopRead:
        if key in by_key:
            return by_key[key]
        if key[0] == "entity":
            entity = entities[int(key[1])]
            return StopRead(
                kind="entity",
                id=entity.entity_id,
                name=entity.canonical_name,
                node_type=entity.node_type,
            )
        if key[0] == "area":
            return StopRead(kind="area", id=int(key[1]), name=f"area {key[1]}")
        return StopRead(kind="term", id=None, name=str(key[1]))

    firsts = [
        h.edge.supporting_chunk_ids[0] for h in hops if h.edge and h.edge.supporting_chunk_ids
    ]
    evidence = await graphview.hydrate_chunks(sess, firsts)
    reads: list[HopRead] = []
    for i, hop in enumerate(hops):
        if hop.kind == "cited" and hop.edge is not None:
            edge = hop.edge
            first = edge.supporting_chunk_ids[0] if edge.supporting_chunk_ids else None
            reads.append(
                HopRead(
                    kind="cited",
                    index=i,
                    edge_id=edge.edge_id,
                    relation_type=edge.relation_type,
                    forward=hop.src == ("entity", edge.from_node),
                    support=int(hop.score),
                    contested=graphview.is_contested(edge),
                    evidence=evidence.get(first) if first is not None else None,
                )
            )
        else:
            reads.append(HopRead(kind=hop.kind, index=i, similarity=round(hop.score, 4)))
    return [stop(k) for k in keys], reads
