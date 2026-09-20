"""caps exist before the first autonomous run

Task P4-10. §16 lists runaway cost from the seed→ingest→cost feedback loop as
"manageable if caps are set before first autonomous run" — a mitigation with an
ordering requirement inside it, and nothing enforced the ordering. This table is
what "set" means; `P4-13` is what refuses to start a run without it.

**One row, and the database says so.** A settings table that *can* hold two rows
eventually does, and then "the budget" is whichever row the query ordered first.
The CHECK turns the second insert into an error rather than an ambiguity, and
makes `WHERE budget_id = 1` the only access pattern there is.

**Every cap is nullable, and null means unconfigured rather than unlimited.**
`reserve_seeds` already refuses a `None` cap for exactly that reason (§16, and
`P4-05`). A row that exists with `max_seeds_per_run` empty has not been
configured for seeds and the run does not start — because "nobody decided" is
the shape in which a missing config becomes a bill.

The positivity CHECKs are here for the same reason the single-row one is: a cap
of `0` or `-1` is a configuration mistake that would otherwise read as a very
strict limit, and the failure would look like a broken orchestrator rather than
a typo.

Revision ID: 070a7b2e2deb
Revises: a242cc7e53e5
Create Date: 2026-09-20 17:55:10.820849
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "070a7b2e2deb"
down_revision: str | None = "a242cc7e53e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_config",
        sa.Column("budget_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("max_tokens_per_run", sa.Integer(), nullable=True),
        sa.Column("max_seeds_per_run", sa.Integer(), nullable=True),
        sa.Column("monthly_cost_ceiling_usd", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("budget_id = 1", name=op.f("ck_budget_config_single_row")),
        sa.CheckConstraint(
            "max_seeds_per_run IS NULL OR max_seeds_per_run > 0",
            name=op.f("ck_budget_config_seeds_positive"),
        ),
        sa.CheckConstraint(
            "max_tokens_per_run IS NULL OR max_tokens_per_run > 0",
            name=op.f("ck_budget_config_tokens_positive"),
        ),
        sa.CheckConstraint(
            "monthly_cost_ceiling_usd IS NULL OR monthly_cost_ceiling_usd > 0",
            name=op.f("ck_budget_config_ceiling_positive"),
        ),
        sa.PrimaryKeyConstraint("budget_id", name=op.f("pk_budget_config")),
    )
    op.create_index(
        op.f("ix_budget_config_created_at"), "budget_config", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_budget_config_created_at"), table_name="budget_config")
    op.drop_table("budget_config")
