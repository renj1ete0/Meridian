"""Scoped credentials (task P3-03, spec §11.4).

Each credential has an explicit tool scope, an expiry and a revocation switch. Only a
SHA-256 hash of the secret is stored and looked up, never compared, and a NULL
`allowed_tools` grants no tools. See docs/features/mcp.md#tokens-design.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import secrets
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import AgentToken

log = get_logger(__name__)

#: Named in the stored value so the scheme can be changed without guessing what
#: existing rows used.
SCHEME = "sha256"

#: 32 bytes. `token_urlsafe` gives ~43 characters of URL-safe text, which is
#: short enough to paste into a client's configuration and long enough that the
#: hash has no meaningful preimage search.
TOKEN_BYTES = 32


@dataclasses.dataclass(frozen=True)
class TokenScope:
    """What a verified credential may do.

    Returned instead of the ORM row so that nothing downstream can accidentally
    hold — or log — the record containing the hash.
    """

    token_id: int
    agent_id: str
    allowed_tools: frozenset[str]
    rate_limit: int | None

    def permits(self, tool: str) -> bool:
        return tool in self.allowed_tools


def hash_token(secret: str) -> str:
    return f"{SCHEME}:{hashlib.sha256(secret.encode()).hexdigest()}"


def new_secret() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def issue_token(
    sess: AsyncSession,
    *,
    agent_id: str,
    allowed_tools: Sequence[str],
    expires_at: dt.datetime | None = None,
    rate_limit: int | None = None,
) -> tuple[str, AgentToken]:
    """Mint a credential. Returns the secret **once**; only its hash is stored.

    ``allowed_tools`` has no default. Flushes; does not commit.
    """
    secret = new_secret()
    row = AgentToken(
        agent_id=agent_id,
        token_hash=hash_token(secret),
        allowed_tools=list(allowed_tools),
        expires_at=expires_at,
        rate_limit=rate_limit,
    )
    sess.add(row)
    await sess.flush()
    # The agent, never the secret and never the hash: logs are where credentials
    # are least protected.
    log.info(
        "token issued",
        extra={
            "agent_id": agent_id,
            "token_id": row.token_id,
            "tools": len(row.allowed_tools or []),
        },
    )
    return secret, row


async def resolve_token(
    sess: AsyncSession, secret: str, *, now: dt.datetime | None = None
) -> TokenScope | None:
    """The scope this secret carries, or None if it carries none.

    None for every rejection (unknown, revoked, expired), so an unauthenticated caller
    learns nothing about which; the reason is logged for the operator.
    """
    if not secret:
        return None

    row = (
        await sess.execute(select(AgentToken).where(AgentToken.token_hash == hash_token(secret)))
    ).scalar_one_or_none()

    if row is None:
        log.warning("token rejected", extra={"reason": "unknown"})
        return None
    if row.revoked:
        log.warning("token rejected", extra={"reason": "revoked", "token_id": row.token_id})
        return None

    moment = now or dt.datetime.now(dt.UTC)
    if row.expires_at is not None and row.expires_at <= moment:
        log.warning("token rejected", extra={"reason": "expired", "token_id": row.token_id})
        return None

    return TokenScope(
        token_id=row.token_id,
        agent_id=row.agent_id,
        # NULL means no tools. See the module docstring: the nullable column's
        # empty state has to be the safe one, because it is what a row created
        # without thinking will hold.
        allowed_tools=frozenset(row.allowed_tools or ()),
        rate_limit=row.rate_limit,
    )


async def revoke_token(sess: AsyncSession, token_id: int) -> bool:
    """Turn a credential off. Returns whether there was one to turn off.

    Revocation rather than deletion, so that an audit trail survives the
    credential: "which agent read this, and when was its access withdrawn" is a
    question that outlives the token.
    """
    row = await sess.get(AgentToken, token_id)
    if row is None:
        return False
    row.revoked = True
    await sess.flush()
    log.info("token revoked", extra={"token_id": token_id, "agent_id": row.agent_id})
    return True


#: Default life of a token issued for an assistant (ADR 0003).
DEFAULT_DAYS = 90


async def list_tokens(sess: AsyncSession, *, include_revoked: bool = False) -> list[AgentToken]:
    """Issued tokens, newest first. Never the secret; only the hash is stored."""
    stmt = select(AgentToken).order_by(AgentToken.token_id.desc())
    if not include_revoked:
        stmt = stmt.where(AgentToken.revoked.is_(False))
    return list(await sess.scalars(stmt))
