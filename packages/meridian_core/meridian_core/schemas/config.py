"""DTOs for database-resident configuration (mirrors
``meridian_core.models.config``, §13.1, §10, §6.4, §11.3, §11.4).

``AgentToken`` (credential hashes) deliberately has no DTO here: it is never a
service boundary object, only ever read and written by the auth layer itself
against the ORM row directly.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .common import CreateBase, QualityTier
from .enums import AgentAvailability, DomainStatus, TokenScope, TopicStatus


class TopicConfigCreate(CreateBase):
    """``topic`` is the primary key and is supplied by the caller rather than
    generated, so this and ``TopicConfigRead`` end up sharing almost every
    field (§10) — the split still earns its keep once an update path needs to
    accept a partial payload while reads stay whole."""

    topic: str = Field(min_length=1)
    weight: float = 0.0
    floor: float = 0.05
    ceiling: float = 1.0
    boost_factor: float | None = None
    boost_expires_at: dt.datetime | None = None
    pinned: bool = False
    status: TopicStatus = "active"


class TopicConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    topic: str
    weight: float
    floor: float
    ceiling: float
    boost_factor: float | None
    boost_expires_at: dt.datetime | None
    pinned: bool
    status: TopicStatus


class SteeringLogCreate(CreateBase):
    """Append-only audit row (§10.1). ``changed_at`` is supplied by the writer
    rather than server-defaulted, so the timestamp always reflects when the
    steering decision was made, not when this row happened to be flushed."""

    changed_at: dt.datetime
    actor: str = Field(min_length=1)  # "user" | "orchestrator"
    topic: str | None = None
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None


class SteeringLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: int
    changed_at: dt.datetime
    actor: str
    topic: str | None
    field: str | None
    old_value: str | None
    new_value: str | None
    reason: str | None


class FetchPolicyCreate(CreateBase):
    """``consecutive_failures`` is not caller input — the worker increments it
    on failure, so every new policy row starts at zero (§6.4)."""

    domain: str = Field(min_length=1)  # '*' = global default
    settings: dict | None = None
    status: DomainStatus = "active"
    note: str | None = None


class FetchPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    domain: str
    settings: dict | None
    status: DomainStatus
    note: str | None
    consecutive_failures: int
    updated_at: dt.datetime | None
    updated_by: str | None


class AgentCreate(CreateBase):
    """``api_key_env_var`` names an environment variable; it is never the
    secret value itself (spec §11.11)."""

    agent_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str | None = None
    task_types: list[str] | None = None
    token_scope: TokenScope = "read"
    cost_tier: str | None = None
    quality_tier: QualityTier | None = None
    max_context: int | None = None
    enabled: bool = False
    fallback_agent_id: str | None = None
    endpoint: str | None = None
    health_url: str | None = None
    availability: AgentAvailability = "on_demand"
    wake_mac: str | None = None
    api_key_env_var: str | None = None


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    provider: str
    model: str | None
    task_types: list[str] | None
    token_scope: TokenScope
    cost_tier: str | None
    quality_tier: QualityTier | None
    max_context: int | None
    enabled: bool
    fallback_agent_id: str | None
    endpoint: str | None
    health_url: str | None
    availability: AgentAvailability
    wake_mac: str | None
    api_key_env_var: str | None
    created_at: dt.datetime
