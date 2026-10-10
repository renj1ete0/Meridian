"""Possible duplicates, as Admin decides them (task `B-202`).

See docs/features/knowledge-graph.md#deciding-a-possible-duplicate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DuplicatePassageRead(BaseModel):
    """One passage behind a node, so the two can be compared on evidence, not name."""

    chunk_id: int
    source_id: int
    title: str | None
    text: str


class DuplicateSideRead(BaseModel):
    entity_id: int
    canonical_name: str
    node_type: str
    jurisdiction: str | None
    aliases: list[str]
    description: str | None
    #: Stated links touching it, either way.
    links: int
    passages: list[DuplicatePassageRead]


class DuplicatePairRead(BaseModel):
    notification_id: int
    #: The name a run read and could not place.
    mention: str
    #: The node the run created rather than risk a bad merge.
    created: DuplicateSideRead
    #: The node it might be.
    candidate: DuplicateSideRead
    created_at: str


class DuplicatesRead(BaseModel):
    pairs: list[DuplicatePairRead]
    #: Undecided pairs in all, of which `pairs` is the first page.
    total: int


class DuplicateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["merge", "keep"]


class DuplicateDecisionRead(BaseModel):
    notification_id: int
    decision: Literal["merged", "kept apart", "reopened"]
    #: The merge to reverse, when one was made.
    merge_id: int | None = None
