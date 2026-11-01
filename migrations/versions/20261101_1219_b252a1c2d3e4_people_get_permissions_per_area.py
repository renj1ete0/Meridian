"""people get permissions per area (task B-252)

Each grant gains switches (`allow`, `deny`) that add or remove single permissions from its
profile, held by CHECKs to the catalogue in `meridian_core.permissions` (written out here, as
a migration must not change when the code does; a test compares the two), and
`grant_changes` records who changed a grant's access. ADR 0024.

Revision ID: b252a1c2d3e4
Revises: b249a0c1d2e3
Create Date: 2026-11-01 12:19:25.915708
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b252a1c2d3e4"
down_revision: str | None = "b249a0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = (
    "explore.read",
    "admin.view",
    "work.write",
    "chat.ask",
    "topics.steer",
    "seeds.add",
    "gazetteer.curate",
    "duplicates.decide",
    "fetch_policy.edit",
    "budget.edit",
    "models.edit",
    "settings.edit",
    "access.manage",
)
_LIST = ", ".join(f"'{name}'" for name in PERMISSIONS)


def upgrade() -> None:
    op.create_table(
        "grant_changes",
        sa.Column("change_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("grant_id", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["grants.grant_id"],
            name=op.f("fk_grant_changes_grant_id_grants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("change_id", name=op.f("pk_grant_changes")),
    )
    op.create_index(op.f("ix_grant_changes_grant_id"), "grant_changes", ["grant_id"], unique=False)
    op.add_column(
        "grants",
        sa.Column(
            "allow", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
    )
    op.add_column(
        "grants",
        sa.Column(
            "deny", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
    )
    for column in ("allow", "deny"):
        op.create_check_constraint(
            f"grant_{column}_known", "grants", f"{column} <@ ARRAY[{_LIST}]::text[]"
        )


def downgrade() -> None:
    for column in ("allow", "deny"):
        op.drop_constraint(f"grant_{column}_known", "grants", type_="check")
    op.drop_column("grants", "deny")
    op.drop_column("grants", "allow")
    op.drop_index(op.f("ix_grant_changes_grant_id"), table_name="grant_changes")
    op.drop_table("grant_changes")
