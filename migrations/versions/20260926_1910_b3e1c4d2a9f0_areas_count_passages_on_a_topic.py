"""areas count the passages that are on a topic (task P6-42)

Nullable: NULL means the build was not measured, which is not the same as none
on a topic. The builds already held are measured here from their membership,
the same way the build counts — leaves from their passages, then each level
from the level below — so the map can shade by on-topic share without waiting
for the next daily build.

Revision ID: b3e1c4d2a9f0
Revises: 6727563a22c2
Create Date: 2026-09-26 19:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b3e1c4d2a9f0"
down_revision: str | None = "6727563a22c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Leaves: the areas passages are members of. Only rows still unmeasured,
#: so a build that counted for itself is left as it counted.
LEAF_SQL = """
        WITH counts AS (
            SELECT am.area_id,
                   count(ct.chunk_id) AS examined,
                   count(*) FILTER (WHERE cardinality(ct.topic_labels) > 0) AS on_topic
              FROM area_members am
              LEFT JOIN chunk_topics ct ON ct.chunk_id = am.chunk_id
             GROUP BY am.area_id
        ), mix AS (
            SELECT area_id, jsonb_object_agg(topic, n) AS topic_mix
              FROM (SELECT am.area_id, t.topic, count(*) AS n
                      FROM area_members am
                      JOIN chunk_topics ct ON ct.chunk_id = am.chunk_id
                      CROSS JOIN LATERAL unnest(ct.topic_labels) AS t(topic)
                     GROUP BY 1, 2) per
             GROUP BY area_id
        )
        UPDATE areas a
           SET examined = c.examined,
               on_topic = c.on_topic,
               topic_mix = coalesce(m.topic_mix, '{}'::jsonb)
          FROM counts c
          LEFT JOIN mix m ON m.area_id = c.area_id
         WHERE a.area_id = c.area_id AND a.examined IS NULL
        """

#: Each coarser level from the one below it. One pass per level; the tree is
#: three deep, and a spare pass changes nothing.
ROLLUP_SQL = """
            WITH kids AS (
                SELECT parent_id,
                       sum(examined) AS examined,
                       sum(on_topic) AS on_topic
                  FROM areas
                 WHERE parent_id IS NOT NULL AND examined IS NOT NULL
                 GROUP BY parent_id
            ), mix AS (
                SELECT parent_id, jsonb_object_agg(key, n) AS topic_mix
                  FROM (SELECT a.parent_id, e.key, sum(e.value::int) AS n
                          FROM areas a
                          CROSS JOIN LATERAL jsonb_each_text(a.topic_mix) AS e
                         WHERE a.parent_id IS NOT NULL AND jsonb_typeof(a.topic_mix) = 'object'
                         GROUP BY 1, 2) per
                 GROUP BY parent_id
            )
            UPDATE areas a
               SET examined = k.examined,
                   on_topic = k.on_topic,
                   topic_mix = coalesce(m.topic_mix, '{}'::jsonb)
              FROM kids k
              LEFT JOIN mix m ON m.parent_id = k.parent_id
             WHERE a.area_id = k.parent_id AND a.examined IS NULL
            """
ROLLUP_PASSES = 3


def upgrade() -> None:
    op.add_column("areas", sa.Column("examined", sa.Integer(), nullable=True))
    op.add_column("areas", sa.Column("on_topic", sa.Integer(), nullable=True))
    op.add_column("areas", sa.Column("topic_mix", JSONB(), nullable=True))

    op.execute(LEAF_SQL)
    for _ in range(ROLLUP_PASSES):
        op.execute(ROLLUP_SQL)


def downgrade() -> None:
    op.drop_column("areas", "topic_mix")
    op.drop_column("areas", "on_topic")
    op.drop_column("areas", "examined")
