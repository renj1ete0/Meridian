"""chunks gain a ranking index

Task B-65. The lexical arm's cost was never finding matches — the GIN index does
that — but ranking them: `ts_rank_cd` reads every match's `search_vector` back
out of the heap and TOAST, so a single common word matching tens of thousands of
passages took 200–570 ms on the live corpus. RUM stores term positions in the
index and returns matches in rank order (`<=>`), so ranking reads the index only.

GIN stays. It serves every unranked `@@` (watches, area views) and costs a tenth
of RUM per insert.

**Needs the image with RUM** (`deploy/postgres/Dockerfile`). If this fails on
`could not open extension control file ".../rum.control"`, the database is
running an older image — the fix is the image, not this file.

Not `CONCURRENTLY`: no migration here runs outside a transaction, and on a corpus
of ~600k passages the build takes about a minute, during which chunk writes wait.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7c2e4a91b6d3"
down_revision: str | None = "0f94609cd9c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_chunks_search_rum"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS rum")
    op.create_index(
        INDEX,
        "chunks",
        ["search_vector"],
        postgresql_using="rum",
        postgresql_ops={"search_vector": "rum_tsvector_ops"},
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="chunks")
    op.execute("DROP EXTENSION IF EXISTS rum")
