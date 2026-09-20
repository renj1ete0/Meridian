"""a list of ids is an array, not a json document

Task B-10. `figures.linked_entity_ids` was the only `json` column in the schema
and the only list of ids not stored as `ARRAY(BigInteger)` — `entities.
merged_from` and the four `supporting_chunk_ids` columns are the same shape and
the same use. Nothing chose the difference; it is what happens when a column is
added by copying a nearby one rather than the one it resembles.

`json` keeps the literal document text. So `'[1, 2]'` and `'[1,2]'` are unequal
values, whitespace and key order are preserved for nothing, there is no
containment operator to index against, and reading one back means parsing JSON
to recover integers Postgres could have handed over directly.

**Why this is four statements and not one `ALTER ... TYPE`.** There is no
implicit cast from `json` to `bigint[]`, so the type change needs a `USING`
expression — and converting a JSON array to a SQL array requires aggregating
`jsonb_array_elements_text`, which is a subquery, which Postgres rejects in
`USING` ("cannot use subquery in transform expression"). A new column, an
`UPDATE` that may contain a subquery, a drop and a rename is the way round it,
and it has the better property anyway: the values go through a real JSON parse
rather than string surgery on the literal text.

Autogenerate's bare `ALTER COLUMN ... TYPE` would have failed at deploy time on
any database with figures in it, and passed here — `figures` is empty in
development, which is exactly the condition that lets a broken migration
through. It was caught by running it against rows put there on purpose.

The column moves to the end of the table's column order, which nothing depends
on: every read goes through the ORM or names its columns.

Revision ID: a242cc7e53e5
Revises: e6c84b2d719f
Create Date: 2026-09-20 15:22:24.692063
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a242cc7e53e5"
down_revision: str | None = "e6c84b2d719f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE figures ADD COLUMN linked_entity_ids_arr BIGINT[]")
    # NULL stays NULL: "no entities linked" and "linked to the empty list" are
    # different facts, and the `WHERE` is what keeps them different — an
    # unfiltered `ARRAY(SELECT ...)` over a NULL input yields `{}`, not NULL.
    op.execute(
        """
        UPDATE figures
           SET linked_entity_ids_arr = ARRAY(
                   SELECT jsonb_array_elements_text(linked_entity_ids::jsonb)::bigint
               )
         WHERE linked_entity_ids IS NOT NULL
        """
    )
    op.execute("ALTER TABLE figures DROP COLUMN linked_entity_ids")
    op.execute("ALTER TABLE figures RENAME COLUMN linked_entity_ids_arr TO linked_entity_ids")


def downgrade() -> None:
    op.execute("ALTER TABLE figures ADD COLUMN linked_entity_ids_json JSON")
    op.execute(
        """
        UPDATE figures
           SET linked_entity_ids_json = to_jsonb(linked_entity_ids)::json
         WHERE linked_entity_ids IS NOT NULL
        """
    )
    op.execute("ALTER TABLE figures DROP COLUMN linked_entity_ids")
    op.execute("ALTER TABLE figures RENAME COLUMN linked_entity_ids_json TO linked_entity_ids")
