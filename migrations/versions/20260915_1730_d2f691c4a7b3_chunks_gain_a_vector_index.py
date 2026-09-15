"""chunks gain a vector index

Task P2-04, the index half. The task is also "measure recall and latency at
corpus size", and that half genuinely waits for `P1-16` — 28 chunks measures
nothing, and an ANN index is exactly the kind of thing that looks perfect until
the corpus is large enough for its recall to matter.

Building it now rather than after the run is deliberate: an HNSW index maintained
incrementally as rows arrive costs nothing per insert at this rate, where building
one over a finished corpus is a single long operation that wants
`maintenance_work_mem` the Pi does not have to spare.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2f691c4a7b3"
down_revision: str | None = "c1e58a3f9b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_chunks_embedding_hnsw"


def upgrade() -> None:
    # `m` and `ef_construction` left at pgvector's defaults (16 / 64). Tuning
    # them is a measurement against a real corpus, not a guess against an empty
    # one, and re-tuning later is a REINDEX rather than a migration.
    op.create_index(
        INDEX,
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="chunks")
