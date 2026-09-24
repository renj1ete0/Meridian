"""DTOs for steering proposals (task P6-38, spec §10, §10.1).

Mirrors ``meridian_core.models.config.SteeringProposal``. There is no
``*Create``: proposals are written only by the pass that measures the signal,
never through an API, so the only thing a caller may submit is a decision.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import ProposalKind, ProposalStatus


class SteeringProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proposal_id: int
    created_at: dt.datetime
    actor: str
    topic: str
    kind: ProposalKind
    current_value: float
    proposed_value: float
    expires_at: dt.datetime | None
    reason: str
    evidence: dict[str, Any]
    apply_after: dt.datetime
    status: ProposalStatus
    decided_by: str | None
    decided_at: dt.datetime | None
    applied_at: dt.datetime | None
    note: str | None


class SteeringProposalsRead(BaseModel):
    """What Admin shows: what is waiting, and what was recently decided."""

    pending: list[SteeringProposalRead]
    recent: list[SteeringProposalRead]
    #: How long a proposal waits for an objection, from the global policy row.
    window_hours: float


class SteeringProposalReject(BaseModel):
    """A rejection. The reason is optional and, when given, logged."""

    reason: str | None = Field(default=None, max_length=500)
