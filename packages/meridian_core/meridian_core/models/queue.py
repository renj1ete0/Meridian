"""The crawl queue (spec §5.1).

Status flow::

    pending → fetched → extracted → embedded → done
                     ↘ failed
                     ↘ rejected_duplicate     (novelty gate, §6.1)

The queue is also the decoupling point between planes: the worker and the
orchestrator never call each other, they only leave rows here (§2 principle 2).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

TASK_STATUS = constrained(
    "pending",
    "fetched",
    "extracted",
    "embedded",
    "done",
    "failed",
    "rejected_duplicate",
    name="task_status",
)

TASK_TYPE = constrained("url", "query", "doi", "sitemap", name="task_type")

# Who put this in the queue. Frontier expansion is model-independent and
# accounts for most of the queue; model-emitted seeds are capped per run (§11.4).
SEED_SOURCE = constrained("frontier", "model", "user", "diversity", name="seed_source")


class QueueTask(Base, TimestampMixin):
    __tablename__ = "queue"

    task_id: Mapped[int] = pk()

    url_or_query: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(TASK_TYPE, nullable=False, default="url")
    status: Mapped[str] = mapped_column(TASK_STATUS, nullable=False, default="pending")

    # Higher runs first. Seeds drawn proportionally to the topic weight vector (§10).
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic: Mapped[str | None] = mapped_column(Text, index=True)
    seed_source: Mapped[str] = mapped_column(SEED_SOURCE, nullable=False, default="frontier")

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # The claim query: highest-priority pending task, oldest first.
        Index("ix_queue_claim", "status", "priority", "created_at"),
        # Cheap already-seen check before enqueuing (§6.4 prefetch filtering).
        Index("ix_queue_url_or_query", "url_or_query"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<QueueTask {self.task_id} {self.status} {self.url_or_query[:60]!r}>"
