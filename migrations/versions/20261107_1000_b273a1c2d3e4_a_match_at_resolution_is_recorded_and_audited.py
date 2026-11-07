"""a match at resolution is recorded and audited (task B-273)

`resolution_matches` records each mention the high band resolved to an existing node, with
the node as it stood, the score and its signals, and the run and model behind the mention.
The graph audit samples it as a third kind: `audit_kind` gains `match` (still five
characters, so the column's width holds), and a draw counts `match_population`, zero for
draws made before.

Revision ID: b273a1c2d3e4
Revises: p710a1c2d3e4
Create Date: 2026-11-07 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b273a1c2d3e4"
down_revision: str | None = "p710a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_BEFORE = ("edge", "merge")
KINDS = ("edge", "merge", "match")

COUNTS_BEFORE = "edge_population >= 0 AND merge_population >= 0 AND merges_reversed >= 0"
COUNTS = (
    "edge_population >= 0 AND merge_population >= 0 AND match_population >= 0"
    " AND merges_reversed >= 0"
)


def _kinds(kinds: Sequence[str]) -> None:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    op.drop_constraint("audit_kind", "graph_audit_samples", type_="check")
    op.create_check_constraint("audit_kind", "graph_audit_samples", f"kind IN ({values})")


def _counts(check: str) -> None:
    op.drop_constraint("audit_counts_not_negative", "graph_audit_draws", type_="check")
    op.create_check_constraint("audit_counts_not_negative", "graph_audit_draws", check)


def upgrade() -> None:
    op.create_table(
        "resolution_matches",
        sa.Column("match_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("mention", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("jurisdiction", sa.Text(), nullable=True),
        sa.Column("supporting_chunk_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("node_name", sa.Text(), nullable=False),
        sa.Column("node_aliases", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("node_chunk_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("produced_by", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("quality_tier", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "btrim(mention) <> ''", name=op.f("ck_resolution_matches_match_mention_named")
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name=op.f("ck_resolution_matches_match_score_in_range")
        ),
        sa.PrimaryKeyConstraint("match_id", name=op.f("pk_resolution_matches")),
    )
    op.create_index(op.f("ix_resolution_matches_at"), "resolution_matches", ["at"], unique=False)
    op.create_index(
        op.f("ix_resolution_matches_entity_id"), "resolution_matches", ["entity_id"], unique=False
    )

    op.add_column(
        "graph_audit_draws",
        sa.Column("match_population", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    _counts(COUNTS)
    _kinds(KINDS)


def downgrade() -> None:
    # A sampled match means nothing before this revision; it goes rather than failing the CHECK.
    op.execute("DELETE FROM graph_audit_samples WHERE kind = 'match'")
    _kinds(KINDS_BEFORE)
    _counts(COUNTS_BEFORE)
    op.drop_column("graph_audit_draws", "match_population")
    op.drop_index(op.f("ix_resolution_matches_entity_id"), table_name="resolution_matches")
    op.drop_index(op.f("ix_resolution_matches_at"), table_name="resolution_matches")
    op.drop_table("resolution_matches")
