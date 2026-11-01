"""Wait for the database to reach the schema this code was built for (task `B-263`).

A server that updates itself pulls new images for every service at once, and the one that
migrates (`migrate`) can finish after the others have started. Each long-running service
waits here at start until the database's Alembic revision is the one this release expects,
so new code never runs against the old schema.
See docs/guides/deployment.md#updates-and-migrations.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .db import dispose_engines, session
from .logging import get_logger

log = get_logger(__name__)

#: The newest migration in this release. `tests/unit/test_schemaready.py` holds it to the
#: head of `migrations/versions`, so adding a migration without changing this fails a test.
EXPECTED_REVISION = "b233a1c2d3e4"

#: How long a service waits before starting anyway, loudly: a database *ahead* of the image
#: (a rollback to an older release) never matches, and an additive schema serves old code.
DEFAULT_WAIT_S = 600.0
POLL_S = 5.0


async def database_revision(session_factory=session) -> str | None:
    """The revision the database is at, or None when it has none yet (never migrated)."""
    try:
        async with session_factory() as sess:
            return await sess.scalar(text("SELECT version_num FROM alembic_version"))
    except DBAPIError:
        # No `alembic_version` table: a database `setup` has not reached yet.
        return None


async def wait_for_schema(
    *,
    timeout_s: float | None = None,
    poll_s: float = POLL_S,
    session_factory=session,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = asyncio.sleep,
) -> bool:
    """Return once the database is at `EXPECTED_REVISION`; True if it got there.

    `MERIDIAN_SCHEMA_WAIT_S` sets the timeout (0 skips the check). After it the service
    starts anyway and says so, so a rollback or a stuck migration is visible rather than a
    service that never starts.
    """
    if timeout_s is None:
        timeout_s = float(os.environ.get("MERIDIAN_SCHEMA_WAIT_S", DEFAULT_WAIT_S))
    if timeout_s <= 0:
        return True
    end = clock() + timeout_s
    seen: str | None = None
    while True:
        try:
            seen = await database_revision(session_factory)
        except OSError:  # the database itself is not up yet
            seen = None
        if seen == EXPECTED_REVISION:
            return True
        if clock() >= end:
            log.error(
                "schema not at this release's revision; starting anyway",
                extra={"expected": EXPECTED_REVISION, "database": seen},
            )
            return False
        log.info(
            "waiting for the database to be migrated",
            extra={"expected": EXPECTED_REVISION, "database": seen},
        )
        await sleep(poll_s)


def wait_before_start() -> bool:
    """`wait_for_schema` for a process entry point, in its own event loop.

    Disposes the engines afterwards: they belong to this loop, and the service's own
    `asyncio.run` makes another.
    """

    async def go() -> bool:
        try:
            return await wait_for_schema()
        finally:
            await dispose_engines()

    return asyncio.run(go())
