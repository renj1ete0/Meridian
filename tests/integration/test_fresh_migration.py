"""Migrating an empty database in one run, as a first deployment does (`B-38`).

Every other schema test runs against the dev database, which was migrated a
few revisions at a time as they were written. A fresh deployment runs every
revision in one `alembic upgrade head` — and Alembic runs them in **one
transaction**, so anything a revision sets with `SET LOCAL` is still set for
every revision after it. The graph-store revision put `ag_catalog` first on the
search path that way, and the next revision's `merge_log` was created inside
AGE's catalog: present, and invisible to the application roles, so every merge
failed with "relation merge_log does not exist". The dev database never saw it.

So this builds a scratch database, migrates it from nothing in a single run,
and checks where everything landed. It is slower than the rest of the suite by
the length of the migration history, which is the point.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from meridian_core.db import Base

# Registers every model on `Base.metadata`.
import meridian_core.models  # noqa: F401  isort:skip

pytestmark = pytest.mark.usefixtures("require_db")

REPO = Path(__file__).resolve().parents[2]

#: AGE's own catalog tables. Anything else in `ag_catalog` is an application
#: table that went astray.
AGE_CATALOG_TABLES = frozenset({"ag_graph", "ag_label"})


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _asyncpg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def scratch():
    owner_url = os.environ.get("PG_MIGRATION_URL")
    if not owner_url:
        pytest.skip("PG_MIGRATION_URL is not set; the owner is needed to create a database")

    name = f"meridian_fresh_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_asyncpg_url(owner_url))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()

    url = _with_database(owner_url, name)
    # What `scripts/init-roles.sh` does for the database the image initialises:
    # pgvector comes from there, not from a migration. The roles are
    # cluster-wide and already exist.
    conn = await asyncpg.connect(_asyncpg_url(url))
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()

    yield url

    admin = await asyncpg.connect(_asyncpg_url(owner_url))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def test_a_fresh_database_puts_every_application_table_in_public(scratch) -> None:
    env = {**os.environ, "PG_MIGRATION_URL": scratch}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr[-4000:]

    conn = await asyncpg.connect(_asyncpg_url(scratch))
    try:
        rows = await conn.fetch(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE'"
        )
    finally:
        await conn.close()

    where = {row["table_name"]: row["table_schema"] for row in rows}
    astray = sorted(
        name
        for name, schema in where.items()
        if schema == "ag_catalog" and name not in AGE_CATALOG_TABLES
    )
    assert not astray, f"application tables created inside AGE's catalog: {astray}"

    misplaced = sorted(
        table.name for table in Base.metadata.sorted_tables if where.get(table.name) != "public"
    )
    assert not misplaced, f"model tables missing from public: {misplaced}"


async def test_the_repair_moves_a_misplaced_merge_log_and_grants_it(scratch) -> None:
    """The databases that already have the table in the wrong schema — every
    deployment made from empty before the fix — are repaired by a migration,
    and the application role can then write to it."""
    env = {**os.environ, "PG_MIGRATION_URL": scratch}

    def alembic(*args: str) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert done.returncode == 0, done.stderr[-4000:]

    # Recreate the broken state: migrate to just before the repair, then put
    # the table where the old graph-store revision left it, without grants.
    alembic("upgrade", "5d2e9f1a7c30")
    conn = await asyncpg.connect(_asyncpg_url(scratch))
    try:
        await conn.execute("ALTER TABLE public.merge_log SET SCHEMA ag_catalog")
        await conn.execute("REVOKE ALL ON ag_catalog.merge_log FROM meridian_rw, meridian_ro")
    finally:
        await conn.close()

    alembic("upgrade", "head")

    conn = await asyncpg.connect(_asyncpg_url(scratch))
    try:
        schema = await conn.fetchval(
            "SELECT table_schema FROM information_schema.tables WHERE table_name = 'merge_log'"
        )
        can_insert = await conn.fetchval(
            "SELECT has_table_privilege('meridian_rw', 'public.merge_log', 'INSERT')"
        )
        can_read = await conn.fetchval(
            "SELECT has_table_privilege('meridian_ro', 'public.merge_log', 'SELECT')"
        )
        can_use_sequence = await conn.fetchval(
            "SELECT has_sequence_privilege('meridian_rw', 'public.merge_log_merge_id_seq', 'USAGE')"
        )
    finally:
        await conn.close()

    assert schema == "public"
    assert can_insert and can_read and can_use_sequence
