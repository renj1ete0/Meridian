"""audited by grant not by token

Task P3-11, shared-read-access §6. "What has this person's model been reading"
is the question worth answering later, and a per-token log cannot answer it
once they hold three clients — you would be joining logs by hand and hoping you
found them all. So the grant is the index and the token is a column.

**Arguments are kept; results are not.** What somebody searched for is the
audit. What came back is the corpus, and copying it here would be a second
store of the same content with none of the retention rules the first one has
(§5.4). `rows` answers "how much" without keeping any of it.

`refused` is not an afterthought. A log of successful calls answers half the
question; a grant being repeatedly refused a tool is the more interesting
signal, and it is the one that disappears if only successes are recorded.

High volume by design, like `fetch_attempts`, and prunable the same way.

Revision ID: 25700ca2364d
Revises: 080d8191f56c
Create Date: 2026-09-20 20:35:25.511674
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "25700ca2364d"
down_revision: str | None = "080d8191f56c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grant_audit",
        sa.Column("audit_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("grant_id", sa.BigInteger(), nullable=False),
        sa.Column("token_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("refused", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["grants.grant_id"],
            name=op.f("fk_grant_audit_grant_id_grants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("audit_id", name=op.f("pk_grant_audit")),
    )
    op.create_index(op.f("ix_grant_audit_at"), "grant_audit", ["at"], unique=False)
    op.create_index(op.f("ix_grant_audit_grant_id"), "grant_audit", ["grant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_grant_audit_grant_id"), table_name="grant_audit")
    op.drop_index(op.f("ix_grant_audit_at"), table_name="grant_audit")
    op.drop_table("grant_audit")
