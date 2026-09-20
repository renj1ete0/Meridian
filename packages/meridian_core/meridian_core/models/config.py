"""Configuration that lives in the database, not in files (spec §13.1).

The YAML in ``config/`` seeds these tables once at first boot and is not read
again. Nothing routine should require editing a file on the host — topic
weights, fetch policy, and model routing are all UI or MCP actions.

Credentials are the deliberate exception: the agent registry stores the *name*
of the environment variable to read, never the value. The database is
snapshotted off-device for backup, and keys would travel with every snapshot
(§11.11).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TRUST_STATE, TimestampMixin, constrained, pk

TOPIC_STATUS = constrained("active", "maintenance", "paused", "archived", name="topic_status")
DOMAIN_STATUS = constrained("active", "blocked", "paused", name="domain_status")
TOKEN_SCOPE = constrained("read", "read_write", name="token_scope")
AVAILABILITY = constrained("always", "on_demand", "opportunistic", name="agent_availability")



class TopicConfig(Base):
    """Attention as a weight vector over topics; seeds drawn proportionally (§10).

    Steering rewrites the vector and never deletes, so returning to a topic costs
    nothing — no rebuild, no re-crawl. That applies to whole topics too:
    ``archived`` releases the topic's share of the pool and stops seeding, but
    leaves every node, edge, and tag it produced untouched (§10.2).
    """

    __tablename__ = "topic_config"

    topic: Mapped[str] = mapped_column(Text, primary_key=True)

    weight: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0.0")
    )
    floor: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.05, server_default=text("0.05")
    )
    ceiling: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1.0")
    )

    # Decay handled by expiry, not by anyone remembering to undo it.
    boost_factor: Mapped[float | None] = mapped_column(Float)
    boost_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # Autonomous adjustment may not touch a pinned topic.
    pinned: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        TOPIC_STATUS, nullable=False, default="active", server_default="active"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TopicConfig {self.topic} w={self.weight} {self.status}>"


class SteeringLog(Base):
    """Why a weight is where it is (§10.1).

    Not optional. With two writers — the user and the orchestrator — the
    alternative is opening the UI in a month with no idea what changed anything.
    """

    __tablename__ = "steering_log"

    log_id: Mapped[int] = pk()

    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)  # user | orchestrator
    topic: Mapped[str | None] = mapped_column(Text, index=True)
    field: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SteeringLog {self.actor} {self.topic}.{self.field} -> {self.new_value!r}>"


class FetchPolicy(Base):
    """Per-domain fetch behaviour, merged over the global default (§6.4).

    Resolution order: per-domain row → the ``'*'`` global row → file defaults.
    Per-domain overrides matter because one setting for a large API and a small
    municipal server is wrong in one direction or the other.
    """

    __tablename__ = "fetch_policy"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)  # '*' = global default

    settings: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        DOMAIN_STATUS, nullable=False, default="active", server_default="active"
    )
    note: Mapped[str | None] = mapped_column(Text)

    # Otherwise one dead site consumes crawl budget for weeks unnoticed.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # --- what the crawl learned about this domain (task P1-27) ------------
    #
    # Learned state, not configuration. `render_js: auto` fetches statically and
    # re-fetches through the browser when the HTML is a shell, which is the
    # right order for a corpus of mostly-static pages — and has no memory, so a
    # JS-only domain pays both requests on every page forever. Those requests
    # queue in the same per-domain slot the pages do, so the cost is crawl
    # throughput on exactly the domains that are already slowest.

    #: *Consecutive* escalations, reset the moment a static fetch turns out to
    #: have been enough. The same shape as `consecutive_failures` above and for
    #: the same reason: a domain that changes behaviour should stop being
    #: treated as though it had not.
    render_js_escalations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    #: When the threshold was crossed. The learning **expires**, and without
    #: that this is a trap: a domain going straight to the browser never fetches
    #: statically again, so the counter cannot reset and a redesign can never be
    #: noticed.
    render_js_learned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # --- what screening concluded about this domain (task P4-14, §2.5) ----
    #
    # The verdict is cached *here*, at the domain, because screening is paid
    # once per domain and not once per page. A site with four thousand pages
    # does not get judged four thousand times, and — more to the point — a
    # domain cleared on Monday does not have page 3,001 quarantined on Friday
    # because that particular page happened to quote something.

    #: `unscreened` until something concludes otherwise. A cleared domain's
    #: pages are cleared; a quarantined domain's pages are held back from the
    #: slow loop until somebody or something judges it.
    trust_state: Mapped[str] = mapped_column(
        TRUST_STATE, nullable=False, default="unscreened", server_default="unscreened"
    )

    #: Consecutive fetches that the injection pre-screen did not flag. The same
    #: shape as `consecutive_failures` and `render_js_escalations`, and reset
    #: the same way: a domain that starts serving hostile pages should stop
    #: being treated as though it had not.
    clean_fetches: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    #: When the verdict was reached, and by what. `auto:tier` and `auto:clean`
    #: are the two the crawl can reach on its own; anything else is a judgement
    #: somebody or some model made, and §2 principle 3 wants it attributable.
    trust_decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    trust_decided_by: Mapped[str | None] = mapped_column(Text)

    #: Why, in one line, for a screen that has to explain a quarantine to the
    #: person deciding whether to clear it.
    trust_reason: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FetchPolicy {self.domain} {self.status} {self.trust_state}>"


class Agent(Base, TimestampMixin):
    """Agent registry and capability routing (§11.3).

    Swapping models is a config row, not a code change — which matters given how
    fast the options move. Fallback chains mean a provider outage degrades the
    run rather than halting it.
    """

    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    task_types: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    token_scope: Mapped[str] = mapped_column(
        TOKEN_SCOPE, nullable=False, default="read", server_default="read"
    )

    cost_tier: Mapped[str | None] = mapped_column(Text)
    # Ordinal, and deliberately distinct from cost_tier (§11.12).
    quality_tier: Mapped[int | None] = mapped_column(Integer)
    max_context: Mapped[int | None] = mapped_column(Integer)

    enabled: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    fallback_agent_id: Mapped[str | None] = mapped_column(Text)

    endpoint: Mapped[str | None] = mapped_column(Text)
    health_url: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[str] = mapped_column(
        AVAILABILITY, nullable=False, default="on_demand", server_default="on_demand"
    )
    wake_mac: Mapped[str | None] = mapped_column(Text)

    # The NAME of the environment variable holding the key — never the key.
    api_key_env_var: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_agents_enabled_quality", "enabled", "quality_tier"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Agent {self.agent_id} tier={self.quality_tier} enabled={self.enabled}>"


class AgentToken(Base, TimestampMixin):
    """Scoped credential per agent (§11.4).

    This is what makes it safe to point an ad-hoc chat session at the graph: an
    interactive session holds read-only scope, and only the orchestrator's own
    profile carries write tools.
    """

    __tablename__ = "agent_tokens"

    token_id: Mapped[int] = pk()

    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    allowed_tools: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    rate_limit: Mapped[int | None] = mapped_column(Integer)
    seed_cap_per_run: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentToken {self.token_id} agent={self.agent_id} revoked={self.revoked}>"


JOB_STATUS = constrained("ok", "failed", "timeout", name="job_run_status")


class BudgetConfig(Base, TimestampMixin):
    """The caps, and the fact that somebody set them (task `P4-10`, §16, §11.9).

    §16 lists runaway cost from the seed→ingest→cost feedback loop as
    *"manageable if caps are set before first autonomous run"* — a mitigation
    with an ordering requirement in it, and nothing enforced the ordering. This
    table is what "set" means, and `P4-13` is what refuses to start without it.

    **One row, enforced by the database.** A settings table that can hold two
    rows eventually holds two rows, and then "the budget" is whichever one the
    query happened to order first. The CHECK makes the second insert an error
    rather than a silent ambiguity, and makes `SELECT ... WHERE budget_id = 1`
    the only access pattern anyone can write.

    **Nullable caps mean unconfigured, not unlimited.** `reserve_seeds` already
    refuses a `None` cap for that reason, and the columns carry it through: a
    budget row that exists but leaves `max_seeds_per_run` empty has not been
    configured for seeds, and the run does not start. "Nobody decided" must
    never read as "no limit", because that is the shape in which a missing
    config becomes a bill.
    """

    __tablename__ = "budget_config"

    #: Always 1. The CHECK below is what makes that true rather than customary.
    budget_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    #: Per run. Both are the caps §11.9 names, and both are refused when unset.
    max_tokens_per_run: Mapped[int | None] = mapped_column(Integer)
    max_seeds_per_run: Mapped[int | None] = mapped_column(Integer)

    #: Across the calendar month, in USD. The ceiling the trend alerting in
    #: §11.9 is measured against — and the one a single run cannot exceed by
    #: itself, because a run is checked against the remainder before it starts.
    monthly_cost_ceiling_usd: Mapped[float | None] = mapped_column(Float)

    #: Who last changed it, for the same reason `steering_log` exists: a cap
    #: that moved is a decision, and decisions should be attributable.
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("budget_id = 1", name="single_row"),
        CheckConstraint(
            "max_tokens_per_run IS NULL OR max_tokens_per_run > 0",
            name="tokens_positive",
        ),
        CheckConstraint(
            "max_seeds_per_run IS NULL OR max_seeds_per_run > 0",
            name="seeds_positive",
        ),
        CheckConstraint(
            "monthly_cost_ceiling_usd IS NULL OR monthly_cost_ceiling_usd > 0",
            name="ceiling_positive",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<BudgetConfig tokens={self.max_tokens_per_run} "
            f"seeds={self.max_seeds_per_run} ceiling={self.monthly_cost_ceiling_usd}>"
        )


class ScheduledJob(Base, TimestampMixin):
    """One recurring job and when it next runs (task P5-06, spec §13.1).

    §13.1's corollary, stated outright: **"no cron files. The scheduler reads its
    timetable from the DB so schedule changes are a UI action."** A crontab on
    the box is a configuration nobody can see from the interface, cannot change
    without SSH, and does not travel with a database snapshot.

    **An interval rather than a cron expression.** Cron's grammar is expressive
    and needs a parser, and §13.2 wants these editable from a UI — where "every
    6 hours" is a number and `0 */6 * * *` is a support question. A job that
    must land at a particular time of day gets `next_run_at` set to that time
    once; the interval keeps it there.
    """

    __tablename__ = "scheduled_jobs"

    job_id: Mapped[int] = pk()

    #: Stable identifier, used in logs and by the UI. Unique so a seed can be
    #: re-run without duplicating the timetable.
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    #: The module to run, as `python -m <module>`. Not a shell string: a
    #: timetable row is editable from a UI, and a row that could name a shell
    #: command would make the schedule table a remote execution surface for
    #: anyone who could write to it.
    module: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"), nullable=False)

    #: When this is due. The queue's shape (`P1-01`): a timestamp the claimer
    #: compares against, rather than a status somebody has to flip.
    next_run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    #: A lease, not a status — same reasoning as `queue`. A scheduler that dies
    #: mid-job releases the job by expiry rather than leaving it claimed forever.
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(JOB_STATUS)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)
    #: Truncated. A job that fails by printing a stack trace should not make the
    #: timetable row unreadable in a UI that has to show it.
    last_error: Mapped[str | None] = mapped_column(Text)

    #: Consecutive failures, for backing a broken job off rather than running it
    #: on schedule forever.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        Index("ix_scheduled_jobs_due", "next_run_at", postgresql_where=text("enabled")),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ScheduledJob {self.name} every {self.interval_seconds}s>"
