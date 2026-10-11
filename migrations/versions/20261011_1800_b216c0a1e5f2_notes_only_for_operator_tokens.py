"""a token records its profile; guests read no notes (task B-216)

Revision ID: b216c0a1e5f2
Revises: b201a1d4a300
Create Date: 2026-10-11 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b216c0a1e5f2"
down_revision: str | None = "b201a1d4a300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUEST = "meridian_guest"

#: Per table, the rows a guest may not see: the operator's notes and the edges attaching
#: them. Every other role keeps every row. See docs/features/mcp.md#grants-design.
HIDDEN_FROM_GUEST = {
    "entities": "NOT is_annotation",
    "edges": "relation_type <> 'annotates'",
}


def upgrade() -> None:
    # NULL for tokens issued before the profile was recorded: they see no notes until
    # reissued, since `analyst` and `operator` carry the same tools and cannot be told apart.
    op.add_column(
        "agent_tokens",
        sa.Column(
            "profile",
            sa.Enum(
                "reader",
                "analyst",
                "operator",
                name="grant_profile",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE agent_tokens SET profile = 'reader'
        WHERE NOT ('run_readonly_query' = ANY (coalesce(allowed_tools, '{}')))
          AND cardinality(coalesce(allowed_tools, '{}')) > 0
        """
    )

    for table, visible in HIDDEN_FROM_GUEST.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # Row security denies every row a policy does not admit, so the other roles need
        # one admitting all; the table owner bypasses it.
        op.execute(f"CREATE POLICY every_row ON {table} USING (true) WITH CHECK (true)")
        op.execute(
            f"CREATE POLICY guest_no_notes ON {table} AS RESTRICTIVE FOR SELECT "
            f"TO {GUEST} USING ({visible})"
        )


def downgrade() -> None:
    for table in HIDDEN_FROM_GUEST:
        op.execute(f"DROP POLICY IF EXISTS guest_no_notes ON {table}")
        op.execute(f"DROP POLICY IF EXISTS every_row ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_column("agent_tokens", "profile")
