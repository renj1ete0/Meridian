"""sources remember the acronym harvest

Task P5-02. §5.6's step 2 — "auto-harvest acronym definitions" — is a pass over
text that has already been extracted, and a pass over the whole corpus every
night is a cost that grows with the corpus forever while the answer stops
changing after the first read.

A nullable timestamp plus a partial index, the same shape the novelty gate uses
on `chunks`: NULL is the whole queue, one document is one unit of work, and a
pass killed halfway keeps everything it committed.

A timestamp rather than a boolean because the harvest's rules will change — a
better initialism check, a wider window — and "when was this read" is what makes
a re-harvest targetable. A boolean can only be reset for everything at once.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1a3f8b57e92"
down_revision: str | None = "c9e7f4a1d3b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("acronyms_harvested_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial, because the rows it wants are the shrinking minority: everything
    # with text and not yet read. `text_available` is in the predicate rather
    # than checked afterwards — a metadata-only source has nothing to harvest and
    # would otherwise sit in the queue being re-skipped every run.
    op.create_index(
        "ix_sources_harvest_pending",
        "sources",
        ["source_id"],
        postgresql_where=sa.text("acronyms_harvested_at IS NULL AND text_available"),
    )


def downgrade() -> None:
    op.drop_index("ix_sources_harvest_pending", table_name="sources")
    op.drop_column("sources", "acronyms_harvested_at")
