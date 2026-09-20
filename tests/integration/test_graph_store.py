"""The graph store, beside the relational one (task `P4-01`, §3).

§3 chose one store on purpose: "queue + metadata + graph: Postgres (+ Apache
AGE). One battle-tested store, one recovery path, one backup routine." These
assert the half that was missing actually arrived, and the two traps found
putting it in.

Against a real Postgres because there is nothing else to test — the whole claim
is that a particular extension is present in the image the stack runs and does
what Cypher says it does.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("require_db")

#: Must not be a role name. See `test_the_graph_is_not_named_after_a_role`.
GRAPH = "graph"


@pytest.fixture
async def sess(session_for):
    return await session_for("rw")


async def test_the_extension_is_installed(sess) -> None:
    """Not merely available. An image can ship `age.so` and still have nobody
    run `CREATE EXTENSION`, which is what `P4-01`'s migration is for."""
    version = await sess.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'age'"))

    assert version, "Apache AGE is not installed in this database"


# `shared_preload_libraries` is what makes `cypher()` resolvable at all, and it
# is deliberately *not* asserted directly: `SHOW` on that setting is superuser
# only, and these tests connect as `meridian_rw` like the application does.
# `test_a_traversal_works` is the real check — without the preload it fails
# with "unhandled cypher(cstring) function call", which is the symptom anybody
# would actually meet.


async def test_the_graph_exists(sess) -> None:
    names = (await sess.execute(text("SELECT name::text FROM ag_catalog.ag_graph"))).scalars().all()

    assert GRAPH in names


async def test_the_graph_is_not_named_after_a_role(sess) -> None:
    """The trap this cost an hour to find.

    `create_graph` creates a Postgres *schema* of the graph's name. Name it
    `meridian` and it collides with the `meridian` role, so `"$user"` in the
    default `search_path` resolves to it — the graph silently becomes the
    default schema, `alembic check` proposes dropping AGE's internal tables,
    and a `CREATE TABLE` with no schema would put an application table inside
    the graph.
    """
    roles = (await sess.execute(text("SELECT rolname::text FROM pg_roles"))).scalars().all()

    assert GRAPH not in roles, (
        f"the graph schema {GRAPH!r} shares a name with a database role, which "
        f"makes it the default schema for that role's connections"
    )


async def test_a_traversal_works(sess) -> None:
    """The actual claim: Cypher, in the database that holds the corpus, as the
    role the application uses.

    This is also the preload check. If `shared_preload_libraries` does not
    include `age`, this fails with "unhandled cypher(cstring) function call" —
    and if the grants are missing it fails with "permission denied for schema
    ag_catalog". Both were real states of this database while `P4-01` was being
    built.
    """
    # `exec_driver_sql`, not `text()`. **Cypher's `:Label` collides with
    # SQLAlchemy's `:param`** — `text()` parses `(:Probe)` as a bind parameter
    # named `Probe` and refuses to run without a value for it. Anything sending
    # Cypher through SQLAlchemy meets this, and the error names a parameter
    # nobody wrote.
    connection = await sess.connection()
    await connection.exec_driver_sql('SET LOCAL search_path = ag_catalog, "$user", public')
    await connection.exec_driver_sql(
        f"""SELECT * FROM cypher('{GRAPH}', $$
            CREATE (:Probe {{name:'a'}})-[:supports]->(:Probe {{name:'b'}})
        $$) AS (v agtype)"""
    )

    result = await connection.exec_driver_sql(
        f"""SELECT count(*) FROM cypher('{GRAPH}', $$
            MATCH (:Probe)-[r]->(:Probe) RETURN r
        $$) AS (r agtype)"""
    )

    assert result.scalar() == 1
    # Rolled back by the fixture, so the graph is left as the migration made it.


async def test_pgvector_still_works_in_the_same_database(sess) -> None:
    """§3's whole argument is *one* store. An image carrying the graph and not
    the vectors would have moved the problem rather than solved it."""
    version = await sess.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    distance = await sess.scalar(text("SELECT '[1,0]'::vector <=> '[0,1]'::vector"))

    assert version
    assert distance == pytest.approx(1.0)
