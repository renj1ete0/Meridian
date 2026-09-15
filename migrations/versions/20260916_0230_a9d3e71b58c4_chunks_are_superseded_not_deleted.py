"""chunks are superseded, not deleted

Task P1-32. A re-crawl of a changed page deleted the source's chunks and wrote
new ones. `edges.supporting_chunk_ids` is an array of ids with no foreign key
behind it — Postgres cannot enforce one on array elements — so every edge citing
a deleted chunk was left pointing at nothing.

That failure is silent in the worst way. §2.3 makes provenance mandatory on
every edge, and an orphaned edge still *has* provenance: it carries a list of
ids, the row passes every check, and only following the citation reveals there
is nothing there. Nothing in the system looks.

So the old chunks are stamped rather than removed. Three things follow, and all
three are the point rather than side effects:

  * every citation stays resolvable;
  * an edge keeps the text it was actually derived from, which matters because
    §2.4 re-derives from source chunks and the page has since changed;
  * the retention sweep can reclaim the ones nothing cites, which is a decision
    made deliberately by a person rather than at write time by a crawl.

The unique constraint has to become partial with it. The old set keeps its
`chunk_index` values, so a constraint over (source_id, chunk_index) would refuse
exactly the replacement this exists to allow.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3e71b58c4"
down_revision: str | None = "f2b81c46d7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chunks", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Unique among the live chunks only.
    op.drop_constraint("uq_chunks_source_id_chunk_index", "chunks", type_="unique")
    op.create_index(
        "uq_chunks_live_index",
        "chunks",
        ["source_id", "chunk_index"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    # The novelty gate stops judging text that is no longer on the page.
    op.drop_index("ix_chunks_novelty_pending", table_name="chunks")
    op.create_index(
        "ix_chunks_novelty_pending",
        "chunks",
        ["chunk_id"],
        postgresql_where=sa.text(
            "embedding IS NOT NULL AND novelty_checked_at IS NULL AND superseded_at IS NULL"
        ),
    )

    op.create_index(
        "ix_chunks_superseded",
        "chunks",
        ["superseded_at"],
        postgresql_where=sa.text("superseded_at IS NOT NULL"),
    )


def downgrade() -> None:
    # Superseded rows would violate the restored constraint, so they go first.
    # Losing them is the correct reading of a downgrade: the column that
    # distinguished them is about to stop existing.
    op.execute("DELETE FROM chunks WHERE superseded_at IS NOT NULL")

    op.drop_index("ix_chunks_superseded", table_name="chunks")
    op.drop_index("ix_chunks_novelty_pending", table_name="chunks")
    op.create_index(
        "ix_chunks_novelty_pending",
        "chunks",
        ["chunk_id"],
        postgresql_where=sa.text("embedding IS NOT NULL AND novelty_checked_at IS NULL"),
    )

    op.drop_index("uq_chunks_live_index", table_name="chunks")
    op.create_unique_constraint(
        "uq_chunks_source_id_chunk_index", "chunks", ["source_id", "chunk_index"]
    )
    op.drop_column("chunks", "superseded_at")
