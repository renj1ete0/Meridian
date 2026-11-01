"""Where topics meet (task B-72).

Counts kept sources by their exact combination of topic labels (source labels, not passage
labels), beside the kept total they are a part of (`B-240`). See docs/features/topics.md#passages.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Source
from .schemas.search import TopicOverlapRead, TopicOverlapsRead
from .stats import kept_sources


async def topic_overlaps(sess: AsyncSession) -> TopicOverlapsRead:
    """Kept sources by exact topic set, and how many kept sources there are in all."""
    # Grouped as stored, then merged in Python: the same set may be stored in
    # different orders, and there are only as many distinct sets as there are
    # combinations anybody's content falls into — a few dozen, not a scan.
    rows = (
        await sess.execute(
            select(Source.topic_labels, func.count())
            .where(func.cardinality(Source.topic_labels) > 0, *kept_sources())
            .group_by(Source.topic_labels)
        )
    ).all()
    merged: Counter[tuple[str, ...]] = Counter()
    for labels, n in rows:
        merged[tuple(sorted(set(labels)))] += n
    overlaps = [
        TopicOverlapRead(topics=list(topics), sources=n)
        for topics, n in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    kept = await sess.scalar(select(func.count()).select_from(Source).where(*kept_sources()))
    return TopicOverlapsRead(
        overlaps=overlaps, labelled_sources=sum(merged.values()), kept_sources=int(kept or 0)
    )
