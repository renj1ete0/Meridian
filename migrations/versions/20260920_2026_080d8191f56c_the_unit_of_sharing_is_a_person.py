"""the unit of sharing is a person

Task P3-06, shared-read-access §3. Somebody given access holds several
credentials — a browser session, an MCP client on a laptop, another on a server
— and revoking their access has to revoke all of them at once. A per-token
model cannot say that: you end up chasing credentials, and the one you miss is
the one that still works.

So `agent_tokens` gains `grant_id` and keeps everything else. The grant says who
and what; a token is one credential under it, and `ON DELETE CASCADE` means
deleting the grant takes them with it.

**`grant_id` is nullable**, and that is not laxness. The operator's own agent
tokens predate grants and are not shared with anybody; a grant for yourself
would be ceremony, and forcing one would mean inventing a subject for a person
who is already the only one here.

The CHECK is the part worth keeping: a `person` grant must have an expiry.
§3 puts it plainly — an access grant with no end is a grant nobody revisits —
and a default nobody sets is how that happens. A `service` grant may be
open-ended because it belongs to a machine somebody is already running.

Revision ID: 080d8191f56c
Revises: 762be04bfbce
Create Date: 2026-09-20 20:26:10.674364
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "080d8191f56c"
down_revision: str | None = "762be04bfbce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grants",
        sa.Column("grant_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "subject_kind",
            sa.Enum(
                "person", "service", name="subject_kind", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
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
            server_default="reader",
            nullable=False,
        ),
        sa.Column("topics", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "max_source_tier",
            sa.Enum(
                "peer_reviewed",
                "government",
                "institutional",
                "press",
                "informal",
                name="source_tier",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("raw_files", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject_kind <> 'person' OR expires_at IS NOT NULL",
            name=op.f("ck_grants_person_grants_expire"),
        ),
        sa.PrimaryKeyConstraint("grant_id", name=op.f("pk_grants")),
    )
    op.create_index(op.f("ix_grants_created_at"), "grants", ["created_at"], unique=False)
    op.create_index(op.f("ix_grants_subject"), "grants", ["subject"], unique=False)
    op.add_column("agent_tokens", sa.Column("grant_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_agent_tokens_grant_id"), "agent_tokens", ["grant_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_agent_tokens_grant_id_grants"),
        "agent_tokens",
        "grants",
        ["grant_id"],
        ["grant_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_agent_tokens_grant_id_grants"), "agent_tokens", type_="foreignkey")
    op.drop_index(op.f("ix_agent_tokens_grant_id"), table_name="agent_tokens")
    op.drop_column("agent_tokens", "grant_id")
    op.drop_index(op.f("ix_grants_subject"), table_name="grants")
    op.drop_index(op.f("ix_grants_created_at"), table_name="grants")
    op.drop_table("grants")
