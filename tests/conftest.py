"""Shared fixtures.

Integration tests run against a real Postgres, never a mock or SQLite. Three of
the bugs found while building Phase 0 — a role bootstrap that never ran, CHECK
constraints that were never created, and default privileges that silently denied
reads — were all invisible to anything except a real database. SQLite would have
reported every one of them as passing.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from http_doubles import RecordingTransport, streamed
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from meridian_core.db import Role, normalize_url
from meridian_core.netguard import IPAddress
from meridian_core.policy import ResolvedPolicy

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


@pytest.fixture
def policy() -> Callable[..., ResolvedPolicy]:
    """Build a ResolvedPolicy with the shipped defaults, overridden per test."""

    def make(**overrides: object) -> ResolvedPolicy:
        return ResolvedPolicy(domain=overrides.pop("domain", "example.test"), **overrides)

    return make


@pytest.fixture
def resolver() -> Callable[..., object]:
    """A stub resolver over a host → addresses mapping.

    ``sequence`` gives a *different* answer per call for one host, which is how
    DNS rebinding is expressed: the check sees one address and the connection
    would see another.
    """

    def make(
        mapping: dict[str, list[str]] | None = None,
        *,
        sequence: dict[str, list[list[str]]] | None = None,
        fail: set[str] | None = None,
    ):
        calls: dict[str, int] = {}

        async def resolve(host: str, port: int = 443) -> list[IPAddress]:
            calls[host] = calls.get(host, 0) + 1
            if fail and host in fail:
                raise OSError(f"Name or service not known: {host}")
            if sequence and host in sequence:
                answers = sequence[host]
                index = min(calls[host] - 1, len(answers) - 1)
                return [ipaddress.ip_address(a) for a in answers[index]]
            if mapping and host in mapping:
                return [ipaddress.ip_address(a) for a in mapping[host]]
            raise OSError(f"Name or service not known: {host}")

        resolve.calls = calls  # type: ignore[attr-defined]
        return resolve

    return make


@pytest.fixture
def recorder() -> type[RecordingTransport]:
    return RecordingTransport


@pytest.fixture
def stream_response():
    return streamed
