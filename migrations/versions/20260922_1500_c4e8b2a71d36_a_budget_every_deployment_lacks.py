"""a budget every deployment lacks

Task B-32, §11.9, §16. `P4-13` refuses to start a synthesis run without a
budget row, which is the right rule and had no way to be satisfied: nothing
ever created one. `config/budget.yaml` now seeds it at first boot, and the
seed is insert-only (§13.1), so every database that already exists keeps its
absence — and keeps deferring every run with "no budget is configured".

**Only where there is no row at all.** An operator who has set caps has made a
decision, and one who cleared a cap has made a stronger one: a null means
unconfigured and refuses, which is deliberate, and overwriting it from a
migration would turn "stop until I think about this" into "carry on with the
default". That is the exact shape in which a missing config becomes a bill.

The numbers match `config/budget.yaml`, and they are small on purpose — a
first day that is cheap tells you what a day costs.

Revision ID: c4e8b2a71d36
Revises: a7c3e5d19f48
Create Date: 2026-09-22 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4e8b2a71d36"
down_revision: str | Sequence[str] | None = "a7c3e5d19f48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO budget_config (
            budget_id, max_tokens_per_run, max_seeds_per_run,
            monthly_cost_ceiling_usd, updated_by, created_at
        )
        VALUES (1, 750000, 25, 25.0, 'migration c4e8b2a71d36', now())
        ON CONFLICT (budget_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Only the row this migration could have written. A budget somebody has
    # since edited is theirs, and removing it would stop synthesis on a
    # deployment that was working.
    op.execute("DELETE FROM budget_config WHERE updated_by = 'migration c4e8b2a71d36'")
