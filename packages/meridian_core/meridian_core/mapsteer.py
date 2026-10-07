"""Steering from the map (task P6-35), through the machinery that already exists.

Each action is a topic boost (`steering.set_boost`), a search seed (`queueing.enqueue`)
or a saved view, logged in `steering_log` with the area. An area steers through the topic
holding :data:`DOMINANT_SHARE` of its passages; "less" of an area about no topic is
refused. See docs/features/map.md#steering-from-an-area.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Literal

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import steering, timefmt
from .areaview import _area_in, _leaves_under, area_name, latest_build
from .models import AreaMember, Chunk, QueueTask, SavedView, Source, TopicConfig
from .queueing import enqueue
from .schemas.areas import AreaSteeringRead, AreaTopicShare

MORE_FACTOR = 1.5
LESS_FACTOR = 0.5
BOOST_DAYS = 14
#: The share of an area's passages one topic needs before the area is
#: steered through that topic.
DOMINANT_SHARE = 0.5
#: A person's seed: above every link and every generated query (70).
SEED_PRIORITY = 100
MIN_SUGGESTION = 3
MAX_SUGGESTION = 200

Action = Literal["more", "less", "watch", "noise"]


class Refused(ValueError):
    """A steer that cannot be done, with the reason in words."""


class AlreadyDone(Refused):
    """The same seed or view exists already."""


@dataclasses.dataclass(frozen=True)
class SteerResult:
    action: str
    area_id: int | None
    topic: str | None
    boost_factor: float | None
    boost_expires_at: dt.datetime | None
    seed_task_ids: list[int]
    view_id: int | None
    #: What happened, in a sentence the map shows.
    message: str
    #: Where it can be undone.
    undo: str
    #: For a noise marking, the key that undoes it (`P6-42`).
    noise_mark: str | None = None


def area_search(area, topic: str | None) -> str:
    """What "more of this area" searches for (`B-106`).

    Its field name, then the topic that holds it, then its first two-word term.
    """
    from .areaview import usable_terms
    from .searchseeds import topic_words

    name = area_name(area.terms, area.field)
    parts = [name]
    if topic:
        words = topic_words(topic)
        if words.lower() not in name.lower():
            parts.append(words)
    phrase = next(
        (t for t in usable_terms(list(area.terms)) if " " in t and t.lower() not in name.lower()),
        None,
    )
    if phrase:
        parts.append(phrase)
    return " ".join(parts)


async def area_topics(sess: AsyncSession, area) -> list[tuple[str | None, int]]:
    """``(primary topic, passages)`` for an area's passages, largest first.

    The primary topic is the source's first content label (`P2-21`); None is a
    source about none of the topics, or not yet examined.
    """
    rows = (
        await sess.execute(
            select(Source.topic_labels[1], func.count())
            .select_from(AreaMember)
            .join(Chunk, Chunk.chunk_id == AreaMember.chunk_id)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(
                AreaMember.build_id == area.build_id, AreaMember.area_id.in_(_leaves_under(area))
            )
            # By position: the subscript is a bind parameter each time it is
            # written, and Postgres will not match two parameters as one expression.
            .group_by(text("1"))
            .order_by(func.count().desc(), text("1"))
        )
    ).all()
    return [(topic, int(n)) for topic, n in rows]


def dominant(
    topics: list[tuple[str | None, int]], configured: set[str] | None = None
) -> str | None:
    """The topic holding at least :data:`DOMINANT_SHARE` of the passages, or None.

    With ``configured``, a label naming no configured topic counts as none: a label can
    outlive its topic's row, and a steer through it would find no weight to move.
    """
    total = sum(n for _, n in topics)
    labelled = [(t, n) for t, n in topics if t and (configured is None or t in configured)]
    if not total or not labelled:
        return None
    topic, n = labelled[0]
    return topic if n / total >= DOMINANT_SHARE else None


async def configured_topics(sess: AsyncSession) -> set[str]:
    return set(await sess.scalars(select(TopicConfig.topic)))


async def _queue_search(
    sess: AsyncSession, query: str, *, topic: str | None, actor: str, reason: str, now: dt.datetime
) -> QueueTask:
    if await sess.scalar(select(QueueTask.task_id).where(QueueTask.url_or_query == query)):
        raise AlreadyDone(f"“{query}” is already on the queue.")
    task = await enqueue(
        sess, query, topic=topic, seed_source="user", task_type="query", priority=SEED_PRIORITY
    )
    await steering.record(
        sess, actor=actor, topic=topic, field="seed", old=None, new=query, reason=reason, now=now
    )
    return task


async def steer_area(
    sess: AsyncSession, area_id: int, action: Action, *, actor: str, now: dt.datetime
) -> SteerResult:
    """More, less or watch one area of the newest build. Flushes; does not commit."""
    build = await latest_build(sess)
    area, _ = await _area_in(sess, build, area_id)
    name = area_name(area.terms, area.field)
    reason = f"from the map: {action} of area “{name}” (build {area.build_id})"
    topic = dominant(await area_topics(sess, area), await configured_topics(sess))

    if action == "watch":
        view_name = f"Area: {name}"[:200]
        if await sess.scalar(select(SavedView.view_id).where(SavedView.name == view_name)):
            raise AlreadyDone(f"“{view_name}” is already a saved view.")
        view = SavedView(
            name=view_name,
            query=area_search(area, topic),
            filters={},
            note=(
                "Watching a map area by its distinctive terms: "
                f"{', '.join(area.terms[:8])}. Areas are rebuilt daily; the view keeps the terms."
            ),
        )
        sess.add(view)
        await sess.flush()
        await steering.record(
            sess,
            actor=actor,
            topic=None,
            field="watch",
            old=None,
            new=view_name,
            reason=reason,
            now=now,
        )
        return SteerResult(
            action,
            area_id,
            None,
            None,
            None,
            [],
            view.view_id,
            f"Saved as the view “{view_name}”; it opens that search with whatever is new.",
            "Explore › saved views — delete it there.",
        )

    if action == "noise":
        return await _mark_noise(sess, area, name, actor=actor, reason=reason, now=now)

    if action == "less" and topic is None:
        raise Refused(
            "This area belongs to no configured topic, so no weight draws it on purpose and "
            "there is "
            "nothing to turn down. It came in by following links; host scores keep the crawl "
            "away from sites whose pages are about none of the topics."
        )

    boost = None
    expires = now + dt.timedelta(days=BOOST_DAYS)
    notes: list[str] = []
    if topic is not None:
        row = await sess.get(TopicConfig, topic)
        if row is None:  # pragma: no cover - dominant() only names configured topics
            raise Refused(f"“{topic}” is not a configured topic.")
        if steering.boost_is_active(row, now=now):
            zone = await timefmt.display_zone(sess)
            ran_to = timefmt.format_instant(row.boost_expires_at, zone, date_only=True)
            notes.append(f"It replaces the ×{row.boost_factor:g} boost that ran to {ran_to}.")
        if row.status != steering.DRAWING:
            notes.append(f"“{topic}” is {row.status}, so the boost applies once it is active.")
        boost = MORE_FACTOR if action == "more" else LESS_FACTOR
        await steering.set_boost(
            sess, topic, factor=boost, expires_at=expires, actor=actor, reason=reason, now=now
        )

    seeds: list[int] = []
    if action == "more":
        query = area_search(area, topic)
        if not query:
            raise Refused("This area has no distinctive terms to search for.")
        try:
            task = await _queue_search(
                sess, query, topic=topic, actor=actor, reason=reason, now=now
            )
            seeds.append(task.task_id)
        except AlreadyDone:
            # With a topic the boost still did something; without one this
            # steer would change nothing at all, which is worth refusing.
            if topic is None:
                raise
            notes.append(f"“{query}” was already queued.")

    parts: list[str] = []
    if boost is not None:
        verb = "boosted" if action == "more" else "turned down"
        parts.append(f"“{topic}” {verb} ×{boost:g} for {BOOST_DAYS} days.")
    elif action == "more":
        parts.append("No configured topic holds most of this area, so no weight was boosted.")
    if seeds:
        parts.append(f"“{area_search(area, topic)}” queued as a search.")
    return SteerResult(
        action,
        area_id,
        topic,
        boost,
        expires if boost is not None else None,
        seeds,
        None,
        " ".join(parts + notes),
        UNDO,
    )


#: Where each kind of steer is reversed.
UNDO = (
    "A boost ends early from Admin › Topics; a queued search can be removed from "
    "Admin's seed list until it is fetched. Both are in the steering log."
)


async def suggest_seed(
    sess: AsyncSession, text: str, *, topic: str | None, actor: str, now: dt.datetime
) -> SteerResult:
    """Queue something new to search for, from empty space on the map."""
    query = " ".join(text.split())
    if not MIN_SUGGESTION <= len(query) <= MAX_SUGGESTION:
        raise Refused(f"A search needs between {MIN_SUGGESTION} and {MAX_SUGGESTION} characters.")
    if topic is not None and await sess.get(TopicConfig, topic) is None:
        raise Refused(f"“{topic}” is not a configured topic.")
    task = await _queue_search(
        sess,
        query,
        topic=topic,
        actor=actor,
        reason="from the map: suggested a new search",
        now=now,
    )
    return SteerResult(
        "suggest",
        None,
        topic,
        None,
        None,
        [task.task_id],
        None,
        f"“{query}” queued as a search{f' for “{topic}”' if topic else ''}.",
        UNDO,
    )


async def area_steering(sess: AsyncSession, area_id: int) -> AreaSteeringRead:
    """What more, less or watch would act on, for the menu to say before acting."""
    build = await latest_build(sess)
    area, _ = await _area_in(sess, build, area_id)
    topics = await area_topics(sess, area)
    topic = dominant(topics, await configured_topics(sess))
    return AreaSteeringRead(
        area_id=area_id,
        topic=topic,
        dominant_share=DOMINANT_SHARE,
        topics=[AreaTopicShare(topic=t, passages=n) for t, n in topics],
        more_factor=MORE_FACTOR,
        less_factor=LESS_FACTOR,
        boost_days=BOOST_DAYS,
        search=area_search(area, topic),
        noise_sources=len(await noise_candidates(sess, area)),
    )


# ---------------------------------------------------------------------------
# "This is noise" (`P6-42`)
# ---------------------------------------------------------------------------

#: The share of a source's live passages that must lie in the field before
#: marking the field marks the source: a page with a paragraph in a noisy field
#: and the rest elsewhere is not that field's noise.
NOISE_SHARE = 0.5

#: Seed sources a person chose; never marked from the map.
PERSON_CHOSEN = ("user",)


async def noise_candidates(sess: AsyncSession, area) -> list[int]:
    """Sources this area would mark: read, about none of the topics, mostly here.

    Only sources the labeller read whole and found about no topic (``topic_labels = {}``
    and no sample label); a labelled or person-seeded source stays.
    """
    from .areaview import _leaves_under

    leaves = _leaves_under(area)
    here = (
        select(Chunk.source_id, func.count().label("n"))
        .join(AreaMember, AreaMember.chunk_id == Chunk.chunk_id)
        .where(
            AreaMember.build_id == area.build_id,
            AreaMember.area_id.in_(leaves),
            Chunk.superseded_at.is_(None),
        )
        .group_by(Chunk.source_id)
        .subquery()
    )
    total = (
        select(Chunk.source_id, func.count().label("n"))
        .where(Chunk.superseded_at.is_(None), Chunk.source_id.in_(select(here.c.source_id)))
        .group_by(Chunk.source_id)
        .subquery()
    )
    chosen = select(QueueTask.url_or_query).where(QueueTask.seed_source.in_(PERSON_CHOSEN))
    rows = await sess.scalars(
        select(Source.source_id)
        .join(here, here.c.source_id == Source.source_id)
        .join(total, total.c.source_id == Source.source_id)
        .where(
            Source.topic_labels == [],
            Source.topic_sample_best.is_(None),
            Source.retention_tier != "junk",
            here.c.n >= total.c.n * NOISE_SHARE,
            Source.url.not_in(chosen),
        )
        .order_by(Source.source_id)
    )
    return list(rows)


def noise_mark(area_id: int, now: dt.datetime) -> str:
    """The key one marking is undone by: which area, and when."""
    return f"{now:%Y%m%dT%H%M%S}-a{area_id}"


async def _mark_noise(sess, area, name, *, actor, reason, now) -> SteerResult:
    ids = await noise_candidates(sess, area)
    if not ids:
        raise Refused(
            f"Nothing in “{name}” is marked: no source here was read whole and found to be "
            "about none of the topics. Its sources carry a topic, or have not been read yet."
        )
    mark = noise_mark(area.area_id, now)
    rows = await sess.execute(
        select(Source.source_id, Source.retention_tier, Source.extra).where(
            Source.source_id.in_(ids)
        )
    )
    for source_id, tier, extra in rows:
        # The tier it had, kept on the row, so undoing restores it exactly.
        note = {**(extra or {}), "noise_mark": {"mark": mark, "was": tier}}
        await sess.execute(
            update(Source)
            .where(Source.source_id == source_id)
            .values(retention_tier="junk", extra=note)
        )
    await steering.record(
        sess,
        actor=actor,
        topic=None,
        field="noise",
        old=None,
        new=f"{mark}: {len(ids)} sources",
        reason=reason,
        now=now,
    )
    return SteerResult(
        "noise",
        area.area_id,
        None,
        None,
        None,
        [],
        None,
        f"{len(ids)} sources in “{name}” that are about none of the topics were marked junk: "
        "search, the map and synthesis leave them out, and nothing was deleted.",
        f"Undo restores every one: mark {mark}.",
        noise_mark=mark,
    )


async def restore_noise(sess: AsyncSession, mark: str, *, actor: str, now: dt.datetime) -> int:
    """Put back every source one marking moved, at the tier it had. Returns how many."""
    rows = await sess.execute(
        select(Source.source_id, Source.extra).where(
            Source.extra["noise_mark"]["mark"].astext == mark
        )
    )
    restored = 0
    for source_id, extra in rows:
        was = (extra or {}).get("noise_mark", {}).get("was") or "primary"
        kept = {k: v for k, v in (extra or {}).items() if k != "noise_mark"}
        await sess.execute(
            update(Source)
            .where(Source.source_id == source_id, Source.retention_tier == "junk")
            .values(retention_tier=was, extra=kept or None)
        )
        restored += 1
    if restored:
        await steering.record(
            sess,
            actor=actor,
            topic=None,
            field="noise",
            old=mark,
            new=f"restored {restored}",
            reason=f"undo of map noise mark {mark}",
            now=now,
        )
    return restored
