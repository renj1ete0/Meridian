"""a task type nothing routes

Task P4-07, §11.3. The seeded registry gave the local tier `tagging` while
every other row, and every caller, says `tag_attributes`. `agents.task_types`
is `text[]`: Postgres accepts anything, routing matches on equality, and an
agent naming a type nobody asks for is simply never selected. The symptom is
not an error — it is the frontier model quietly doing narrow, schema-constrained
work that was configured to go to a cheap one.

**A migration rather than an edit to `config/agents.yaml`.** The seed is
insert-only on purpose — re-seeding must not undo steering (`P1-07`) — so
fixing the YAML fixes new installs and leaves every existing database wrong.
The YAML is fixed too; this is the half that reaches a database that has
already been seeded.

**Scoped to the one known-bad value**, not a rewrite of the column. An operator
who has since added their own agents has rows this knows nothing about, and a
migration that normalised everything it did not recognise would be destroying
configuration to enforce a rule the registry has only just acquired.

Revision ID: c1f4a0d7e9b2
Revises: 6b85d1a408c2
Create Date: 2026-09-20 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c1f4a0d7e9b2"
down_revision: str | Sequence[str] | None = "6b85d1a408c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WRONG = "tagging"
RIGHT = "tag_attributes"


def upgrade() -> None:
    # `array_replace` rather than a read-modify-write: it touches only the
    # element, leaves the rest of the array in its stated order, and does
    # nothing at all to a row that does not contain the value.
    op.execute(
        f"""
        UPDATE agents
           SET task_types = array_replace(task_types, '{WRONG}', '{RIGHT}')
         WHERE task_types @> ARRAY['{WRONG}']::text[]
        """
    )

    # A row that declared both would now hold `tag_attributes` twice. Harmless
    # to routing, which tests membership, but a duplicate in a config column is
    # the kind of thing somebody later reads as significant.
    op.execute(
        """
        UPDATE agents
           SET task_types = (
                 SELECT array_agg(DISTINCT t ORDER BY t)
                   FROM unnest(task_types) AS t
               )
         WHERE array_length(task_types, 1)
               <> (SELECT count(DISTINCT t) FROM unnest(task_types) AS t)
        """
    )


def downgrade() -> None:
    # Reverses the rename. The de-duplication is not reversed: which duplicate
    # came first is not recorded, and inventing an order would be worse than
    # leaving the column tidy.
    op.execute(
        f"""
        UPDATE agents
           SET task_types = array_replace(task_types, '{RIGHT}', '{WRONG}')
         WHERE agent_id = 'local-llamacpp'
           AND task_types @> ARRAY['{RIGHT}']::text[]
        """
    )
