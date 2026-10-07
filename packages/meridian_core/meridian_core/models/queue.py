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

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, text
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

# Who put this in the queue (§5.2). Model-emitted seeds are capped per run (§11.4).
# See docs/reference/data-model.md#queue.
SEED_SOURCE = constrained(
    "frontier",
    "sitemap",
    "search",
    "citation",
    "doi",
    "model",
    "user",
    "diversity",
    name="seed_source",
)

#: Which of §7.4's five diversity mechanisms wrote a `diversity` query (task
#: `P5-05`). NULL for every other row, and for a query that only widens a topic.
SEED_MECHANISMS = (
    "counter_seed",  # 1: counter-phrasings ("criticism of …")
    "tier_imbalance",  # 2: a node evidenced by one tier, asked for the others
    "naive_phrasing",  # 3: an outsider's words for a topic
    "distant_walk",  # 4: a node far from where the crawl has been
    "non_english",  # 5: a concept in another language's own words
)
SEED_MECHANISM = constrained(*SEED_MECHANISMS, name="seed_mechanism")


class QueueTask(Base, TimestampMixin):
    __tablename__ = "queue"

    task_id: Mapped[int] = pk()

    url_or_query: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(
        TASK_TYPE, nullable=False, default="url", server_default="url"
    )
    status: Mapped[str] = mapped_column(
        TASK_STATUS, nullable=False, default="pending", server_default="pending"
    )

    # Higher runs first. Seeds drawn proportionally to the topic weight vector (§10).
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    topic: Mapped[str | None] = mapped_column(Text, index=True)
    seed_source: Mapped[str] = mapped_column(
        SEED_SOURCE, nullable=False, default="frontier", server_default="frontier"
    )
    seed_mechanism: Mapped[str | None] = mapped_column(SEED_MECHANISM)

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # Backoff. A failed task is not eligible again until this passes, so a dead
    # domain stops spinning the queue (§13.4) without being removed from it.
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # A lease, not a status: an expired claim is simply reclaimable (§13.4).
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    claimed_by: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    #: For a `query` task, how many result URLs came back and how many were new to
    #: the queue (`B-56`). NULL until answered.
    search_results: Mapped[int | None] = mapped_column(Integer)
    search_queued: Mapped[int | None] = mapped_column(Integer)
    #: For a `doi` task, the page whose references named it (`B-58`): a ranking
    #: input, so it is set NULL rather than blocking a source's deletion.
    parent_source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sources.source_id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        # The claim query: eligible tasks, highest priority first, oldest first.
        Index("ix_queue_claim", "status", "priority", "created_at", "next_attempt_at"),
        # Cheap already-seen check before enqueuing (§6.4 prefetch filtering).
        Index("ix_queue_url_or_query", "url_or_query"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<QueueTask {self.task_id} {self.status} {self.url_or_query[:60]!r}>"


# Why the attempt ended. Recorded rather than inferred from a status code,
# because "blocked" and "robots_denied" are policy outcomes with no HTTP status,
# and the daily health line needs to tell them apart from a genuine 500.
FETCH_OUTCOME = constrained(
    "success",
    "not_modified",  # conditional request paid off; nothing refetched
    "http_error",
    "timeout",
    "too_large",
    "robots_denied",
    # robots.txt could not be read, so the origin is refused until it can be.
    # Not `robots_denied`: that is the site's answer and is final, this is the
    # absence of one — a DNS failure at boot must not drop a URL for good.
    "robots_unreachable",
    "blocked",  # domain marked blocked by policy before the request went out
    "connection_error",
    "parse_error",
    # The safeguards in worker/fetch.py, each its own outcome rather than "blocked",
    # because each wants a different response.
    "unsafe_target",  # netguard refused the address, scheme or redirect hop
    "content_type_rejected",
    "decompression_bomb",
    "too_many_redirects",
    name="fetch_outcome",
)


class FetchAttempt(Base):
    """One record per fetch attempt, successful or not.

    High volume by design, pruned on a retention window. See
    docs/reference/data-model.md#fetch-attempts.
    """

    __tablename__ = "fetch_attempts"

    attempt_id: Mapped[int] = pk()

    # Nullable: the task may be deleted or the attempt may predate one (a robots
    # or blocklist rejection happens before anything is claimed).
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("queue.task_id", ondelete="SET NULL"), index=True
    )

    # Denormalised so per-domain rates survive the task being pruned.
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    attempted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    outcome: Mapped[str] = mapped_column(FETCH_OUTCOME, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error_detail: Mapped[str | None] = mapped_column(Text)

    duration_ms: Mapped[int | None] = mapped_column(Integer)
    bytes_fetched: Mapped[int | None] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        # "success rate over the last N hours", the health line's core query.
        Index("ix_fetch_attempts_outcome_time", "outcome", "attempted_at"),
        # "what is this domain doing", for the blocked-domain decision and for
        # noticing a site that started failing without tripping the counter.
        Index("ix_fetch_attempts_domain_time", "domain", "attempted_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FetchAttempt {self.attempt_id} {self.outcome} {self.domain}>"
