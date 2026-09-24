"""DTOs for the Gaps list and its actions (task P6-36; mirrors ``meridian_core.gaps``)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ActionKind = Literal["seed_query", "boost_topic", "open_search"]
SourceState = Literal["ok", "unavailable", "pending"]


class GapActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: ActionKind
    label: str
    topic: str | None = None
    query: str | None = None
    factor: float | None = None
    days: int | None = None


class GapRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    kind: str
    subject: str
    title: str
    reason: str
    severity: float
    evidence: dict[str, Any]
    actions: list[GapActionRead]


class GapSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    status: SourceState
    note: str | None = None
    gaps: int = 0


class GapsRead(BaseModel):
    gaps: list[GapRead]
    sources: list[GapSourceRead]
    computed_at: dt.datetime


class GapSeed(BaseModel):
    """Queue a search query for a topic, from a gap."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=3, max_length=200)
    gap_id: str = Field(min_length=1, max_length=200)

    @field_validator("query")
    @classmethod
    def _one_line(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 3:
            raise ValueError("a query needs at least three characters of words")
        if "://" in cleaned:
            raise ValueError("a search seed is words, not a URL; add URLs as seeds in Admin")
        return cleaned


class GapBoost(BaseModel):
    """Boost a topic temporarily, from a gap. Expires by itself (§10)."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=100)
    factor: float = Field(gt=1.0, le=5.0)
    days: int = Field(ge=1, le=60)
    gap_id: str = Field(min_length=1, max_length=200)


class GapActionResult(BaseModel):
    """What was done, and how to undo it."""

    kind: Literal["seed_query", "boost_topic"]
    topic: str
    detail: str
    undo: str
    task_id: int | None = None
    expires_at: dt.datetime | None = None
