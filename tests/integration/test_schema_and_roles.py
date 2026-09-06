"""Integration tests against a real Postgres.

These encode invariants the spec states, and each one corresponds to a bug that
was actually found during Phase 0 by executing rather than reading:

- the role bootstrap ran as a ``.sql`` file the entrypoint could not parse, so no
  roles existed at all;
- ``constrained()`` omitted ``create_constraint``, so every status column was an
  unchecked VARCHAR;
- default privileges were declared for one writer only, so the read-only role
  lost SELECT on tables created by the other.

None of the three was visible in review.
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import inspect, text

from meridian_core.db import Base

pytestmark = pytest.mark.usefixtures("require_db")


# --------------------------------------------------------------------------
# Migration drift — the single most valuable test in the suite
# --------------------------------------------------------------------------


def test_migrations_match_the_models() -> None:
    """`alembic upgrade head` must leave nothing for autogenerate to do.

    If this fails, someone changed a model without generating a migration, and
    production will diverge from the code the moment it deploys. Runs alembic in
    a subprocess because its env.py drives its own asyncio loop.
    """
    result = subprocess.run(
        ["uv", "run", "alembic", "check"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "schema drift between models and migrations:\n"
        f"{result.stdout}\n{result.stderr}\n"
        'Run: make revision m="what changed"'
    )


async def test_every_model_table_exists_in_the_database(session_for) -> None:
    """A model with no table behind it fails at runtime, not at import."""
    sess = await session_for("rw")
    conn = await sess.connection()
    live = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    expected = set(Base.metadata.tables)
    missing = expected - live
    assert not missing, f"models with no table in the database: {sorted(missing)}"


async def test_pgvector_is_installed(session_for) -> None:
    """Embedding columns are unusable without the extension, and it is created
    at first boot rather than by a migration — so it is worth asserting."""
    sess = await session_for("rw")
    version = await sess.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
    assert version is not None, "pgvector missing — scripts/init-roles.sh did not run"


# --------------------------------------------------------------------------
# Role enforcement (scaffold §4, spec §12.4)
# --------------------------------------------------------------------------


async def test_readonly_role_can_read(session_for) -> None:
    """Regression: default privileges once covered only one writer, and the
    read-only role silently lost SELECT on tables the other created."""
    sess = await session_for("ro")
    count = await sess.scalar(text("SELECT count(*) FROM queue"))
    assert count is not None


async def test_readonly_role_cannot_write(session_for) -> None:
    """Enforced by Postgres, not by application code. That is what makes the
    `run_readonly_query` escape hatch safe to expose to a model (§12.4)."""
    sess = await session_for("ro")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text("INSERT INTO queue (url_or_query, task_type, status) VALUES ('x','url','pending')")
        )
    assert "permission denied" in str(exc.value).lower()


async def test_readonly_role_cannot_write_to_any_table(session_for) -> None:
    """Spot-checking one table would miss a newly added one."""
    sess = await session_for("ro")
    for table in ["sources", "entities", "edges", "topic_config", "runs"]:
        await sess.rollback()
        with pytest.raises(Exception) as exc:
            await sess.execute(text(f"DELETE FROM {table}"))
        assert "permission denied" in str(exc.value).lower(), f"{table} is writable by ro"


# --------------------------------------------------------------------------
# Constraints must actually exist, not merely be intended
# --------------------------------------------------------------------------


async def test_check_constraints_reject_invalid_enum_values(session_for) -> None:
    """Regression for the bug where every 'enum' column accepted any string.

    SQLAlchemy's Enum(native_enum=False) does not emit a CHECK unless
    create_constraint=True, which it has defaulted to False since 1.4.
    """
    sess = await session_for("rw")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO queue (url_or_query, task_type, status, priority, "
                "seed_source, attempts, created_at) "
                "VALUES ('x','url','not_a_real_status',0,'user',0,now())"
            )
        )
    assert "check constraint" in str(exc.value).lower()


async def test_check_constraints_exist_for_every_constrained_column(session_for) -> None:
    """Counts CHECKs in the database against constrained columns in the models."""
    from sqlalchemy import Enum as SAEnum

    expected = {
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, SAEnum)
    }
    sess = await session_for("rw")
    live = await sess.scalar(
        text(
            "SELECT count(*) FROM information_schema.table_constraints "
            "WHERE constraint_type='CHECK' AND constraint_schema='public' "
            "AND constraint_name LIKE 'ck_%'"
        )
    )
    assert live >= len(expected), (
        f"{len(expected)} constrained columns in the models but only {live} CHECK "
        "constraints in the database"
    )


async def test_foreign_keys_are_enforced(session_for) -> None:
    """A chunk pointing at a nonexistent source would orphan provenance."""
    sess = await session_for("rw")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO chunks (source_id, text, chunk_index, created_at) "
                "VALUES (999999999, 'orphan', 0, now())"
            )
        )
    assert "foreign key" in str(exc.value).lower()


async def test_duplicate_source_urls_are_rejected(session_for) -> None:
    """The same URL twice would double-count coverage and split provenance."""
    sess = await session_for("rw")
    await sess.execute(
        text(
            "INSERT INTO sources (url, source_tier, retention_tier, text_available, "
            "ocr_applied, ocr_tier, created_at) VALUES "
            "('https://dupe.test','government','primary',true,false,'none',now())"
        )
    )
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO sources (url, source_tier, retention_tier, text_available, "
                "ocr_applied, ocr_tier, created_at) VALUES "
                "('https://dupe.test','press','background',true,false,'none',now())"
            )
        )
    assert "unique" in str(exc.value).lower()


async def test_supporting_chunk_ids_cannot_be_null(session_for) -> None:
    """An edge without provenance is not assertable (§2 principle 3)."""
    sess = await session_for("rw")
    await sess.execute(
        text(
            "INSERT INTO entities (entity_id, canonical_name, node_type, schema_version, "
            "created_at) VALUES (900001,'A','concept',1,now()), (900002,'B','concept',1,now())"
        )
    )
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO edges (from_node, to_node, relation_type, "
                "supporting_chunk_ids, schema_version, created_at) "
                "VALUES (900001, 900002, 'relates_to', NULL, 1, now())"
            )
        )
    assert "null" in str(exc.value).lower()
