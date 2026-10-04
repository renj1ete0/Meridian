"""a half-precision vector index on passages (task B-136, ADR 0007)

Benchmarked on 200,000 passages against exact nearest neighbours: the half-precision index
was a third of the size, recall@10 within a point at the default search width, and faster
at the median and the tail. Stored vectors stay full precision; only the index changes,
and queries order by the same expression (`meridian_core.vectorindex`).

Built concurrently so the crawl and the embedder keep writing while it builds, then the old
index is dropped. On a large corpus the build takes minutes; give Postgres enough
`maintenance_work_mem` and `shm_size` (`B-132`, `B-144`).

Revision ID: b136ba1f0000
Revises: b137a11ce0de
Create Date: 2026-10-04 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b136ba1f0000"
down_revision: str | None = "b137a11ce0de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chunks_embedding_hnsw_half ON chunks "
            "USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chunks_embedding_hnsw")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chunks_embedding_hnsw_half")
