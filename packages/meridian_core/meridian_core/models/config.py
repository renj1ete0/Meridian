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

from sqlalchemy import DateTime, Float, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

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

    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FetchPolicy {self.domain} {self.status}>"


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
