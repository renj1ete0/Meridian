"""the graph store, alongside the relational one

Task P4-01, §3. Apache AGE, which `deploy/postgres/Dockerfile` now ships in the
image. §3 chose one store on purpose — "queue + metadata + graph: Postgres (+
Apache AGE). One battle-tested store, one recovery path, one backup routine" —
and this is the half that was missing.

**A migration, not only an init script.** `scripts/init-roles.sh` runs from
`docker-entrypoint-initdb.d`, which Postgres executes only when the data
directory is empty. A database that already holds a corpus would never see it,
and the first machine to hit that is the one with the crawl on it. Doing it
here means a fresh install and an existing one take the same path.

**`CREATE EXTENSION` needs the library preloaded**, which the image handles
through `shared_preload_libraries`. If this fails on a missing `age.so`, the
database is running an image without AGE — the older `pgvector/pgvector:pg17` —
and the fix is the image, not this file.

**`meridian_rw` owns the graph.** AGE creates a table per label on first use
and attaches it with `ALTER TABLE ... INHERIT`, which Postgres permits only to
the parent's owner — so a writer with `ALL` privileges and no ownership fails
with "must be owner of table _ag_label_vertex" the first time a model writes an
edge. Ownership is transferred here, once, rather than discovered then.

**The graph is called `graph`, not `meridian`.** `create_graph` creates a
Postgres schema of that name, and a schema named after the `meridian` role is
one that `"$user"` resolves to — which silently makes the graph the default
schema for every connection. `alembic check` then proposes dropping AGE's own
label tables, and a `CREATE TABLE` without a schema would put an application
table inside the graph. Both were observed before the rename.

**The search path is set locally, not on the database.** AGE's usual advice is
to put `ag_catalog` first on the database's `search_path` permanently. That
also makes it the first *writable* schema, so a later `CREATE TABLE` with no
schema would land an application table inside AGE's catalog — invisible until
something went looking for it.

Schema-qualifying every call was the first attempt and is not enough:
`create_graph` builds the graph's label tables, and those reference
`graphid_ops` unqualified. Without `ag_catalog` on the path it fails with
`operator class "graphid_ops" does not exist for access method "btree"` — a
name nobody wrote, which reads like a broken install rather than a search path.
So `SET LOCAL` for the transaction, and nothing outside it changes.

**What this deliberately does not decide.** The graph is created and left
empty. Whether AGE becomes the source of truth for nodes and edges, or a
queryable projection of the `entities` and `edges` tables that already exist,
is a real choice with real consequences: every write going two places, and a
reconciliation story for when they disagree. It is cheap to decide now and
expensive after fifty thousand edges, and it is not a decision to make inside a
migration. Nothing writes to this graph yet.

Revision ID: 6abd2d23835d
Revises: 25700ca2364d
Create Date: 2026-09-20 21:03:21.591748
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6abd2d23835d"
down_revision: str | None = "25700ca2364d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The graph AGE creates. One graph rather than one per topic: §7 describes a
#: single connected structure, and a graph per topic would make every
#: cross-topic edge — which is where the interesting ones are — a join between
#: two stores.
#:
#: **Not named after the project, and that is the whole point.** `create_graph`
#: creates a Postgres *schema* of this name. Calling it `meridian` makes that
#: schema share a name with the `meridian` role, so `"$user"` in the default
#: `search_path` starts resolving to it — the graph becomes the default schema,
#: Alembic reflects its internal tables as unqualified and proposes dropping
#: them, and a later `CREATE TABLE` with no schema lands an application table
#: inside the graph. Found by doing it.
GRAPH = "graph"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS age")
    # `SET LOCAL`, so this lasts exactly as long as the transaction.
    #
    # `create_graph` is not merely a function call in `ag_catalog`: it creates
    # the graph's label tables, and those reference `graphid_ops` *unqualified*.
    # Without `ag_catalog` on the path it fails with `operator class
    # "graphid_ops" does not exist for access method "btree"`, which names
    # something nobody wrote and reads like a broken AGE install.
    #
    # Local rather than on the database, because a permanent `ag_catalog` first
    # on the search path also makes it the first writable schema — and then a
    # later `CREATE TABLE` with no schema lands an application table inside
    # AGE's catalog, which looks like nothing at all until something goes
    # looking for it.
    op.execute('SET LOCAL search_path = ag_catalog, "$user", public')
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = '{GRAPH}') THEN
                PERFORM ag_catalog.create_graph('{GRAPH}');
            END IF;
        END
        $$
        """.replace("{GRAPH}", GRAPH)
    )

    # The extension is created by the owner; the application connects as
    # `meridian_rw` and reads as `meridian_ro`. Without this every Cypher call
    # fails with "permission denied for schema ag_catalog" — the graph would
    # exist and be unusable by the only roles that ever touch it, which is the
    # same shape as `P0-18`'s role bootstrap that never ran.
    #
    # **The writer has to *own* the graph, not merely be granted on it.** AGE
    # creates a table per vertex and edge label on first use, and attaches it
    # with `ALTER TABLE ... INHERIT`, which Postgres allows only to the parent's
    # owner. Granting `ALL` is not enough and fails with "must be owner of table
    # _ag_label_vertex" — a message that points at an internal table nobody
    # wrote, the first time a model tries to write an edge.
    op.execute(f"ALTER SCHEMA {GRAPH} OWNER TO meridian_rw")
    for table in ("_ag_label_vertex", "_ag_label_edge"):
        op.execute(f"ALTER TABLE {GRAPH}.{table} OWNER TO meridian_rw")
        op.execute(f"ALTER SEQUENCE {GRAPH}.{table}_id_seq OWNER TO meridian_rw")

    for role, privileges in (("meridian_rw", "ALL"), ("meridian_ro", "SELECT")):
        op.execute(f"GRANT USAGE ON SCHEMA ag_catalog TO {role}")
        op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA ag_catalog TO {role}")
        op.execute(
            f"GRANT {'USAGE, CREATE' if role == 'meridian_rw' else 'USAGE'} "
            f"ON SCHEMA {GRAPH} TO {role}"
        )
        op.execute(f"GRANT {privileges} ON ALL TABLES IN SCHEMA {GRAPH} TO {role}")
        op.execute(f"GRANT {privileges} ON ALL SEQUENCES IN SCHEMA {GRAPH} TO {role}")
        # Label tables are created later, by the first query that uses a label.
        # Without default privileges the reader cannot see anything the writer
        # adds after today — the exact failure `P0-18` found for `public`.
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {GRAPH} GRANT {privileges} ON TABLES TO {role}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE meridian_rw IN SCHEMA {GRAPH} "
            f"GRANT {privileges} ON TABLES TO {role}"
        )


def downgrade() -> None:
    # `cascade` takes the graph's label tables with it. Safe only because
    # nothing writes here yet — once this graph holds edges, a downgrade that
    # drops it is data loss and should become a refusal instead.
    op.execute('SET LOCAL search_path = ag_catalog, "$user", public')
    op.execute(f"SELECT ag_catalog.drop_graph('{GRAPH}', true)".replace("{GRAPH}", GRAPH))
    op.execute("DROP EXTENSION IF EXISTS age")
