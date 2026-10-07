"""DTOs for a route across claims and resemblance (task P6-32).

Every hop is `cited` (a passage states the link) or `similar` (the ends only read
alike), counted apart, with the claims-only answer carried beside the mixed one so
Gaps (`P6-36`) can read it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .search import SearchHitRead

HopKind = Literal["cited", "similar"]

#: What a stop is. `term` is text that names no node and joins the graph only
#: by resemblance; `area` is reserved for the corpus areas of `P6-30`, which
#: plug in as another source of hops.
StopKind = Literal["entity", "term", "area"]

#: Which kinds of hop a search may take.
Allow = Literal["cited", "cited_and_similar"]


class StopRead(BaseModel):
    kind: StopKind
    #: The entity or area id; null for a term.
    id: int | None
    name: str
    #: The entity's node type; null for a term or an area.
    node_type: str | None = None


class HopRead(BaseModel):
    """One step of a route, labelled with the kind of evidence behind it."""

    kind: HopKind
    #: Indexes into `RouteRead.stops`: this hop joins stops[i] to stops[i+1].
    index: int

    #: Cited hops: the edge, its relation, and whether it runs along the
    #: route (stops[i] is the stated subject) or against it.
    edge_id: int | None = None
    relation_type: str | None = None
    forward: bool | None = None
    #: Distinct passages behind the edge. Never zero on a cited hop.
    support: int | None = None
    contested: bool = False
    #: The first passage behind the edge, so the claim can be read in place.
    evidence: SearchHitRead | None = None

    #: Similar hops: the cosine of the two vectors. Null on a cited hop.
    similarity: float | None = None


class RouteSummaryRead(BaseModel):
    """The shortest route of claims alone, in brief."""

    found: bool
    hops: int | None
    #: Why no search could run, when that is the answer: for instance an end
    #: that names no node cannot be reached by claims at all.
    reason: str | None = None


class RouteRead(BaseModel):
    source: StopRead
    target: StopRead
    max_depth: int
    allow: Allow

    found: bool
    #: Number of hops on the route; null when none was found.
    hops: int | None
    stops: list[StopRead]
    route: list[HopRead]
    cited_hops: int
    similar_hops: int

    #: The answer from claims alone, always computed. `found` false here is
    #: the finding "no cited route within `max_depth` hops".
    cited_only: RouteSummaryRead
    #: The search stopped because it reached its work bound, not because the
    #: graph ran out; "not found" is then weaker than it looks.
    truncated: bool
    #: The cosine floor a similar hop had to clear.
    similar_floor: float
