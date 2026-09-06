"""Alembic environment.

Two decisions worth stating, because both were bugs waiting to happen:

1. **Migrations run as the database owner** (``PG_MIGRATION_URL``), not as
   ``meridian_rw``. ``ALTER DEFAULT PRIVILEGES`` only covers objects created by
   the role that declared them, so a table created by the wrong role leaves
   ``meridian_ro`` without ``SELECT`` on it — and that failure is invisible until
   an Explore query fails in production (scaffold §4).

2. **pgvector's types must be importable when autogenerate runs**, or every
   revision will try to drop and recreate the embedding columns.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from meridian_core.db import Base, normalize_url

# Importing the models package registers every table on Base.metadata, which is
# what autogenerate compares the live database against.
import meridian_core.models  # noqa: F401  isort:skip

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("PG_MIGRATION_URL")
    if not url:
        raise RuntimeError(
            "PG_MIGRATION_URL is not set. Migrations run as the database owner, "
            "not as meridian_rw — see migrations/env.py for why."
        )
    return normalize_url(url)


def _render_item(type_, obj, autogen_context) -> str | bool:
    """Render pgvector columns with the import they need.

    Autogenerate emits ``pgvector.sqlalchemy.vector.VECTOR(dim=1024)`` but does
    not add the corresponding import, so the generated revision raises
    ``NameError`` the moment it runs. Registering the import here fixes it for
    every future revision rather than requiring a manual edit each time.
    """
    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector({obj.dim})"
    return False


def _include_object(obj, name: str, type_: str, reflected: bool, compare_to) -> bool:
    """Keep Alembic's hands off objects it doesn't own.

    Apache AGE and pgvector create catalog tables in their own schemas; without
    this, autogenerate proposes dropping them.
    """
    return not (type_ == "table" and getattr(obj, "schema", None) not in (None, "public"))


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, a widened column or a changed default silently produces
        # an empty revision, and the schema drifts from the models unnoticed.
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
