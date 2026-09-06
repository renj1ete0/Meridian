"""Database engines, sessions, and the declarative base.

Two roles, two engines (scaffold §4):

- ``meridian_rw`` — worker, orchestrator, and ``/api/admin/*`` routes
- ``meridian_ro`` — ``/api/explore/*`` routes and ``run_readonly_query`` (spec §12.4)

Read-only is enforced by Postgres, not by application code. That is the whole
point: the read-only escape hatch is safe because the role cannot write, not
because the query builder declines to.

Engines are created lazily on first use so that importing this module has no
side effects — tests, Alembic, and ``--help`` invocations must not open sockets.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final, Literal

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

Role = Literal["rw", "ro"]

_ENV_VAR: Final[dict[Role, str]] = {"rw": "PG_RW_URL", "ro": "PG_RO_URL"}

# Postgres runs with max_connections=40 (scaffold §3) shared across the worker,
# orchestrator, API (which holds both engines), migrations, and any psql session.
# These defaults keep the total comfortably under that; override per service via
# the environment rather than editing this file.
_DEFAULT_POOL_SIZE: Final[int] = 5
_DEFAULT_MAX_OVERFLOW: Final[int] = 5
_DEFAULT_POOL_TIMEOUT: Final[int] = 30
_DEFAULT_POOL_RECYCLE: Final[int] = 1800  # 30 min, ahead of any idle reaper

_engines: dict[Role, AsyncEngine] = {}
_sessionmakers: dict[Role, async_sessionmaker[AsyncSession]] = {}


# Stable constraint names so Alembic autogenerate produces deterministic
# migrations instead of database-assigned names that differ per environment.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every Meridian model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def normalize_url(url: str) -> str:
    """Coerce a plain Postgres URL to the asyncpg driver.

    ``.env`` files carry driver-agnostic URLs (``postgresql://...``) so they stay
    readable and so swapping drivers does not mean rewriting every environment.
    """
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise RuntimeError(f"Unrecognised Postgres URL scheme: {url.split('://', 1)[0]!r}")


def database_url(role: Role) -> str:
    """Read and normalise the URL for ``role``, or fail with a usable message."""
    var = _ENV_VAR[role]
    raw = os.environ.get(var)
    if not raw:
        raise RuntimeError(
            f"{var} is not set. Copy .env.example and fill it in; "
            f"credentials come from the environment, never the database (spec §11.11)."
        )
    return normalize_url(raw)


def get_engine(role: Role = "rw") -> AsyncEngine:
    """Return the process-wide engine for ``role``, creating it on first call."""
    engine = _engines.get(role)
    if engine is not None:
        return engine

    engine = create_async_engine(
        database_url(role),
        pool_size=_int_env("MERIDIAN_DB_POOL_SIZE", _DEFAULT_POOL_SIZE),
        max_overflow=_int_env("MERIDIAN_DB_MAX_OVERFLOW", _DEFAULT_MAX_OVERFLOW),
        pool_timeout=_int_env("MERIDIAN_DB_POOL_TIMEOUT", _DEFAULT_POOL_TIMEOUT),
        pool_recycle=_int_env("MERIDIAN_DB_POOL_RECYCLE", _DEFAULT_POOL_RECYCLE),
        # The ingestion node runs unattended for weeks; a dropped connection must
        # surface as one retried checkout, not as a service that needs restarting.
        pool_pre_ping=True,
        echo=os.environ.get("MERIDIAN_DB_ECHO", "").lower() in {"1", "true", "yes"},
    )
    _engines[role] = engine
    return engine


def get_sessionmaker(role: Role = "rw") -> async_sessionmaker[AsyncSession]:
    """Return the session factory for ``role``."""
    maker = _sessionmakers.get(role)
    if maker is not None:
        return maker

    maker = async_sessionmaker(
        bind=get_engine(role),
        class_=AsyncSession,
        expire_on_commit=False,  # attributes stay readable after commit
        autoflush=False,
    )
    _sessionmakers[role] = maker
    return maker


@asynccontextmanager
async def session(role: Role = "rw") -> AsyncIterator[AsyncSession]:
    """Session scope that commits on success and rolls back on failure.

    Read-only callers should use :func:`session_ro`, which additionally marks the
    transaction read-only so a stray write fails immediately rather than at commit.
    """
    async with get_sessionmaker(role)() as sess:
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise


@asynccontextmanager
async def session_ro() -> AsyncIterator[AsyncSession]:
    """Read-only session scope, for explore routes and ad-hoc queries.

    Belt and braces: the ``meridian_ro`` role cannot write, and the transaction is
    also declared read-only so mistakes fail loudly and locally.
    """
    async with get_sessionmaker("ro")() as sess:
        await sess.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield sess
        finally:
            await sess.rollback()


async def check_connection(role: Role = "rw") -> bool:
    """Return True if ``role`` can reach the database. Used by healthchecks."""
    try:
        async with get_engine(role).connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engines() -> None:
    """Close all pooled connections. Call on shutdown."""
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()
    _sessionmakers.clear()
