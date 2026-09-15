"""guest read role

Task P3-07. `meridian_ro` has SELECT on every table in the schema, including
`agent_tokens` — whose `token_hash` column is the one secret the database holds
— and `fetch_policy`, which describes how this crawler behaves and where it
looks. That is the right role for the operator's own read path and the wrong one
to put behind anything a guest can reach (`P3-04`'s SQL escape hatch, or any
future shared read surface).

`meridian_guest` gets SELECT on the corpus and the graph, and nothing else.

**Grants live in a migration, the credential does not.** Tables have to exist
before they can be granted on, which makes this schema-scoped work and puts it
here; the password is deployment state and stays in `init-roles.sh` (§11.11 —
credentials come from the environment). The role is created here as NOLOGIN if
it is absent, so an existing database gets a usable role without a password
being invented for it, and `init-roles.sh` gives it LOGIN on new deployments.

**No default privileges, deliberately.** Every other role in this schema has
`ALTER DEFAULT PRIVILEGES` so that migrations' new tables are covered
automatically. This one does not: a table added later is invisible to guests
until somebody grants it explicitly. That is the fail-closed direction, and it
is the correct one here — the cost of forgetting is a guest who cannot read
something they should, which gets reported; the cost of the opposite is a guest
silently reading a table nobody considered, which does not.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f4b8c3e71a25"
down_revision: str | None = "e3a7b2d5c918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "meridian_guest"

#: The corpus and the graph. Everything a reader needs to answer a question and
#: follow it back to a source, and nothing about how the system is run.
READABLE = (
    # corpus
    "sources",
    "chunks",
    "figures",
    # graph
    "entities",
    "edges",
    "attribute_definitions",
    "attribute_values",
    "observations",
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
            -- NOLOGIN: this migration has no business inventing a credential.
            -- `init-roles.sh` grants LOGIN where a password is configured.
            CREATE ROLE {ROLE} NOLOGIN;
          END IF;
        END
        $$;
        """
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table in READABLE:
        op.execute(f"GRANT SELECT ON {table} TO {ROLE}")


def downgrade() -> None:
    # The grants go; the role stays. Dropping a role is cluster-wide and would
    # fail or cascade depending on what else in the cluster references it, and a
    # downgrade that can fail on state this migration never created is worse
    # than one that leaves a privilege-less role behind.
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
