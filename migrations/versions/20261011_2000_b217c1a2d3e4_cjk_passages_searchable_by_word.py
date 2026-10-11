"""CJK passages searchable by word (task B-217)

Revision ID: b217c1a2d3e4
Revises: b216c0a1e5f2
Create Date: 2026-10-11 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b217c1a2d3e4"
down_revision: str | None = "b216c0a1e5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Written out rather than imported: a migration must not change when the code does.
#: `meridian_core.cjk.CJK_CLASS` is held to it by a test.
CJK_CLASS = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"

INDEX = "ix_chunks_cjk_search"


def upgrade() -> None:
    # Each run of CJK characters becomes its overlapping pairs; other text is unchanged.
    # See docs/features/search.md#cjk.
    op.execute(
        f"""
        CREATE FUNCTION meridian_cjk_bigrams(t text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $f$
          SELECT coalesce(string_agg(
            CASE WHEN length(m) > 1 AND m ~ '^[{CJK_CLASS}]+$'
                 THEN (SELECT string_agg(substr(m, i, 2), ' ')
                       FROM generate_series(1, length(m) - 1) i)
                 ELSE m END, ' '), '')
          FROM regexp_matches(t, '[{CJK_CLASS}]+|[^{CJK_CLASS}]+', 'g') r(a),
               LATERAL (SELECT a[1] AS m) x
        $f$
        """
    )
    # Partial: about 2% of passages hold any of these characters, and only those are read.
    op.execute(
        f"""
        CREATE INDEX {INDEX} ON chunks
        USING gin (to_tsvector('simple'::regconfig, meridian_cjk_bigrams(text)))
        WHERE text ~ '[{CJK_CLASS}]'
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.execute("DROP FUNCTION IF EXISTS meridian_cjk_bigrams(text)")
