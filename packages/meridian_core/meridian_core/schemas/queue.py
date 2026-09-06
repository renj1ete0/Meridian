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
    # Operational state, surfaced so the Admin queue view can show why a task is
    # waiting — a backoff window and a held lease look identical otherwise.
    next_attempt_at: dt.datetime | None
    claimed_at: dt.datetime | None
    claimed_by: str | None
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


class FetchHealth(BaseModel):
    """The fetch half of the daily health line (§12.5), over a time window.

    Derived from ``fetch_attempts``, never from a running counter: a counter
    that resets on success cannot say what the rate *was*, which is the question
    "is the crawler still working" actually reduces to.
    """

    window_hours: int = Field(gt=0)
    domain: str | None = None  # None = every domain

    attempts: int = Field(ge=0)
    successes: int = Field(ge=0)
    # None when nothing was attempted in the window. 0.0 would report a crawler
    # that fetched nothing as a crawler where everything failed.
    success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    by_outcome: dict[FetchOutcome, int] = Field(default_factory=dict)
