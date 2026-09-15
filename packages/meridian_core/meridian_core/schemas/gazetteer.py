"""DTOs for the gazetteer (mirrors ``meridian_core.models.gazetteer``, §5.6)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .common import CreateBase
from .enums import GazetteerEntityType, GazetteerSource


class GazetteerTermCreate(CreateBase):
    canonical: str = Field(min_length=1)
    aliases: list[str] | None = None
    entity_type: GazetteerEntityType
    jurisdiction: str | None = None
    ambiguous: bool = False
    topic_labels: list[str] | None = None
    source: GazetteerSource = "manual"
    approved: bool = False


class GazetteerTermRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term_id: int
    canonical: str
    aliases: list[str] | None
    entity_type: GazetteerEntityType
    jurisdiction: str | None
    ambiguous: bool
    topic_labels: list[str] | None
    source: GazetteerSource
    approved: bool
    occurrence_count: int
    #: Set when somebody turned this term down (`P6-13`). Distinct from
    #: `approved=false` with no timestamp, which means nobody has looked yet —
    #: and the harvest reads the difference, so a rejected term is not re-created
    #: by the next document that defines it.
    rejected_at: dt.datetime | None = None
    created_at: dt.datetime
