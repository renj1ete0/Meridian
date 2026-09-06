"""DTOs for the crawl queue (mirrors ``meridian_core.models.queue``, §5.1).

One ``Create``/``Read`` pair. Status, attempts, and ``fetched_at`` are state-
machine fields the worker advances, not caller input (§5.1) — that asymmetry
is why the two classes are not identical rather than a reason to merge them.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .common import CreateBase
from .enums import FetchOutcome, SeedSource, TaskStatus, TaskType


class QueueTaskCreate(CreateBase):
    """What a caller submits to enqueue a fetch."""

    url_or_query: str = Field(min_length=1)
    task_type: TaskType = "url"
    priority: int = 0
    topic: str | None = None
    seed_source: SeedSource = "frontier"


class QueueTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: int
    url_or_query: str
    task_type: TaskType
    status: TaskStatus
    priority: int
    topic: str | None
    seed_source: SeedSource
    attempts: int
    fetched_at: dt.datetime | None
    error: str | None
    created_at: dt.datetime


class FetchAttemptRead(BaseModel):
    """One fetch attempt. Read-only — the worker writes these, nothing else does."""

    model_config = ConfigDict(from_attributes=True)

    attempt_id: int
    task_id: int | None
    domain: str
    url: str
    attempted_at: dt.datetime
    outcome: FetchOutcome
    status_code: int | None
    error_detail: str | None
    duration_ms: int | None
    bytes_fetched: int | None
    attempt_number: int
