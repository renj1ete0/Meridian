"""Where topics meet (task B-72).

A source carries every topic its content is about (`P2-21`), so the corpus
is a web rather than a partition: some sources sit in two or three topics at
once. This counts them by exact combination, which is what a view of
overlapping topics needs — pick circles, see how many sources lie in all of
them, then search there (``topic_match=all``).

Searchable sources only: no junk, no duplicates, examined and on at least one
topic. Source labels, not passage labels — a count of documents is what a
person weighing "is there anything here" reads.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Source
from .schemas.search import TopicOverlapRead, TopicOverlapsRead


async def topic_overlaps(sess: AsyncSession) -> TopicOverlapsRead:
    ordered = func.array(
        select(func.unnest(Source.topic_labels).label("t")).order_by("t").scalar_subquery()
    )
    rows = (
        await sess.execute(
            select(ordered.label("topics"), func.count())
            .where(
                func.cardinality(Source.topic_labels) > 0,
                Source.retention_tier != "junk",
                Source.duplicate_of.is_(None),
            )
            .group_by("topics")
            .order_by(func.count().desc())
        )
    ).all()
    overlaps = [TopicOverlapRead(topics=list(t), sources=n) for t, n in rows]
    return TopicOverlapsRead(overlaps=overlaps, labelled_sources=sum(o.sources for o in overlaps))
