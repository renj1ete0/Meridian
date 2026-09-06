"""DTOs for run state, enrichment jobs, reports, and notifications (mirrors
``meridian_core.models.runs``, §11.10, §6.6, §11.13).
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .common import CreateBase, QualityTier
from .enums import EnrichmentType, JobStatus, NotificationType, RunStage, RunStatus


class RunCreate(CreateBase):
    """Deliberately minimal. A run is resumable orchestrator state (models/
    runs.py module docstring, §6.3) advanced stage-by-stage by the
    orchestrator itself — stage, counters, and completion are not caller
    input at creation time."""

    started_at: dt.datetime | None = None
    agent_id: str | None = None


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    stage: RunStage | None
    status: RunStatus
    last_chunk_id: int | None
    agent_id: str | None
    tokens_used: int
    cost_usd: float | None
    edges_added: int
    tags_added: int
    seeds_emitted: int
    error: str | None


class EnrichmentItemCreate(CreateBase):
    """Deferred, expensive, and explicitly triggered — never automatic (§6.6)."""

    item_type: EnrichmentType
    target_id: int
    requested_by: str | None = None
    requested_at: dt.datetime | None = None


class EnrichmentItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    item_type: EnrichmentType
    target_id: int
    status: JobStatus
    requested_by: str | None
    requested_at: dt.datetime | None
    completed_at: dt.datetime | None
    error: str | None
    created_at: dt.datetime


class ReportCreate(CreateBase):
    """``coverage_snapshot`` is captured by the server at submit time (models/
    runs.py module docstring, §11.13), not supplied by the caller."""

    scope: dict | None = None
    question: str | None = None


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: int
    scope: dict | None
    question: str | None
    coverage_snapshot: dict | None
    status: JobStatus
    output_path: str | None
    produced_by: str | None
    model: str | None
    quality_tier: QualityTier | None
    created_at: dt.datetime | None
    completed_at: dt.datetime | None
    error: str | None


class NotificationCreate(CreateBase):
    notification_type: NotificationType
    title: str = Field(min_length=1)
    body: str | None = None
    payload: dict | None = None
    surface: str | None = None


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: int
    notification_type: NotificationType
    title: str
    body: str | None
    payload: dict | None
    surface: str | None
    read_at: dt.datetime | None
    created_at: dt.datetime
