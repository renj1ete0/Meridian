"""DTOs for saved views (task P6-09, spec §12.5)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class SavedViewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    view_id: int
    name: str
    query: str | None
    filters: dict
    focus_entity_id: int | None
    note: str | None
    last_opened_at: dt.datetime | None
    created_at: dt.datetime


class SavedViewCreate(BaseModel):
    """What a reader saves.

    `filters` is free-form because it mirrors `SearchFilters`, which grows with
    §12.3's canvas filters — but it is *validated against* that model on the way
    in, so a view cannot store a filter the search cannot apply. A view that
    silently drops a filter when reopened is worse than one that refuses to save.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    query: str | None = None
    filters: dict = Field(default_factory=dict)
    focus_entity_id: int | None = None
    note: str | None = None


class SavedViewEdit(BaseModel):
    """Rename, re-note, or overwrite what a view points at."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    query: str | None = None
    filters: dict | None = None
    focus_entity_id: int | None = None
    note: str | None = None


class SavedViewsRead(BaseModel):
    views: list[SavedViewRead]
