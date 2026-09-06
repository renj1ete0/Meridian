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
    topic_labels: list[str] | None = None
    source: GazetteerSource = "manual"
    approved: bool = False


class GazetteerTermRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term_id: int
    canonical: str
    aliases: list[str] | None
    entity_type: GazetteerEntityType
    topic_labels: list[str] | None
    source: GazetteerSource
    approved: bool
    occurrence_count: int
    created_at: dt.datetime
