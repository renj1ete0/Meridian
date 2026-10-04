"""DTOs for issuing MCP tokens in Admin (tasks `B-138`, `B-146`; ADRs 0003 and 0011)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class TokenRowRead(BaseModel):
    """One issued token, never its secret."""

    token_id: int
    held_by: str
    #: The profile whose tools it carries, or "custom" for a hand-scoped token.
    profile: str
    tools: list[str]
    created_at: dt.datetime
    expires_at: dt.datetime | None
    #: "active", "expired" or "revoked".
    state: str
    #: True when it never expires (ADR 0011): flagged so it stays a visible choice.
    no_expiry: bool


class TokensRead(BaseModel):
    rows: list[TokenRowRead]
    #: The profiles that can be issued, with their tools.
    profiles: dict[str, list[str]]
    #: The public MCP address, when the deployment has one (`MERIDIAN_MCP_RESOURCE_URL`).
    public_url: str | None


class TokenIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    held_by: str = Field(min_length=1, max_length=80, pattern=r"^[\w .@-]+$")
    profile: str = "reader"
    #: Days until it expires; None for never (ADR 0011).
    days: int | None = Field(default=90, ge=1, le=3650)


class TokenIssued(BaseModel):
    """The one response that carries the secret. It is not stored anywhere."""

    row: TokenRowRead
    secret: str
