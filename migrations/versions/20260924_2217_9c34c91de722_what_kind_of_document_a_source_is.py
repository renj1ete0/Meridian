"""what kind of document a source is (task B-59)

`sources.doc_kind`, nullable: NULL is "not yet classified", which is the
backfill's queue. The CHECK is written by hand rather than left to the column
type: SQLAlchemy happens to emit it for a column added with a constrained
type, but autogenerate does not see CHECKs on existing tables at all (see the
handover), so relying on the implicit one would leave the next change to this
value set with nothing to drop.

Revision ID: 9c34c91de722
Revises: baa1aa041c08
Create Date: 2026-09-24 22:17:46.039463
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c34c91de722"
down_revision: str | None = "baa1aa041c08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The bare name: the metadata naming convention expands it to
# ck_sources_doc_kind on both create and drop.
CONSTRAINT = "doc_kind"

KINDS = ("paper", "report", "news", "legal", "profile", "listing", "other")


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "doc_kind",
            sa.Enum(*KINDS, name="doc_kind", native_enum=False, create_constraint=False),
            nullable=True,
        ),
    )
    op.create_index(op.f("ix_sources_doc_kind"), "sources", ["doc_kind"], unique=False)
    values = ", ".join(f"'{kind}'" for kind in KINDS)
    op.create_check_constraint(CONSTRAINT, "sources", f"doc_kind IN ({values})")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "sources", type_="check")
    op.drop_index(op.f("ix_sources_doc_kind"), table_name="sources")
    op.drop_column("sources", "doc_kind")
