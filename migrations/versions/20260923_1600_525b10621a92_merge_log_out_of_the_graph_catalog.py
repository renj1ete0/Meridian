"""merge_log out of the graph catalog

Task B-38. On a database migrated from empty in one run, the graph-store
revision's `SET LOCAL search_path = ag_catalog, ...` was still in force when
the next revision created `merge_log`, so the table was created inside AGE's
`ag_catalog` schema — where the application roles neither look for it nor hold
any grant on it. Every merge failed with "relation merge_log does not exist".
Databases migrated a revision at a time (the dev database) are unaffected.

The graph-store revision now restores the search path itself. This repairs a
database that already has the misplaced table: moved to `public` (its indexes,
constraints and sequence move with it) and granted as the default privileges
would have granted it there. A database where it is already in `public` is left
untouched.

Revision ID: 525b10621a92
Revises: 5d2e9f1a7c30
Create Date: 2026-09-23 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "525b10621a92"
down_revision: str | Sequence[str] | None = "5d2e9f1a7c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('ag_catalog.merge_log') IS NOT NULL
               AND to_regclass('public.merge_log') IS NULL THEN
                ALTER TABLE ag_catalog.merge_log SET SCHEMA public;
                GRANT ALL ON public.merge_log TO meridian_rw;
                GRANT SELECT ON public.merge_log TO meridian_ro;
                GRANT ALL ON SEQUENCE public.merge_log_merge_id_seq TO meridian_rw;
                GRANT SELECT ON SEQUENCE public.merge_log_merge_id_seq TO meridian_ro;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # Nothing to undo: putting the table back inside AGE's catalog would
    # re-break every merge.
    pass
