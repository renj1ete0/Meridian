"""The graph workspace's read path (tasks P6-01, P6-02, P6-03; spec §12.1–§12.3).

§12.2 is the rule this module exists to keep: **never render the whole graph.**
A reader lands on one node and sees its neighbours to depth 1, capped and
ranked; everything else is a click away. So nothing here returns more than one
node's neighbourhood, one node's evidence, or one route between two nodes.

**The relational tables are the source of truth**, not Apache AGE (see
`models/graph.py` and `docs/handover.md`). Every query below reads `entities`,
`edges`, `attribute_values`, `chunks` and `sources` directly. §12.1 puts
topology in the client, over a filtered subgraph; the one piece of topology
done here is path mode's breadth-first search, because the client only ever
holds one neighbourhood and a route usually leaves it.

Three decisions carry the rest.

**Ranking is by support, and says so.** §12.2 asks for neighbours "ranked by
edge weight", and `edges` has no weight column. The nearest measured quantity
is how many distinct passages justify the connection, so that is the rank —
named `support`, not `weight`, so no surface can pass a count of passages off
as a score somebody computed.

**Filters act on evidence.** An edge survives a tier, date or topic filter when
at least one passage behind it satisfies all of them together — see
`GraphFilters`. Filtering on the node's own labels instead would keep an edge
whose only evidence is exactly what the reader excluded.

**Facets are counted before filtering.** A rail whose counts shrink as boxes
are ticked hides the one option that would bring a neighbour back.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import annotations
from .models import AttributeDefinition, AttributeValue, Chunk, Edge, Entity, Source
from .passagetopics import passage_topics_for
from .schemas.graph import EntityRead
from .schemas.graphview import (
    ContestedListRead,
    ContestedPairRead,
    ContestedSideRead,
    EvidenceRead,
    FacetCount,
    GraphEdgeRead,
    GraphFacetsRead,
    GraphFilters,
    GraphNodeDetailRead,
    GraphNodeRead,
    NeighbourhoodRead,
    NodeMatchRead,
    NodeSearchRead,
    PathRead,
)
from .schemas.search import NodeAttributeRead, SearchHitRead
from .search import page_unit_for

#: §12.2: "capped at ~30". The default, and the reason the ceiling is low: a
#: hundred labelled nodes around one focus is already the hairball §12.2 is
#: written against.
DEFAULT_NEIGHBOURS = 30
MAX_NEIGHBOURS = 100

#: Second-hop hints per shown neighbour, and overall. Enough to say "there is
#: more past here"; few enough that depth 2 is never quietly drawn.
HINTS_PER_NEIGHBOUR = 2
MAX_HINTS = 40

#: How far path mode searches. Beyond a few hops a "connection" is two nodes in
#: the same corpus, which is not a finding.
DEFAULT_PATH_DEPTH = 4
MAX_PATH_DEPTH = 6
#: Nodes a path search may visit before giving up. A bound on work, so a hub
#: cannot turn one request into a scan of the graph.
MAX_PATH_VISITED = 20_000

#: Passages the node panel carries. The same cap the older node route uses.
MAX_EVIDENCE = 40

#: Pairs the contested list shows at once, and the most a caller may ask for.
#: Each pair hydrates two passages, so the ceiling bounds the response.
DEFAULT_CONTESTED = 50
MAX_CONTESTED = 200
MAX_PANEL_ANNOTATIONS = 10

DEFAULT_MATCHES = 10
MAX_MATCHES = 25


class NodeNotFound(LookupError):
    """No entity has this id. The route turns it into a 404 that names the id."""

    def __init__(self, entity_id: int) -> None:
        super().__init__(f"No entity {entity_id}.")
        self.entity_id = entity_id


# --------------------------------------------------------------------------
# Small shared pieces
# --------------------------------------------------------------------------


def is_contested(edge: Edge) -> bool:
    """§9 marks a disagreement by naming the other edge in `contested_with`.

    An empty array and NULL both mean "not contested"; only a named edge is a
    contradiction somebody recorded.
    """
    return bool(edge.contested_with)


def _contested_clause():
    return and_(Edge.contested_with.is_not(None), func.cardinality(Edge.contested_with) > 0)


@dataclasses.dataclass(frozen=True)
class _Evidence:
    """One chunk's source, as far as filtering and facets need it."""

    source_id: int
    tier: str
    published: dt.date | None
    topics: tuple[str, ...]


async def _evidence_for(sess: AsyncSession, chunk_ids: Iterable[int]) -> dict[int, _Evidence]:
    ids = sorted(set(chunk_ids))
    if not ids:
        return {}
    rows = await sess.execute(
        select(
            Chunk.chunk_id,
            Source.source_id,
            Source.source_tier,
            Source.publication_date,
            Source.topic_labels,
        )
        .join(Source, Source.source_id == Chunk.source_id)
        .where(Chunk.chunk_id.in_(ids))
    )
    return {
        chunk_id: _Evidence(source_id, tier, published, tuple(topics or ()))
        for chunk_id, source_id, tier, published, topics in rows
    }


def edge_passes(edge: Edge, evidence: dict[int, _Evidence], filters: GraphFilters) -> bool:
    """Whether one edge survives the filters (§12.2, task P6-02).

    Pure, so the rule is testable without a database. At least one passage
    must satisfy tier, date and topic *together* — see the module docstring.
    A passage whose source has no date fails any date bound: an undated
    passage is not evidence "from 2020 onwards", and passing it would make the
    filter a suggestion.
    """
    if filters.contested_only and not is_contested(edge):
        return False
    if not filters.narrows_evidence():
        return True

    edge_topics = set(edge.topic_labels or ())
    wanted_topics = set(filters.topics)
    wanted_tiers = set(filters.tiers)

    for chunk_id in edge.supporting_chunk_ids or ():
        found = evidence.get(chunk_id)
        if found is None:
            continue
        if wanted_tiers and found.tier not in wanted_tiers:
            continue
        if filters.published_from and (
            found.published is None or found.published < filters.published_from
        ):
            continue
        if filters.published_to and (
            found.published is None or found.published > filters.published_to
        ):
            continue
        if wanted_topics and not (wanted_topics & (edge_topics | set(found.topics))):
            continue
        return True
    return False


def _other(edge: Edge, entity_id: int) -> int:
    return edge.to_node if edge.from_node == entity_id else edge.from_node


def _edge_read(edge: Edge, kind: str) -> GraphEdgeRead:
    return GraphEdgeRead(
        edge_id=edge.edge_id,
        from_node=edge.from_node,
        to_node=edge.to_node,
        relation_type=edge.relation_type,
        kind=kind,
        confidence=edge.confidence,
        stance=edge.stance,
        certainty=edge.certainty,
        support=len(set(edge.supporting_chunk_ids or ())),
        contested=is_contested(edge),
        contested_with=list(edge.contested_with or ()),
    )


@dataclasses.dataclass
class _Stats:
    degree: int = 0
    sources: int = 0
    newest: dt.date | None = None
    topics: set[str] = dataclasses.field(default_factory=set)


async def _stats(sess: AsyncSession, ids: Sequence[int]) -> dict[int, _Stats]:
    """Degree, distinct sources, newest date and evidence topics per node.

    Over *all* of each node's edges, not only the ones drawn: the hover card
    describes the node, and a node with forty edges of which three are on
    screen should say forty.
    """
    wanted = sorted(set(ids))
    out: dict[int, _Stats] = {entity_id: _Stats() for entity_id in wanted}
    if not wanted:
        return out

    incident = (
        await sess.scalars(
            select(Edge).where(or_(Edge.from_node.in_(wanted), Edge.to_node.in_(wanted)))
        )
    ).all()

    chunks_of: dict[int, set[int]] = defaultdict(set)
    for edge in incident:
        for end in {edge.from_node, edge.to_node}:
            if end in out:
                out[end].degree += 1
                out[end].topics.update(edge.topic_labels or ())
                chunks_of[end].update(edge.supporting_chunk_ids or ())

    evidence = await _evidence_for(sess, (c for chunks in chunks_of.values() for c in chunks))
    for entity_id, chunk_ids in chunks_of.items():
        found = [evidence[c] for c in chunk_ids if c in evidence]
        stats = out[entity_id]
        stats.sources = len({e.source_id for e in found})
        dates = [e.published for e in found if e.published is not None]
        stats.newest = max(dates) if dates else None
        for e in found:
            stats.topics.update(e.topics)
    return out


async def _home_topics(sess: AsyncSession, entities: Iterable[Entity]) -> dict[int, list[str]]:
    """A node's own topics: its labels, else the topics its own chunks came from.

    Derived entities are justified by their edges and usually carry no chunks
    of their own, so for most nodes this is just `topic_labels`. The fallback
    exists for the nodes that do cite passages directly.
    """
    out: dict[int, list[str]] = {}
    need: dict[int, list[int]] = {}
    for entity in entities:
        if entity.topic_labels:
            out[entity.entity_id] = sorted(set(entity.topic_labels))
        elif entity.supporting_chunk_ids:
            need[entity.entity_id] = list(entity.supporting_chunk_ids)
        else:
            out[entity.entity_id] = []
    if need:
        evidence = await _evidence_for(sess, (c for ids in need.values() for c in ids))
        for entity_id, chunk_ids in need.items():
            out[entity_id] = sorted(
                {t for c in chunk_ids if c in evidence for t in evidence[c].topics}
            )
    return out


def _node_read(
    entity: Entity,
    role: str,
    stats: _Stats | None,
    home: list[str],
    *,
    support: int = 0,
    contested: bool = False,
    cross_topic: bool = False,
) -> GraphNodeRead:
    stats = stats or _Stats()
    return GraphNodeRead(
        entity_id=entity.entity_id,
        canonical_name=entity.canonical_name,
        node_type=entity.node_type,
        jurisdiction=entity.jurisdiction,
        is_annotation=entity.is_annotation,
        role=role,
        home_topics=home,
        topics=sorted(set(home) | stats.topics),
        contested=contested,
        cross_topic=cross_topic,
        support=support,
        degree=stats.degree,
        sources=stats.sources,
        newest=stats.newest,
    )


def is_cross_topic(focus_home: Sequence[str], other_home: Sequence[str]) -> bool:
    """Both nodes have home topics and they share none.

    Unknown is not different: a node with no topics is not cross-topic, or
    every unlabelled node would be drawn as the finding §12.2 calls path mode's
    whole point.
    """
    return bool(focus_home) and bool(other_home) and not (set(focus_home) & set(other_home))


@dataclasses.dataclass
class _Neighbour:
    entity_id: int
    edges: list[Edge]

    @property
    def support(self) -> int:
        return len({c for edge in self.edges for c in edge.supporting_chunk_ids or ()})

    @property
    def confidence(self) -> float:
        known = [edge.confidence for edge in self.edges if edge.confidence is not None]
        return max(known) if known else -1.0

    @property
    def contested(self) -> bool:
        return any(is_contested(edge) for edge in self.edges)


def rank(neighbours: Iterable[_Neighbour], names: dict[int, str]) -> list[_Neighbour]:
    """Most support first, then the most confident edge, then by name.

    The name and id close every tie, so the same neighbourhood draws the same
    thirty nodes twice — a cap applied to an unstable order would show a
    different graph on every refresh.
    """
    return sorted(
        neighbours,
        key=lambda n: (-n.support, -n.confidence, names.get(n.entity_id, ""), n.entity_id),
    )


# --------------------------------------------------------------------------
# The neighbourhood (P6-01, P6-02)
# --------------------------------------------------------------------------


async def neighbourhood(
    sess: AsyncSession,
    entity_id: int,
    filters: GraphFilters | None = None,
    *,
    limit: int = DEFAULT_NEIGHBOURS,
) -> NeighbourhoodRead:
    """One node and its depth-1 neighbours, filtered, ranked and capped (§12.2)."""
    filters = filters or GraphFilters()
    limit = max(1, min(limit, MAX_NEIGHBOURS))

    focus = await sess.get(Entity, entity_id)
    if focus is None:
        raise NodeNotFound(entity_id)

    incident = (
        await sess.scalars(
            select(Edge)
            .where(or_(Edge.from_node == entity_id, Edge.to_node == entity_id))
            .where(Edge.from_node != Edge.to_node)
        )
    ).all()

    other_ids = sorted({_other(edge, entity_id) for edge in incident})
    others: dict[int, Entity] = {}
    if other_ids:
        others = {
            e.entity_id: e
            for e in (
                await sess.scalars(select(Entity).where(Entity.entity_id.in_(other_ids)))
            ).all()
            # A node merged into another is a redirect, not a neighbour (§5.5).
            if e.redirects_to is None
        }

    evidence = await _evidence_for(
        sess, (c for edge in incident for c in edge.supporting_chunk_ids or ())
    )

    attributes_of: dict[int, set[str]] = defaultdict(set)
    if others:
        for owner, name in await sess.execute(
            select(AttributeValue.entity_id, AttributeDefinition.name)
            .join(
                AttributeDefinition,
                AttributeDefinition.attribute_id == AttributeValue.attribute_id,
            )
            .where(AttributeValue.entity_id.in_(list(others)))
        ):
            attributes_of[owner].add(name)

    grouped: dict[int, list[Edge]] = defaultdict(list)
    for edge in incident:
        other = _other(edge, entity_id)
        if other in others:
            grouped[other].append(edge)

    facets = _facets(grouped, evidence, attributes_of)

    passing: list[_Neighbour] = []
    for other, edges in grouped.items():
        if filters.attribute and filters.attribute not in attributes_of.get(other, ()):
            continue
        kept = [edge for edge in edges if edge_passes(edge, evidence, filters)]
        if kept:
            passing.append(_Neighbour(other, kept))

    names = {e.entity_id: e.canonical_name for e in others.values()}
    ranked = rank(passing, names)
    shown = ranked[:limit]
    shown_ids = [n.entity_id for n in shown]

    between = []
    if len(shown_ids) > 1:
        between = (
            await sess.scalars(
                select(Edge)
                .where(Edge.from_node.in_(shown_ids), Edge.to_node.in_(shown_ids))
                .where(Edge.from_node != Edge.to_node)
                .order_by(Edge.edge_id)
            )
        ).all()

    hint_edges, hint_entities = await _hints(sess, entity_id, shown_ids)

    stats = await _stats(sess, [entity_id, *shown_ids])
    homes = await _home_topics(sess, [focus, *(others[i] for i in shown_ids)])
    focus_home = homes[entity_id]

    focus_contested = bool(
        await sess.scalar(
            select(func.count())
            .select_from(Edge)
            .where(or_(Edge.from_node == entity_id, Edge.to_node == entity_id))
            .where(_contested_clause())
        )
    )

    nodes = [_node_read(focus, "focus", stats[entity_id], focus_home)]
    for n in shown:
        nodes.append(
            _node_read(
                others[n.entity_id],
                "neighbour",
                stats[n.entity_id],
                homes[n.entity_id],
                support=n.support,
                contested=n.contested,
                cross_topic=is_cross_topic(focus_home, homes[n.entity_id]),
            )
        )
    for hint in hint_entities:
        nodes.append(_node_read(hint, "hint", None, []))

    edges = [_edge_read(edge, "focus") for n in shown for edge in n.edges]
    edges += [_edge_read(edge, "between") for edge in between]
    edges += [_edge_read(edge, "hint") for edge in hint_edges]

    return NeighbourhoodRead(
        focus=nodes[0],
        focus_contested=focus_contested,
        redirects_to=focus.redirects_to,
        nodes=nodes,
        edges=edges,
        total=len(ranked),
        shown=len(shown),
        unfiltered=len(grouped),
        limit=limit,
        filters=filters,
        facets=facets,
    )


def _facets(
    grouped: dict[int, list[Edge]],
    evidence: dict[int, _Evidence],
    attributes_of: dict[int, set[str]],
) -> GraphFacetsRead:
    topics: dict[str, set[int]] = defaultdict(set)
    tiers: dict[str, set[int]] = defaultdict(set)
    attrs: dict[str, set[int]] = defaultdict(set)
    contested: set[int] = set()
    dates: list[dt.date] = []

    for other, edges in grouped.items():
        for name in attributes_of.get(other, ()):
            attrs[name].add(other)
        for edge in edges:
            if is_contested(edge):
                contested.add(other)
            for topic in edge.topic_labels or ():
                topics[topic].add(other)
            for chunk_id in edge.supporting_chunk_ids or ():
                found = evidence.get(chunk_id)
                if found is None:
                    continue
                tiers[found.tier].add(other)
                for topic in found.topics:
                    topics[topic].add(other)
                if found.published is not None:
                    dates.append(found.published)

    def counted(bucket: dict[str, set[int]]) -> list[FacetCount]:
        return sorted(
            (FacetCount(value=value, count=len(ids)) for value, ids in bucket.items()),
            key=lambda f: (-f.count, f.value),
        )

    return GraphFacetsRead(
        topics=counted(topics),
        tiers=counted(tiers),
        attributes=counted(attrs),
        contested=len(contested),
        published_min=min(dates) if dates else None,
        published_max=max(dates) if dates else None,
    )


async def _hints(
    sess: AsyncSession, focus_id: int, shown_ids: Sequence[int]
) -> tuple[list[Edge], list[Entity]]:
    """A few second-hop nodes per neighbour, strongest edges first.

    Drawn as unlabelled dots (design-system.md §2). They are not depth 2 —
    clicking the neighbour is how a reader goes there.
    """
    if not shown_ids:
        return [], []
    excluded = {focus_id, *shown_ids}

    rows = await sess.execute(
        text(
            """
            WITH shown AS (SELECT unnest(CAST(:shown AS bigint[])) AS via),
            candidate AS (
                SELECT shown.via,
                       CASE WHEN e.from_node = shown.via THEN e.to_node ELSE e.from_node END
                           AS other,
                       e.edge_id,
                       cardinality(e.supporting_chunk_ids) AS support
                FROM edges e
                JOIN shown ON e.from_node = shown.via OR e.to_node = shown.via
                WHERE e.from_node <> e.to_node
            ),
            ranked AS (
                SELECT c.*, row_number() OVER (
                    PARTITION BY c.via ORDER BY c.support DESC, c.edge_id
                ) AS rn
                FROM candidate c
                JOIN entities en ON en.entity_id = c.other AND en.redirects_to IS NULL
                WHERE NOT (c.other = ANY(CAST(:excluded AS bigint[])))
            )
            SELECT edge_id, other FROM ranked
            WHERE rn <= :per
            ORDER BY support DESC, edge_id
            LIMIT :cap
            """
        ),
        {
            "shown": list(shown_ids),
            "excluded": sorted(excluded),
            "per": HINTS_PER_NEIGHBOUR,
            "cap": MAX_HINTS,
        },
    )
    picked = rows.all()
    if not picked:
        return [], []
    edge_ids = [edge_id for edge_id, _ in picked]
    entity_ids = sorted({other for _, other in picked})
    edges = (
        await sess.scalars(select(Edge).where(Edge.edge_id.in_(edge_ids)).order_by(Edge.edge_id))
    ).all()
    entities = (
        await sess.scalars(
            select(Entity).where(Entity.entity_id.in_(entity_ids)).order_by(Entity.entity_id)
        )
    ).all()
    return list(edges), list(entities)


# --------------------------------------------------------------------------
# The node panel
# --------------------------------------------------------------------------


async def hydrate_chunks(sess: AsyncSession, chunk_ids: Iterable[int]) -> dict[int, SearchHitRead]:
    """Chunks with the source fields that make them citable, by id.

    Superseded chunks are included, as on the older node route: this is the text
    a claim was *derived from*, and §2.4 re-derives from source chunks, so the
    citation must resolve after the page has changed.
    """
    ids = sorted(set(chunk_ids))
    if not ids:
        return {}
    rows = await sess.execute(
        select(Chunk, Source)
        .join(Source, Source.source_id == Chunk.source_id)
        .where(Chunk.chunk_id.in_(ids))
    )
    passages = await passage_topics_for(sess, ids)
    return {
        chunk.chunk_id: SearchHitRead(
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            text=chunk.text,
            page_or_offset=chunk.page_or_offset,
            chunk_index=chunk.chunk_index,
            url=source.url,
            title=source.title,
            source_tier=source.source_tier,
            publication_date=source.publication_date,
            language=source.language,
            topic_labels=source.topic_labels,
            passage_topics=passages.get(chunk.chunk_id),
            page_unit=page_unit_for((source.extra or {}).get("media_type")),
            media_type=(source.extra or {}).get("media_type"),
            duplicate_of=chunk.duplicate_of,
            # Not ranked: nothing scored these.
            score=0.0,
            lexical_rank=None,
            vector_rank=None,
        )
        for chunk, source in rows
    }


def _side(edge: Edge, names: dict[int, str], hits: dict[int, SearchHitRead]) -> ContestedSideRead:
    """One edge of a contested pair, with its first passage hydrated."""
    first = edge.supporting_chunk_ids[0] if edge.supporting_chunk_ids else None
    return ContestedSideRead(
        edge_id=edge.edge_id,
        relation_type=edge.relation_type,
        from_entity_id=edge.from_node,
        from_name=names.get(edge.from_node, f"#{edge.from_node}"),
        to_entity_id=edge.to_node,
        to_name=names.get(edge.to_node, f"#{edge.to_node}"),
        certainty=edge.certainty,
        stance=edge.stance,
        evidence=hits.get(first) if first is not None else None,
    )


async def contested_pairs(
    sess: AsyncSession, *, limit: int = DEFAULT_CONTESTED
) -> ContestedListRead:
    """Every disagreement §9 marked, across the graph (task P6-10, §12.5).

    The third entry point beside search and coverage. **Each pair once**: §9
    names the disagreement on both edges, so reading `contested_with` from every
    edge would list each pair twice, once from either side. The side with the
    lower edge id is ``ours`` — an arbitrary order, and stated as one, because
    on a list with no node to arrive from neither side is the reader's.

    Newest disagreement first: the list is an entry point, and the reader
    returning to it wants what changed. ``total`` counts pairs before the cap,
    so a capped list says so. A `contested_with` id naming an edge that no
    longer exists is dropped, as on the node panel.
    """
    edges = (
        await sess.scalars(
            select(Edge)
            .where(_contested_clause())
            .order_by(Edge.created_at.desc(), Edge.edge_id.desc())
        )
    ).all()
    by_id = {edge.edge_id: edge for edge in edges}
    # The other side need not name this one back: §9 marks both, but a pair
    # marked from one side only is still a disagreement, so fetch what is named.
    named = sorted({c for edge in edges for c in edge.contested_with or ()} - by_id.keys())
    if named:
        by_id.update(
            {
                e.edge_id: e
                for e in (await sess.scalars(select(Edge).where(Edge.edge_id.in_(named))))
            }
        )

    found: list[tuple[Edge, Edge]] = []
    seen: set[tuple[int, int]] = set()
    for edge in edges:
        for other_id in edge.contested_with or ():
            other = by_id.get(other_id)
            if other is None or other_id == edge.edge_id:
                continue
            key = (min(edge.edge_id, other_id), max(edge.edge_id, other_id))
            if key in seen:
                continue
            seen.add(key)
            first, second = (edge, other) if edge.edge_id < other_id else (other, edge)
            found.append((first, second))

    shown = found[:limit]
    ids = {n for a, b in shown for e in (a, b) for n in (e.from_node, e.to_node)}
    names = (
        {
            e_id: name
            for e_id, name in await sess.execute(
                select(Entity.entity_id, Entity.canonical_name).where(
                    Entity.entity_id.in_(sorted(ids))
                )
            )
        }
        if ids
        else {}
    )
    hits = await hydrate_chunks(
        sess,
        [e.supporting_chunk_ids[0] for a, b in shown for e in (a, b) if e.supporting_chunk_ids],
    )
    return ContestedListRead(
        pairs=[
            ContestedPairRead(ours=_side(a, names, hits), theirs=_side(b, names, hits))
            for a, b in shown
        ],
        total=len(found),
    )


async def node_detail(sess: AsyncSession, entity_id: int) -> GraphNodeDetailRead:
    """Everything the panel beside the canvas shows, in one request (§12.5)."""
    entity = await sess.get(Entity, entity_id)
    if entity is None:
        raise NodeNotFound(entity_id)

    rows = (
        await sess.execute(
            select(AttributeValue, AttributeDefinition)
            .join(
                AttributeDefinition,
                AttributeDefinition.attribute_id == AttributeValue.attribute_id,
            )
            .where(AttributeValue.entity_id == entity_id)
        )
    ).all()
    attributes = sorted(
        (
            NodeAttributeRead(
                value_id=value.value_id,
                name=definition.name,
                scope=definition.scope,
                topic=definition.topic,
                value=value.value,
                value_numeric=value.value_numeric,
                confidence=value.confidence,
                quality_tier=value.quality_tier,
                supporting_chunk_ids=list(value.supporting_chunk_ids or ()),
            )
            for value, definition in rows
        ),
        key=lambda a: (-(a.confidence or 0.0), a.name),
    )

    incident = (
        await sess.scalars(
            select(Edge)
            .where(or_(Edge.from_node == entity_id, Edge.to_node == entity_id))
            .order_by(Edge.edge_id)
        )
    ).all()

    # One citation per chunk. An edge's citation wins over an attribute's,
    # because only an edge records certainty and stance (§8) and dropping them
    # in favour of a bare attribute citation would lose the measured part.
    cited: dict[int, dict] = {}
    for chunk_id in entity.supporting_chunk_ids or ():
        cited.setdefault(chunk_id, {"via": "node"})
    for attribute in attributes:
        for chunk_id in attribute.supporting_chunk_ids:
            if cited.get(chunk_id, {}).get("via") in (None, "node"):
                cited[chunk_id] = {"via": "attribute"}

    other_ids = {_other(edge, entity_id) for edge in incident}
    contested_ids = sorted({c for edge in incident for c in edge.contested_with or ()})
    against = (
        {
            edge.edge_id: edge
            for edge in (
                await sess.scalars(select(Edge).where(Edge.edge_id.in_(contested_ids)))
            ).all()
        }
        if contested_ids
        else {}
    )
    for edge in against.values():
        other_ids.update({edge.from_node, edge.to_node})
    names = (
        {
            e_id: name
            for e_id, name in await sess.execute(
                select(Entity.entity_id, Entity.canonical_name).where(
                    Entity.entity_id.in_(sorted(other_ids | {entity_id}))
                )
            )
        }
        if other_ids
        else {entity_id: entity.canonical_name}
    )

    for edge in sorted(incident, key=lambda e: (-(e.confidence or 0.0), e.edge_id)):
        other = _other(edge, entity_id)
        for chunk_id in edge.supporting_chunk_ids or ():
            if cited.get(chunk_id, {}).get("via") != "edge":
                cited[chunk_id] = {
                    "via": "edge",
                    "relation_type": edge.relation_type,
                    "other_entity_id": other,
                    "other_name": names.get(other),
                    "certainty": edge.certainty,
                    "stance": edge.stance,
                }

    first_chunks = [
        edge.supporting_chunk_ids[0]
        for edge in [*incident, *against.values()]
        if edge.supporting_chunk_ids
    ]
    hits = await hydrate_chunks(sess, [*cited, *first_chunks])

    evidence = sorted(
        (
            EvidenceRead(hit=hits[chunk_id], **how)
            for chunk_id, how in cited.items()
            if chunk_id in hits
        ),
        # Newest first, undated last: the reader weighing a claim wants the
        # current state of the evidence before its history.
        key=lambda e: (
            e.hit.publication_date is None,
            -(e.hit.publication_date.toordinal() if e.hit.publication_date else 0),
            e.hit.chunk_id,
        ),
    )

    pairs = [
        ContestedPairRead(
            ours=_side(edge, names, hits), theirs=_side(against[other_id], names, hits)
        )
        for edge in incident
        for other_id in edge.contested_with or ()
        # An id naming an edge that no longer exists is dropped rather than
        # shown as a half-pair; the count on the badge still includes it.
        if other_id in against
    ]

    homes = await _home_topics(sess, [entity])
    mine = await annotations.listing(sess, about=entity_id, limit=MAX_PANEL_ANNOTATIONS)

    return GraphNodeDetailRead(
        entity=EntityRead.model_validate(entity),
        home_topics=homes[entity_id],
        contested=any(is_contested(edge) for edge in incident),
        attributes=attributes,
        evidence=evidence[:MAX_EVIDENCE],
        evidence_total=len(evidence),
        contested_with=pairs,
        annotations=mine.annotations,
    )


# --------------------------------------------------------------------------
# Search by name (the workspace's node search box)
# --------------------------------------------------------------------------


def _like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_nodes(
    sess: AsyncSession, query: str, *, limit: int = DEFAULT_MATCHES
) -> NodeSearchRead:
    """Entities whose name or an alias contains the query, best match first.

    Exact name, then prefix, then substring; within each, the better-connected
    node first, because a reader typing a name wants the node with something
    around it. Merged-away entities are skipped — they are redirects.
    """
    term = query.strip()
    limit = max(1, min(limit, MAX_MATCHES))
    if not term:
        return NodeSearchRead(query=query, matches=[])

    contains = f"%{_like(term)}%"
    prefix = f"{_like(term)}%"
    rows = await sess.execute(
        text(
            r"""
            SELECT e.entity_id, e.canonical_name, e.node_type, e.jurisdiction, e.is_annotation,
                   CASE WHEN e.canonical_name ILIKE :contains ESCAPE '\' THEN NULL ELSE
                       (SELECT a FROM unnest(e.aliases) a
                        WHERE a ILIKE :contains ESCAPE '\' ORDER BY length(a) LIMIT 1)
                   END AS matched_alias,
                   (SELECT count(*) FROM edges d
                    WHERE d.from_node = e.entity_id OR d.to_node = e.entity_id) AS degree
            FROM entities e
            WHERE e.redirects_to IS NULL
              AND (e.canonical_name ILIKE :contains ESCAPE '\'
                   OR EXISTS (SELECT 1 FROM unnest(e.aliases) a
                              WHERE a ILIKE :contains ESCAPE '\'))
            ORDER BY lower(e.canonical_name) = lower(:term) DESC,
                     e.canonical_name ILIKE :prefix ESCAPE '\' DESC,
                     degree DESC,
                     e.canonical_name,
                     e.entity_id
            LIMIT :limit
            """
        ),
        {"contains": contains, "prefix": prefix, "term": term, "limit": limit},
    )
    return NodeSearchRead(
        query=query,
        matches=[
            NodeMatchRead(
                entity_id=row.entity_id,
                canonical_name=row.canonical_name,
                node_type=row.node_type,
                jurisdiction=row.jurisdiction,
                is_annotation=row.is_annotation,
                matched_alias=row.matched_alias,
                degree=row.degree,
            )
            for row in rows
        ],
    )


# --------------------------------------------------------------------------
# Path mode (P6-03)
# --------------------------------------------------------------------------


async def shortest_path(
    sess: AsyncSession, source: int, target: int, *, max_depth: int = DEFAULT_PATH_DEPTH
) -> PathRead:
    """One shortest route between two nodes, edges taken in either direction.

    Breadth-first, level by level, one query per level — so the work is bounded
    by `max_depth` and `MAX_PATH_VISITED` rather than by the size of the graph.
    Within a level the best-supported edge is tried first, so where two routes
    are equally short the one with more evidence behind it is returned.
    """
    max_depth = max(1, min(max_depth, MAX_PATH_DEPTH))
    ends = {
        e.entity_id: e
        for e in (
            await sess.scalars(select(Entity).where(Entity.entity_id.in_([source, target])))
        ).all()
    }
    for end in (source, target):
        if end not in ends:
            raise NodeNotFound(end)

    empty = PathRead(
        source=source,
        target=target,
        max_depth=max_depth,
        found=False,
        hops=None,
        nodes=[],
        edges=[],
    )
    if source == target:
        stats = await _stats(sess, [source])
        homes = await _home_topics(sess, [ends[source]])
        node = _node_read(ends[source], "focus", stats[source], homes[source])
        return PathRead(
            source=source,
            target=target,
            max_depth=max_depth,
            found=True,
            hops=0,
            nodes=[node],
            edges=[],
        )

    parent: dict[int, tuple[int, Edge]] = {}
    seen = {source}
    frontier = [source]
    found = False
    for _ in range(max_depth):
        if not frontier or len(seen) > MAX_PATH_VISITED:
            break
        level = (
            await sess.scalars(
                select(Edge)
                .where(or_(Edge.from_node.in_(frontier), Edge.to_node.in_(frontier)))
                .where(Edge.from_node != Edge.to_node)
            )
        ).all()
        level = sorted(level, key=lambda e: (-len(set(e.supporting_chunk_ids or ())), e.edge_id))
        on_frontier = set(frontier)
        reached: dict[int, tuple[int, Edge]] = {}
        for edge in level:
            for here, there in ((edge.from_node, edge.to_node), (edge.to_node, edge.from_node)):
                if here in on_frontier and there not in seen and there not in reached:
                    reached[there] = (here, edge)
        if reached:
            merged = set(
                (
                    await sess.scalars(
                        select(Entity.entity_id).where(
                            Entity.entity_id.in_(list(reached)), Entity.redirects_to.is_not(None)
                        )
                    )
                ).all()
            )
            for node_id in merged:
                reached.pop(node_id, None)
        parent.update(reached)
        seen.update(reached)
        if target in reached:
            found = True
            break
        frontier = list(reached)

    if not found:
        return empty

    route = [target]
    edges: list[Edge] = []
    while route[-1] != source:
        previous, edge = parent[route[-1]]
        edges.append(edge)
        route.append(previous)
    route.reverse()
    edges.reverse()

    entities = {
        e.entity_id: e
        for e in (await sess.scalars(select(Entity).where(Entity.entity_id.in_(route)))).all()
    }
    stats = await _stats(sess, route)
    homes = await _home_topics(sess, entities.values())
    nodes = [
        _node_read(
            entities[node_id],
            "focus" if node_id == source else "neighbour",
            stats[node_id],
            homes[node_id],
            contested=any(is_contested(e) for e in edges if node_id in (e.from_node, e.to_node))
            and node_id != source,
        )
        for node_id in route
    ]
    return PathRead(
        source=source,
        target=target,
        max_depth=max_depth,
        found=True,
        hops=len(edges),
        nodes=nodes,
        edges=[_edge_read(edge, "focus") for edge in edges],
    )
