"""DTOs for the graph workspace's read path (tasks P6-01, P6-02, P6-03, §12.2).

§12.3 asks for one normalised subgraph that several renderers consume, so these
are shaped for *the neighbourhood*, not for any one view: the node-link canvas
and the table both read the same `NeighbourhoodRead`, and a view that wanted a
different shape would be a second query that can disagree with the first.

Everything here is derived from the relational tables — `entities`, `edges`,
`attribute_values`, `chunks`, `sources` — which are the graph's source of
truth (see `models/graph.py`). Nothing is computed that the tables do not hold:
where a quantity has no column (an edge has no stored *weight*), the value
returned is named for what it actually counts (`support`, passages), so the
interface cannot present an invented number as a measured one.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .annotations import AnnotationRead
from .enums import Certainty, NodeType, SourceTier, Stance
from .graph import EntityRead
from .search import NodeAttributeRead, SearchHitRead

#: Where a node sits relative to the focus. `hint` is a second-hop node drawn
#: only as a faint dot (design-system.md §2, "Edge, second hop (hint)") — it
#: says "there is more past here" without rendering depth 2, which §12.2 rules
#: out.
NodeRole = Literal["focus", "neighbour", "hint"]

#: How an edge relates to the focus, which is what decides how it is drawn.
EdgeKind = Literal["focus", "between", "hint"]


class GraphFilters(BaseModel):
    """§12.2's live filters, applied on the server (task P6-02).

    **Filters act on evidence, not on labels.** An edge survives when at least
    one passage behind it satisfies every evidence filter at once — a tier *and*
    a date *and* a topic — because "peer-reviewed evidence from 2020 onwards" is
    a claim about one passage, and letting an edge pass on a 2020 press item
    plus a 2012 journal article would answer a question nobody asked.
    """

    model_config = ConfigDict(extra="forbid")

    topics: list[str] = Field(default_factory=list)
    tiers: list[SourceTier] = Field(default_factory=list)
    published_from: dt.date | None = None
    published_to: dt.date | None = None
    contested_only: bool = False
    #: A neighbour must carry this attribute (§7) to be shown.
    attribute: str | None = None

    def narrows_evidence(self) -> bool:
        return bool(self.topics or self.tiers or self.published_from or self.published_to)


class GraphNodeRead(BaseModel):
    """One node as the canvas draws it and the hover card describes it."""

    entity_id: int
    canonical_name: str
    node_type: NodeType
    jurisdiction: str | None
    is_annotation: bool
    role: NodeRole

    #: The node's own topics: its `topic_labels`, or, when it has none, the
    #: topics of the sources its own supporting chunks came from. What decides
    #: `cross_topic`.
    home_topics: list[str]
    #: Every topic the node's evidence touches: home topics plus the topics of
    #: the sources behind its edges. What the hover card counts.
    topics: list[str]

    #: One of this node's edges to the focus is contested (§9). Always false
    #: for the focus itself — see `NeighbourhoodRead.focus_contested`.
    contested: bool = False
    #: Both this node and the focus have home topics, and they do not overlap.
    #: This is §12.2's "where cross-topic links surface".
    cross_topic: bool = False

    #: Distinct passages behind the edges joining this node to the focus. What
    #: neighbours are ranked by; zero for the focus and for hints.
    support: int = 0
    #: Edges incident on this node anywhere in the graph, not only here.
    degree: int = 0
    #: Distinct sources behind those edges, and the newest publication date
    #: among them. Null when no source carries a date.
    sources: int = 0
    newest: dt.date | None = None


class GraphEdgeRead(BaseModel):
    edge_id: int
    from_node: int
    to_node: int
    relation_type: str
    kind: EdgeKind
    confidence: float | None
    stance: Stance | None
    certainty: Certainty | None
    #: Distinct supporting chunks. Never zero: an edge without provenance is
    #: not assertable (§2 principle 3) and the column is NOT NULL.
    support: int
    contested: bool
    contested_with: list[int] = Field(default_factory=list)


class FacetCount(BaseModel):
    value: str
    #: Neighbours of the focus that this value would keep, before any filter.
    count: int


class GraphFacetsRead(BaseModel):
    """Counts for the filter rail, over the *unfiltered* neighbourhood.

    Unfiltered on purpose: a rail whose counts shrank as boxes were ticked
    would hide the option that brings a neighbour back, which is the one the
    reader is looking for.
    """

    topics: list[FacetCount]
    tiers: list[FacetCount]
    attributes: list[FacetCount]
    #: Neighbours joined to the focus by at least one contested edge.
    contested: int
    #: The span of publication dates behind the neighbourhood's evidence, so
    #: the date slider ranges over what exists rather than over an invented
    #: window. Both null when no passage behind it carries a date.
    published_min: dt.date | None
    published_max: dt.date | None


class NeighbourhoodRead(BaseModel):
    """§12.2's focus + expand, as one normalised subgraph (§12.3).

    `total` is the count *after* filters and `shown` what the cap let through,
    so the interface can say "11 of 34 neighbours shown" rather than letting a
    capped view pass for the whole neighbourhood.
    """

    focus: GraphNodeRead
    #: The focus has at least one contested edge anywhere in the graph.
    focus_contested: bool
    #: Set when the focus was merged into another entity (§5.5). The client
    #: follows it rather than drawing a node that no longer stands alone.
    redirects_to: int | None

    nodes: list[GraphNodeRead]
    edges: list[GraphEdgeRead]

    total: int
    shown: int
    unfiltered: int
    limit: int
    ranked_by: Literal["support"] = "support"

    filters: GraphFilters
    facets: GraphFacetsRead


class EvidenceRead(BaseModel):
    """A passage behind this node, with what it was cited for.

    A search hit's shape plus the claim it supports. `certainty` and `stance`
    belong to the *edge* that cites the chunk (§8: observable properties, not
    verdicts), so a chunk cited only by an attribute carries neither — and says
    nothing rather than a default that would read as a measurement.
    """

    hit: SearchHitRead
    via: Literal["attribute", "edge", "node"]
    relation_type: str | None = None
    other_entity_id: int | None = None
    other_name: str | None = None
    certainty: Certainty | None = None
    stance: Stance | None = None


class ContestedSideRead(BaseModel):
    edge_id: int
    relation_type: str
    from_entity_id: int
    from_name: str
    to_entity_id: int
    to_name: str
    certainty: Certainty | None
    stance: Stance | None
    #: The first passage behind the edge, hydrated. Null only if every chunk it
    #: names has gone, which the sweep never does to a cited chunk.
    evidence: SearchHitRead | None


class ContestedPairRead(BaseModel):
    """One of this node's edges and an edge §9 marked as disagreeing with it.

    Both sides are returned because a contradiction is a result (§9, voice 03),
    and showing only the other side would frame this node's edge as the one
    that is right.
    """

    ours: ContestedSideRead
    theirs: ContestedSideRead


class GraphNodeDetailRead(BaseModel):
    """The node panel beside the canvas (§12.5, design `Explore`)."""

    entity: EntityRead
    home_topics: list[str]
    contested: bool
    attributes: list[NodeAttributeRead]
    evidence: list[EvidenceRead]
    #: All passages behind the node, before the panel's cap.
    evidence_total: int
    contested_with: list[ContestedPairRead]
    annotations: list[AnnotationRead]


class NodeMatchRead(BaseModel):
    entity_id: int
    canonical_name: str
    node_type: NodeType
    jurisdiction: str | None
    is_annotation: bool
    #: Which alias matched, when it was not the canonical name — so a reader who
    #: typed an acronym sees why the full name came back.
    matched_alias: str | None
    degree: int


class NodeSearchRead(BaseModel):
    query: str
    matches: list[NodeMatchRead]


class PathRead(BaseModel):
    """§12.2's path mode (task P6-03): one shortest route between two nodes.

    `found` false with empty lists is an answer — "no route within N hops" —
    and not an error: two nodes with nothing between them is a gap, and a gap
    is a finding (design-system.md §4, voice 01).
    """

    source: int
    target: int
    max_depth: int
    found: bool
    hops: int | None
    nodes: list[GraphNodeRead]
    edges: list[GraphEdgeRead]
