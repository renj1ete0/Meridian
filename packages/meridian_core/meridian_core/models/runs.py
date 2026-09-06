"""Run state, enrichment, and report jobs (spec §11.10, §6.6, §11.13).

Orchestrator state is a plain table, not a workflow framework. A crash at
``stage='tagging'`` resumes there on the next wake, and combined with the rule
that the high-water mark advances only after writes commit (§6.3), that gives
full resumability without an abstraction layer between the orchestrator and its
validated tool calls.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

RUN_STAGE = constrained(
    "pull", "extract", "tag", "score", "analogies", "gap", "seed", "done", name="run_stage"
)
RUN_STATUS = constrained("running", "done", "failed", "deferred", name="run_status")

JOB_STATUS = constrained("pending", "queued", "running", "done", "failed", name="job_status")

ENRICHMENT_TYPE = constrained(
    "figure_vlm", "ocr_quality", "chart_ocr", name="enrichment_item_type"
)

NOTIFICATION_TYPE = constrained(
    "run_summary",
    "job_complete",
    "alert",
    "seed_proposal",
    "gazetteer_proposal",
    "merge_adjudication",
    name="notification_type",
)


class Run(Base):
    """One synthesis pass (§11.10)."""

    __tablename__ = "runs"

    run_id: Mapped[int] = pk()

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str | None] = mapped_column(RUN_STAGE)
    status: Mapped[str] = mapped_column(RUN_STATUS, nullable=False, default="running")

    # The high-water mark. Advanced only after writes commit (§6.3).
    last_chunk_id: Mapped[int | None] = mapped_column(BigInteger)

    agent_id: Mapped[str | None] = mapped_column(Text)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Logged per run so a 20% week-on-week rise is visible well before the
    # monthly ceiling is reached (§11.9).
    cost_usd: Mapped[float | None] = mapped_column(Float)

    edges_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tags_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seeds_emitted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Run {self.run_id} {self.stage} {self.status}>"


class EnrichmentItem(Base, TimestampMixin):
    """Deferred, expensive, optional work — never automatic (§6.6).

    VLM figure descriptions and quality-tier OCR run only when explicitly
    triggered, which keeps the pipeline autonomous for acquisition while leaving
    the costly passes under deliberate control.
    """

    __tablename__ = "enrichment_queue"

    item_id: Mapped[int] = pk()

    item_type: Mapped[str] = mapped_column(ENRICHMENT_TYPE, nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(JOB_STATUS, nullable=False, default="pending")

    requested_by: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_enrichment_status_type", "status", "item_type"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EnrichmentItem {self.item_id} {self.item_type} {self.status}>"


class Report(Base):
    """A user-triggered drafting job (§11.13).

    Always bound to a scope — a saved view, a coverage cell, the contested list,
    or a node pair — plus a question. ``coverage_snapshot`` records what the
    evidence looked like at submit time, so a draft written over thin coverage
    stays auditable afterwards rather than reading as confident prose.
    """

    __tablename__ = "reports"

    job_id: Mapped[int] = pk()

    scope: Mapped[dict | None] = mapped_column(JSONB)
    question: Mapped[str | None] = mapped_column(Text)
    coverage_snapshot: Mapped[dict | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(JOB_STATUS, nullable=False, default="queued")
    output_path: Mapped[str | None] = mapped_column(Text)

    produced_by: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    quality_tier: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Report {self.job_id} {self.status}>"


class Notification(Base, TimestampMixin):
    """In-app counterpart to the Telegram digest (§12.5, §13.3).

    Filterable by type rather than by read state — the useful question is "what
    finished" or "what needs a decision", not "what have I glanced at".
    """

    __tablename__ = "notifications"

    notification_id: Mapped[int] = pk()

    notification_type: Mapped[str] = mapped_column(NOTIFICATION_TYPE, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)

    # Where acting on it happens, so the panel can route to Explore or Admin.
    surface: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Notification {self.notification_id} {self.notification_type}>"
