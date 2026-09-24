"""DTOs for a term's neighbourhood (task P6-33).

Cited and similar are separate lists of separate types, never one list with a
flag: a consumer that renders one list cannot then draw resemblance as a claim
by forgetting to read the flag.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .enums import NodeType
from .search import SearchHitRead


class TermRead(BaseModel):
    entity_id: int
    canonical_name: str
    node_type: NodeType


class RelationRead(BaseModel):
    relation_type: str
    #: True when the anchor is the stated subject (anchor → other).
    outgoing: bool


class CitedTermRead(TermRead):
    """Inner ring: a passage states a link between the anchor and this node."""

    #: Distinct passages behind the link(s). Never zero — a link without a
    #: passage is not assertable (§2 principle 3).
    support: int
    relations: list[RelationRead]
    #: One of the links is marked as disagreeing with another (§9).
    contested: bool


class SimilarTermRead(TermRead):
    """Outer ring: near in meaning. No passage states a link."""

    #: Cosine similarity of the two name vectors.
    similarity: float


class SimilarPassageRead(BaseModel):
    """A passage near the anchor in meaning. It need not mention it."""

    hit: SearchHitRead
    similarity: float


SimilarBasis = Literal["node", "term", "none"]


class TermNeighbourhoodRead(BaseModel):
    """Both rings for one term (task P6-33).

    `anchor` null means no node is named by the term, so the inner ring is
    empty because there is nothing to have links — a different fact from a
    node with no links yet, which is `anchor` set and `cited_total` zero.
    """

    term: str
    anchor: TermRead | None
    #: Nodes whose names contain the term, offered when none equals it.
    candidates: list[TermRead]

    cited: list[CitedTermRead]
    cited_total: int

    similar: list[SimilarTermRead]
    similar_total: int
    passages: list[SimilarPassageRead]
    #: Whose vector the outer ring was measured from: the anchor's name, the
    #: term as typed, or nothing — then the outer ring was not computed at all,
    #: and an empty list must not read as "nothing is similar".
    similar_basis: SimilarBasis
    similar_floor: float
    passage_floor: float
