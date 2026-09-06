"""Shared fixtures.

Integration tests run against a real Postgres, never a mock or SQLite. Three of
the bugs found while building Phase 0 — a role bootstrap that never ran, CHECK
constraints that were never created, and default privileges that silently denied
reads — were all invisible to anything except a real database. SQLite would have
reported every one of them as passing.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from meridian_core.db import Role, normalize_url

DEV_URLS = {
    "rw": "postgresql://meridian_rw:dev@localhost:21111/meridian",
    "ro": "postgresql://meridian_ro:dev@localhost:21111/meridian",
    "owner": "postgresql://meridian:dev@localhost:21111/meridian",
}


def _url(role: str) -> str:
    env = {"rw": "PG_RW_URL", "ro": "PG_RO_URL", "owner": "PG_MIGRATION_URL"}[role]
    return normalize_url(os.environ.get(env) or DEV_URLS[role])


async def _reachable() -> bool:
    engine = create_async_engine(_url("rw"), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def database_available() -> bool:
    return await _reachable()


@pytest.fixture
def require_db(database_available: bool) -> None:
    if not database_available:
        pytest.skip("no Postgres at PG_RW_URL — run `make dev-up` first")


@pytest_asyncio.fixture
async def session_for(require_db) -> AsyncIterator[callable]:
    """Yield a factory for role-scoped sessions that roll back afterwards.

    Every test gets a transaction that is discarded, so tests cannot leak state
    into each other or into the developer's dev database.
    """
    engines = {}
    sessions = []

    async def make(role: Role | str = "rw") -> AsyncSession:
        if role not in engines:
            engines[role] = create_async_engine(_url(role), poolclass=None)
        maker = async_sessionmaker(engines[role], class_=AsyncSession, expire_on_commit=False)
        sess = maker()
        sessions.append(sess)
        return sess

    yield make

    for sess in sessions:
        await sess.rollback()
        await sess.close()
    for engine in engines.values():
        await engine.dispose()
