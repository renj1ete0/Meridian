"""How the corpus grew, day by day (task `B-140`, ADRs 0005 and 0010).

Counts what the database holds, by calendar day in the display zone (ADR 0009). Searchable
material only: no junk, no copies, no superseded passages. A page about one topic counts for
it; a page about two or more of the shown topics counts once, as "multi", so a day's stack adds
up to pages rather than labels. A day the crawl fetched nothing is flagged, so it is drawn as a
gap rather than read as a day that found nothing. See docs/features/growth.md.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from .chunks import _host_of
from .models import (
    AreaBuildHistory,
    Chunk,
    ChunkTopics,
    Edge,
    Entity,
    FetchAttempt,
    Source,
    TopicConfig,
)
from .schemas.growth import (
    GrowthCount,
    GrowthDay,
    GrowthRead,
    MapSize,
    TopicGrowth,
)
from .stats import kept_sources, live_entities

#: Windows the page offers; None is all time.
WINDOWS: tuple[int | None, ...] = (7, 30, None)
DEFAULT_DAYS = 30


def _local_day(column, zone: str):
    return cast(func.timezone(literal(zone), column), Date)


def _kept():
    return kept_sources()


def window_start(
    now: dt.datetime, zone: str, days: int | None
) -> tuple[dt.date | None, dt.datetime | None]:
    """The first local day of the window and the instant it starts; (None, None) for all time."""
    if days is None:
        return None, None
    today = now.astimezone(ZoneInfo(zone)).date()
    first = today - dt.timedelta(days=days - 1)
    start = dt.datetime.combine(first, dt.time(), tzinfo=ZoneInfo(zone)).astimezone(dt.UTC)
    return first, start


def split_labels(labels: Sequence[str], shown: set[str] | None) -> str | None:
    """Which bucket a page belongs in: its one shown topic, "multi", or None to leave it out."""
    kept = [label for label in labels if shown is None or label in shown]
    if not kept:
        return None
    return kept[0] if len(kept) == 1 else "multi"


async def growth(
    sess: AsyncSession,
    *,
    zone: str,
    days: int | None = DEFAULT_DAYS,
    topics: Sequence[str] | None = None,
    now: dt.datetime | None = None,
) -> GrowthRead:
    """Everything the growth page draws, for one window and topic filter."""
    now = now or dt.datetime.now(dt.UTC)
    first, start = window_start(now, zone, days)
    shown = set(topics) if topics else None

    def counts(column):
        """(total, in window) as of ``now``; all time counts the whole total as in window."""
        recent = func.count().filter(column >= start) if start is not None else func.count()
        return func.count(), recent

    every = sorted(set(await sess.scalars(select(TopicConfig.topic))))
    series = {name: index for index, name in enumerate(every)}

    # Pages per day, by topic.
    day = _local_day(Source.created_at, zone)
    stmt = select(day, Source.topic_labels).where(
        *_kept(), func.cardinality(Source.topic_labels) > 0, Source.created_at <= now
    )
    if start is not None:
        stmt = stmt.where(Source.created_at >= start)
    per_day: dict[dt.date, Counter[str]] = defaultdict(Counter)
    per_topic_day: dict[str, Counter[dt.date]] = defaultdict(Counter)
    for when, labels in await sess.execute(stmt):
        bucket = split_labels(labels or [], shown)
        if bucket is None:
            continue
        per_day[when][bucket] += 1
        for label in labels:
            if shown is None or label in shown:
                per_topic_day[label][when] += 1

    # Days the crawl fetched anything (attempts are pruned after a month; a day with a kept page
    # was certainly crawled).
    attempt_day = _local_day(FetchAttempt.attempted_at, zone)
    crawled_q = select(attempt_day).distinct().where(FetchAttempt.attempted_at <= now)
    if start is not None:
        crawled_q = crawled_q.where(FetchAttempt.attempted_at >= start)
    crawled = set(await sess.scalars(crawled_q)) | set(per_day)

    # New sites: hosts whose first kept page arrived in the window.
    host = _host_of(Source.url)
    firsts = (
        select(host.label("host"), func.min(Source.created_at).label("first"))
        .where(*_kept(), Source.created_at <= now)
        .group_by(host)
        .subquery()
    )
    first_day = _local_day(firsts.c.first, zone)
    site_rows = await sess.execute(select(first_day, func.count()).group_by(first_day))
    sites_by_day = {when: n for when, n in site_rows}
    sites_total = sum(sites_by_day.values())

    # The days of the window, oldest first.
    if first is None:
        known = sorted(set(per_day) | crawled | set(sites_by_day))
        first_shown = known[0] if known else None
    else:
        first_shown = first
    today = now.astimezone(ZoneInfo(zone)).date()
    calendar: list[dt.date] = []
    if first_shown is not None:
        cursor = first_shown
        while cursor <= today:
            calendar.append(cursor)
            cursor += dt.timedelta(days=1)

    daily = [
        GrowthDay(
            day=when,
            crawled=when in crawled,
            by_topic={k: v for k, v in sorted(per_day[when].items()) if k != "multi"},
            multi=per_day[when]["multi"],
            new_sites=sites_by_day.get(when, 0),
        )
        for when in calendar
    ]

    # Passages on a topic: live passages carrying a label, per topic and in all.
    passage_filter = [
        Chunk.superseded_at.is_(None),
        func.cardinality(ChunkTopics.topic_labels) > 0,
        Chunk.created_at <= now,
    ]
    if shown is not None:
        passage_filter.append(ChunkTopics.topic_labels.op("&&")(list(shown)))
    base = (
        select(*counts(Chunk.created_at))
        .select_from(ChunkTopics)
        .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
        .where(*passage_filter)
    )
    passages_total, passages_window = (await sess.execute(base)).one()

    label = func.unnest(ChunkTopics.topic_labels).label("label")
    inner = (
        select(label, Chunk.created_at.label("created"))
        .join(Chunk, Chunk.chunk_id == ChunkTopics.chunk_id)
        .where(Chunk.superseded_at.is_(None), Chunk.created_at <= now)
        .subquery()
    )
    per_label = await sess.execute(
        select(inner.c.label, *counts(inner.c.created)).group_by(inner.c.label)
    )
    passages_by_topic = {name: (total, recent) for name, total, recent in per_label}

    def topic_row(name: str) -> TopicGrowth:
        total, recent = passages_by_topic.get(name, (0, 0))
        return TopicGrowth(
            topic=name,
            series=series.get(name, len(series)),
            passages=GrowthCount(total=total, in_window=recent),
            daily=[per_topic_day[name][when] for when in calendar],
        )

    all_topics = [topic_row(name) for name in sorted({*every, *passages_by_topic, *per_topic_day})]
    shown_names = sorted(shown) if shown else [t.topic for t in all_topics if t.passages.total]

    # Sources kept.
    src_filter = [*_kept(), Source.created_at <= now]
    if shown is not None:
        src_filter.append(Source.topic_labels.op("&&")(list(shown)))
    sources_total, sources_window = (
        await sess.execute(select(*counts(Source.created_at)).where(*src_filter))
    ).one()

    # The graph: concepts (not notes, not merged away) and links. Not narrowed by topic.
    concepts_total, concepts_window = (
        await sess.execute(
            select(*counts(Entity.created_at)).where(*live_entities(), Entity.created_at <= now)
        )
    ).one()
    links_total, links_window = (
        await sess.execute(select(*counts(Edge.created_at)).where(Edge.created_at <= now))
    ).one()

    # The map: every recorded build in the window, and the newest.
    history_q = select(AreaBuildHistory).order_by(AreaBuildHistory.computed_at)
    history_all = list(await sess.scalars(history_q.where(AreaBuildHistory.computed_at <= now)))
    map_history = [
        MapSize.model_validate(row, from_attributes=True)
        for row in history_all
        if start is None or row.computed_at >= start
    ]
    map_now = MapSize.model_validate(history_all[-1], from_attributes=True) if history_all else None

    return GrowthRead(
        as_of=now,
        zone=zone,
        days=days,
        first_day=first_shown,
        topics=shown_names,
        all_topics=all_topics,
        passages=GrowthCount(total=passages_total, in_window=passages_window),
        sources=GrowthCount(total=sources_total, in_window=sources_window),
        sites=GrowthCount(
            total=sites_total,
            in_window=sum(n for when, n in sites_by_day.items() if first is None or when >= first),
        ),
        concepts=GrowthCount(total=concepts_total, in_window=concepts_window),
        links=GrowthCount(total=links_total, in_window=links_window),
        daily=daily,
        map_now=map_now,
        map_history=map_history,
        map_history_from=history_all[0].computed_at if history_all else None,
    )


__all__ = ["DEFAULT_DAYS", "WINDOWS", "growth", "split_labels", "window_start"]
