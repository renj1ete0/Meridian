"""DTOs for the answer page (mirrors ``meridian_core.answer``).

``tests/unit/test_answer.py`` compares these to the dataclasses field by field,
and ``web/tests/api.test.ts`` compares the client's field lists to these.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import SearchArm, SourceTier


class AnswerItemRead(BaseModel):
    """One source, shown by its best-matching passage."""

    model_config = ConfigDict(from_attributes=True)

    source_id: int
    chunk_id: int
    title: str | None
    url: str
    #: The registrable domain: what "different publishers" is counted by.
    publisher: str
    source_tier: SourceTier
    publication_date: dt.date | None
    text: str
    score: float
    #: How many of this source's passages matched.
    passages: int


class AnswerGroupRead(BaseModel):
    """The evidence about one country, or about none (``code`` null)."""

    model_config = ConfigDict(from_attributes=True)

    code: str | None
    name: str
    sources: int
    publishers: int
    tier_mix: dict[str, int]
    newest: dt.date | None
    coverage: Literal["strong", "thin"]
    items: list[AnswerItemRead]
    #: For the unplaced group: sources never examined for places.
    unexamined: int = 0


class AnswerRead(BaseModel):
    """A question answered as grouped evidence. No text here is generated."""

    query: str
    groups: list[AnswerGroupRead] = Field(default_factory=list)
    unplaced: AnswerGroupRead | None = None
    #: The coverage rule, in words the page shows beside the verdicts.
    coverage_rule: str
    strong_min_publishers: int
    strong_needs_tiers: list[SourceTier]

    #: The topics most matching sources carry, most first — what a "find
    #: more" search is filed under when the reader chose none.
    topics: list[str] = Field(default_factory=list)

    #: What the grouping was done over.
    passages_considered: int
    sources_considered: int
    candidate_pool: int
    arms: list[SearchArm]
    degraded: bool
    degraded_reason: str | None = None
