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

There is deliberately no read-write dependency here yet. `/api/admin/*` is not
built, and providing the session it would need invites a route to reach for it
before anyone has thought about validation (§11.8).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core.db import session_ro


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
