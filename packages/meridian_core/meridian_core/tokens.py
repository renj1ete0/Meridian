"""Scoped credentials (task P3-03, spec §11.4).

§11.4's model, made enforceable: every agent gets its own credential with an
explicit tool scope, an expiry, and a revocation switch. "This is what makes it
safe to point an ad-hoc chat session at the graph."

**Secrets are never compared in this module.** The presented token is hashed and
the hash is looked up; nothing holds a stored secret alongside a candidate one.
That removes the whole class of timing and logging mistakes that comes from
comparing them — there is no branch here whose duration depends on how much of a
token was right, and a log line that accidentally included the row would include
a hash.

**SHA-256, not bcrypt, and that is deliberate.** Password hashes are slow to
defend low-entropy human choices against offline guessing. These tokens are 256
bits of `secrets.token_urlsafe` — an attacker with the hash cannot guess the
preimage at any cost, and a slow hash would only add latency to every single
request. The scheme is named in the stored value so a future change is a
migration rather than an archaeology exercise.

**An unscoped token grants nothing.** `allowed_tools` is nullable and NULL means
*no tools*, not *all tools*. That is the same argument as `P3-07`'s refusal to
give the guest role default privileges: forgetting to grant produces a caller
who cannot do something and says so, which gets fixed; forgetting to restrict
produces a caller who can do everything and does not mention it.
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

    ``allowed_tools`` has no default. An unscoped token is a real decision and
    has to be spelled out, rather than being what you get by not thinking about
    it — and a caller that genuinely wants one passes the list.

    Flushes; does not commit. The caller owns the transaction, because issuing a
    token is usually one step of a larger administrative action.
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
    # The agent, never the secret and never the hash. A log that carried either
    # would put a working credential in the place credentials are least
    # protected — and §11.11 keeps them out of the database's own reach for the
    # same reason.
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

    None for every rejection — unknown, revoked, expired — and deliberately not
    three distinct errors. The caller is unauthenticated by definition, and
    telling it *which* of those applies confirms that a token exists, or existed,
    which is information it has not earned. The reason is logged for the
    operator instead.
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
