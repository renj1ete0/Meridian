"""The guest read role (task P3-07, spec §11.4, §12.4).

Against a real Postgres because privileges are not a property of any code.
`P0-18` is the precedent: a role bootstrap that never ran and default privileges
that silently denied reads were both invisible to everything except an actual
database, and the same is true in the other direction — a grant nobody intended
looks exactly like one nobody made.

The role exists because `meridian_ro` can SELECT every table in the schema,
`agent_tokens` included, and that table holds the only secret the database
stores. That is the right role for the operator's own read path and the wrong
one to put behind `P3-04`'s SQL escape hatch or any shared read surface.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

# Loaded by path: `migrations/` is an Alembic script directory, not an
# importable package. Reading the list from the migration rather than repeating
# it here is the whole point — a copy would drift, and the copy is what the
# drift test would then be checking.
_migration = importlib.util.spec_from_file_location(
    "guest_read_role",
    pathlib.Path(__file__).resolve().parents[2]
    / "migrations/versions/20260915_1900_f4b8c3e71a25_guest_read_role.py",
)
_module = importlib.util.module_from_spec(_migration)
_migration.loader.exec_module(_module)
READABLE, ROLE = _module.READABLE, _module.ROLE

pytestmark = pytest.mark.usefixtures("require_db")


async def tables(sess) -> set[str]:
    rows = await sess.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    return {row[0] for row in rows}


async def may_read(sess, table: str) -> bool:
    return bool(
        await sess.scalar(
            text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
            {"role": ROLE, "table": table},
        )
    )


async def test_the_role_exists(session_for) -> None:
    sess = await session_for("owner")
    assert await sess.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}), (
        f"{ROLE} was never created — the migration did not run, or ran against another database"
    )


async def test_the_privilege_matrix_covers_every_table(session_for) -> None:
    """Drift, over `pg_tables` rather than over a list written here.

    A table added to the schema and to neither set fails this, which is the
    point: "should a guest be able to read this" is a decision, and a new table
    silently inheriting either answer is how the wrong one gets made.
    """
    sess = await session_for("owner")
    readable = {t for t in await tables(sess) if await may_read(sess, t)}

    assert readable == set(READABLE), (
        "guest-readable tables have drifted from the migration's list.\n"
        f"  unexpectedly readable: {sorted(readable - set(READABLE))}\n"
        f"  expected but not readable: {sorted(set(READABLE) - readable)}"
    )


@pytest.mark.parametrize(
    "table",
    ["agent_tokens", "fetch_policy", "queue", "fetch_attempts", "agents", "steering_log"],
)
async def test_operational_tables_are_not_readable(session_for, table: str) -> None:
    """Named individually as well as covered by the matrix above.

    `agent_tokens` is the one that matters most — `token_hash` is the single
    secret in this schema — but the others leak a different thing: `queue` and
    `fetch_policy` describe what this crawler is about to look at and how it
    behaves, which is the operator's research direction rather than the corpus.
    """
    sess = await session_for("owner")
    assert await may_read(sess, table) is False, f"a guest can read {table}"


async def test_the_role_actually_cannot_select(session_for) -> None:
    """A rejection test, not a privilege lookup.

    `has_table_privilege` and the planner are meant to agree, and asserting only
    the catalogue would pass if they ever did not. `SET ROLE` runs the query
    with the guest's privileges without needing its password.
    """
    sess = await session_for("owner")
    await sess.execute(text(f"SET LOCAL ROLE {ROLE}"))

    with pytest.raises(ProgrammingError):
        await sess.execute(text("SELECT token_hash FROM agent_tokens LIMIT 1"))

    await sess.rollback()


async def test_the_role_can_read_the_corpus(session_for) -> None:
    """The other half. A role that can read nothing is trivially safe and
    useless, and would pass every test above."""
    sess = await session_for("owner")
    await sess.execute(text(f"SET LOCAL ROLE {ROLE}"))

    assert await sess.scalar(text("SELECT count(*) FROM chunks")) is not None
    assert await sess.scalar(text("SELECT count(*) FROM sources")) is not None

    await sess.rollback()


async def test_the_role_cannot_write_what_it_can_read(session_for) -> None:
    """Read-only means read-only. SELECT on a table is not INSERT on it, but
    that is a property of the grant rather than of the role's name, and nothing
    else in this file would notice if a future `GRANT ALL` slipped in."""
    sess = await session_for("owner")
    await sess.execute(text(f"SET LOCAL ROLE {ROLE}"))

    with pytest.raises(ProgrammingError):
        await sess.execute(text("INSERT INTO sources (url) VALUES ('https://x.test/nope')"))

    await sess.rollback()


async def test_a_new_table_is_not_readable_by_default(session_for) -> None:
    """Fail closed. Every other role here has `ALTER DEFAULT PRIVILEGES` so that
    migrations' new tables are covered automatically; this one deliberately does
    not, so a table added later is invisible until granted explicitly.

    The asymmetry is the argument: forgetting to grant means a guest cannot read
    something they should, which someone reports. Forgetting to revoke means a
    guest reads a table nobody considered, which nobody reports.
    """
    sess = await session_for("owner")
    await sess.execute(text("CREATE TABLE guest_default_probe (id int)"))
    try:
        assert await may_read(sess, "guest_default_probe") is False
    finally:
        await sess.execute(text("DROP TABLE guest_default_probe"))
        await sess.rollback()
