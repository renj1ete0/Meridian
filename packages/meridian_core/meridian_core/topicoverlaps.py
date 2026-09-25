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

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Source
from .schemas.search import TopicOverlapRead, TopicOverlapsRead


async def topic_overlaps(sess: AsyncSession) -> TopicOverlapsRead:
    # Grouped as stored, then merged in Python: the same set may be stored in
    # different orders, and there are only as many distinct sets as there are
    # combinations anybody's content falls into — a few dozen, not a scan.
    rows = (
        await sess.execute(
            select(Source.topic_labels, func.count())
            .where(
                func.cardinality(Source.topic_labels) > 0,
                Source.retention_tier != "junk",
                Source.duplicate_of.is_(None),
            )
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
    return TopicOverlapsRead(overlaps=overlaps, labelled_sources=sum(merged.values()))
