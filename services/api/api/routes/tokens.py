"""`/api/admin/tokens`: issue, list and revoke MCP tokens (task `B-146`, ADRs 0003 and 0011).

The same mechanism as `python -m api.tokens` (`B-138`), behind the Admin gate. The secret is in
one response only, the issuing one, and is never stored or logged. See docs/features/mcp.md.
"""

from __future__ import annotations

import datetime as dt
import os

from fastapi import APIRouter, HTTPException

from meridian_core.grants import PROFILE_TOOLS
from meridian_core.models import AgentToken
from meridian_core.schemas.tokens import TokenIssue, TokenIssued, TokenRowRead, TokensRead
from meridian_core.tokens import issue_token, list_tokens, revoke_token

from ..deps import AdminAllowed, WriteSession

router = APIRouter(prefix="/api/admin", tags=["admin"])


def profile_of(tools: list[str] | None) -> str:
    """The profile whose tools these are exactly, or "custom"."""
    have = set(tools or [])
    for name, granted in PROFILE_TOOLS.items():
        if have == set(granted):
            return name
    return "custom"


def row_of(token: AgentToken, now: dt.datetime) -> TokenRowRead:
    state = (
        "revoked"
        if token.revoked
        else ("expired" if token.expires_at is not None and token.expires_at <= now else "active")
    )
    return TokenRowRead(
        token_id=token.token_id,
        held_by=token.agent_id,
        profile=profile_of(token.allowed_tools),
        tools=sorted(token.allowed_tools or []),
        created_at=token.created_at,
        expires_at=token.expires_at,
        state=state,
        no_expiry=token.expires_at is None,
    )


@router.get("/tokens", response_model=TokensRead)
async def tokens_list(_: AdminAllowed, sess: WriteSession, all: bool = False) -> TokensRead:  # noqa: A002
    """Issued tokens, newest first; revoked ones only when asked."""
    now = dt.datetime.now(dt.UTC)
    rows = await list_tokens(sess, include_revoked=all)
    resource = os.environ.get("MERIDIAN_MCP_RESOURCE_URL", "")
    return TokensRead(
        rows=[row_of(row, now) for row in rows],
        profiles={name: sorted(tools) for name, tools in PROFILE_TOOLS.items()},
        public_url=resource if resource.startswith("https://") else None,
    )


@router.post("/tokens", response_model=TokenIssued, status_code=201)
async def tokens_issue(body: TokenIssue, _: AdminAllowed, sess: WriteSession) -> TokenIssued:
    """Issue a token. The response is the only place its secret ever appears."""
    if body.profile not in PROFILE_TOOLS:
        raise HTTPException(status_code=422, detail=f"No profile {body.profile!r}.")
    now = dt.datetime.now(dt.UTC)
    expires = now + dt.timedelta(days=body.days) if body.days else None
    secret, row = await issue_token(
        sess,
        agent_id=body.held_by.strip(),
        allowed_tools=sorted(PROFILE_TOOLS[body.profile]),
        expires_at=expires,
    )
    await sess.commit()
    await sess.refresh(row)
    return TokenIssued(row=row_of(row, now), secret=secret)


@router.post("/tokens/{token_id}/revoke", response_model=TokenRowRead)
async def tokens_revoke(token_id: int, _: AdminAllowed, sess: WriteSession) -> TokenRowRead:
    """Turn a token off. It stays listed under revoked, for the record."""
    if not await revoke_token(sess, token_id):
        raise HTTPException(status_code=404, detail=f"No token {token_id}.")
    await sess.commit()
    row = await sess.get(AgentToken, token_id)
    return row_of(row, dt.datetime.now(dt.UTC))
