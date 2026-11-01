"""The topics as a reader meets them (task B-238): description, size, newest, fields.

Read-only, for `/api/explore/topics`. Counted with :func:`stats.kept_sources`, the rule the
landing's counts and Growth use. See docs/features/web-app.md#topics-on-the-landing.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .areaview import area_name, latest_build
from .models import Area, Source, TopicConfig
from .schemas.search import TopicCardRead, TopicCardsRead, TopicFieldRead, TopicNewestRead
from .stats import kept_sources

#: Map fields named per topic: enough to say what a topic spans, few enough to read.
FIELDS_SHOWN = 3

#: The Map's level that names fields of work (1 is regions, the deepest the leaves).
FIELD_LEVEL = 2


async def topic_cards(sess: AsyncSession, *, today: dt.date | None = None) -> TopicCardsRead:
    """Every topic not archived, heaviest first, as the landing lists them.

    A publication date after ``today`` is a parsing error, not news, so it never makes a
    source the newest.
    """
    today = today or dt.datetime.now(dt.UTC).date()
    configs = list(
        await sess.scalars(
            select(TopicConfig)
            .where(TopicConfig.status != "archived")
            .order_by(TopicConfig.weight.desc(), TopicConfig.topic)
        )
    )
    build = await latest_build(sess)

    cards: list[TopicCardRead] = []
    for config in configs:
        on_topic = (Source.topic_labels.contains([config.topic]), *kept_sources())
        sources = int(
            await sess.scalar(select(func.count()).select_from(Source).where(*on_topic)) or 0
        )
        newest_row = (
            await sess.execute(
                select(Source.source_id, Source.title, Source.publication_date)
                .where(*on_topic, Source.publication_date <= today)
                .order_by(Source.publication_date.desc(), Source.source_id.desc())
                .limit(1)
            )
        ).first()
        newest = (
            TopicNewestRead(source_id=newest_row[0], title=newest_row[1], published=newest_row[2])
            if newest_row
            else None
        )

        fields: list[TopicFieldRead] = []
        if build is not None:
            share = Area.topic_mix[config.topic].astext.cast(Integer)
            rows = (
                await sess.execute(
                    select(Area, share)
                    .where(
                        Area.build_id == build.build_id,
                        Area.level == FIELD_LEVEL,
                        Area.topic_mix.has_key(config.topic),
                    )
                    .order_by(share.desc(), Area.area_id)
                    .limit(FIELDS_SHOWN)
                )
            ).all()
            fields = [
                TopicFieldRead(
                    area_id=area.area_id,
                    parent_id=area.parent_id,
                    name=area_name(area.terms, area.field),
                    passages=int(passages),
                )
                for area, passages in rows
                if passages
            ]

        cards.append(
            TopicCardRead(
                topic=config.topic,
                description=config.description,
                sources=sources,
                newest=newest,
                fields=fields,
            )
        )
    return TopicCardsRead(topics=cards)
