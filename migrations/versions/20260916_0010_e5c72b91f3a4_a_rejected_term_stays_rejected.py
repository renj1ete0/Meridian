"""a rejected term stays rejected

Task P6-13. `approved` is a boolean and the approval queue has three states, not
two: waiting, approved, and turned down. Without the third, rejecting a
harvested term can only mean deleting the row — and the next harvest reads the
same documents, finds the same definition, and files it again. The queue would
refill with exactly the terms somebody had already said no to, which is how an
approval queue stops being worth opening.

So the row stays as a tombstone. `approved=false, rejected_at=<when>` is
distinguishable from `approved=false, rejected_at=NULL`, and the harvest reads
the difference: a rejected term is not re-created, not corroborated, and not
auto-approved by a later document agreeing with the one that was wrong.

Reversible on purpose. Clearing `rejected_at` puts the term back in the queue,
because a judgement made on two occurrences is worth revisiting at twenty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c72b91f3a4"
down_revision: str | None = "d1a3f8b57e92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gazetteer",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The queue the admin screen opens on: proposed, still waiting, most
    # corroborated first. Partial, because the pending set is the minority once
    # the harvest has run for a week and everything else has a verdict.
    op.create_index(
        "ix_gazetteer_pending",
        "gazetteer",
        ["occurrence_count"],
        postgresql_where=sa.text("NOT approved AND rejected_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_gazetteer_pending", table_name="gazetteer")
    op.drop_column("gazetteer", "rejected_at")
