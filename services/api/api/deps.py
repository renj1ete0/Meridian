"""Request-scoped dependencies (task P2-07, scaffold §4, spec §12.6).

One dependency per database role, and the role is chosen by the route prefix it
serves — §12.6's first deferred-auth decision: "route prefixes by mutation,
`/api/explore/*` for reads, `/api/admin/*` for writes and control. Role-gating
then becomes a single middleware check on a path prefix."

**The read-only guarantee is Postgres's, not this module's.** `meridian_ro`
cannot write, so an explore route that tried would fail at the database even if
every layer above it had a bug. `session_ro` additionally issues
`SET TRANSACTION READ ONLY`, which turns a mistake into an immediate error at
the statement rather than a surprise at commit — belt and braces, and the belt
is the one that matters. AGENTS.md: "Enforcing read-only at the database, not in
application code, is what makes the read-only escape hatch safe."

**`/api/admin/*` gets a writable session and nothing else does.** `WriteSession`
is defined here rather than in the admin router so that the role boundary is one
file: a handler under `/api/explore` that wanted to write would have to import
across the prefix to do it, which is visible in review in a way a session opened
inline is not.

It is also gated. Admin is the only surface that changes anything, and a
deployment that puts this behind a tunnel without Cloudflare Access in front is
one forgotten variable away from handing the corpus's configuration to whoever
finds the hostname. So the routes refuse unless identity is verified or somebody
has explicitly said this instance is not exposed — the same opt-out shape the MCP
surface uses, for the same reason: open-unless-configured fails silently and in
the wrong direction.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import session, session_ro

#: The strings that count as "yes". Anything else — including "0" and "no" — is
#: not an opt-out, because a variable set to a typo must not read as consent.
TRUTHY = frozenset({"1", "true", "yes"})


async def read_session() -> AsyncIterator[AsyncSession]:
    """A read-only session for the life of one request.

    Not cached across requests: pooling is the engine's job, and a session held
    longer than a request would keep a transaction — and therefore a snapshot —
    open while the corpus moved underneath it.
    """
    async with session_ro() as sess:
        yield sess


#: The annotation routes use, so the role is visible in the signature rather
#: than buried in a decorator someone can copy onto the wrong handler.
ReadSession = Annotated[AsyncSession, Depends(read_session)]


def admin_is_unprotected() -> bool:
    """Whether anybody has said this instance is not exposed.

    Deliberately an opt-out rather than a default. The failure mode of
    open-unless-configured is a write surface published to the internet by a
    deployment step somebody forgot, and the person who forgets is deploying
    rather than reading this file.
    """
    return (os.environ.get("MERIDIAN_ADMIN_ALLOW_ANONYMOUS") or "").strip().lower() in TRUTHY


def admin_is_allowed() -> None:
    """Refuse admin entirely unless callers are identified, or the opt-out is set.

    `CF_ACCESS_TEAM_DOMAIN` plus `CF_ACCESS_AUD` is what makes the middleware
    verify an Access assertion (`P3-08`), and that is the only identity this
    service has. With neither that nor the opt-out, admin is a set of
    unauthenticated write endpoints, and 503 with a message naming the fix is a
    better answer than serving them.
    """
    if admin_is_unprotected():
        return
    if os.environ.get("CF_ACCESS_TEAM_DOMAIN") and os.environ.get("CF_ACCESS_AUD"):
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "Admin is closed: no caller identity is configured. Set CF_ACCESS_TEAM_DOMAIN "
            "and CF_ACCESS_AUD to verify Access assertions, or MERIDIAN_ADMIN_ALLOW_ANONYMOUS "
            "on an instance that is not exposed."
        ),
    )


async def write_session() -> AsyncIterator[AsyncSession]:
    """A read-write session for the life of one request.

    Committing is the handler's job, not this dependency's. A generator that
    committed on the way out would commit whatever a handler had flushed before
    raising, which turns a rejected request into a half-applied one — and the
    response would say it failed.
    """
    async with session("rw") as sess:
        yield sess


#: The annotation admin routes use. The gate is a dependency rather than a check
#: inside each handler so that a new route cannot be added without it.
WriteSession = Annotated[AsyncSession, Depends(write_session)]
AdminAllowed = Annotated[None, Depends(admin_is_allowed)]
