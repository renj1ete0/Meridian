"""Attention as a weight vector over topics (task P6-12, spec §10, §10.1).

Normalising clamps and redistributes so floors and ceilings hold; a boost applies at
read time until it expires; paused and archived topics leave the pool with their weight
kept. See docs/features/steering.md#weights.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SteeringLog, TopicConfig

#: Float comparison slack. Weights are REAL in Postgres and arrive having been
#: through a round trip, so an exact `sum == 1.0` is a test that fails for
#: reasons that have nothing to do with steering.
EPS = 1e-9

#: Statuses that draw seeds. §10.2 puts `maintenance` outside the pool — it
#: "stops generating new seeds but keeps processing its queue" — and `archived`
#: and `paused` with it. Only `active` is in.
DRAWING = "active"


class InfeasibleWeights(ValueError):
    """Floors that cannot all be honoured at once.

    Raised by the write paths only; :func:`normalise` relaxes instead, so a read never
    fails.
    """


@dataclasses.dataclass(frozen=True)
class TopicShare:
    """One topic's bounds and its raw weight, as normalisation sees them."""

    topic: str
    weight: float
    floor: float = 0.0
    ceiling: float = 1.0


def effective_weight(row, *, now: dt.datetime) -> float:
    """The weight a draw should use, boost included while it lasts.

    A factor without an expiry is ignored: a boost is both or neither.
    """
    factor = getattr(row, "boost_factor", None)
    expires = getattr(row, "boost_expires_at", None)
    if factor is None or expires is None:
        return row.weight
    if expires <= now:
        # Not cleared, not an error. The row still records what was boosted and
        # until when, which is the audit trail; it simply stops counting.
        return row.weight
    return row.weight * factor


def boost_is_active(row, *, now: dt.datetime) -> bool:
    factor = getattr(row, "boost_factor", None)
    expires = getattr(row, "boost_expires_at", None)
    return factor is not None and expires is not None and expires > now


def normalise(shares: list[TopicShare]) -> dict[str, float]:
    """Weights that sum to 1.0 with every floor and ceiling honoured.

    Clamp-and-redistribute. All-zero weights are spread evenly; an empty set returns
    nothing. Unmeetable bounds are relaxed, never raised: ceilings yield, and floors
    summing past 1.0 scale down proportionally. See docs/features/steering.md#weights.
    """
    if not shares:
        return {}

    floors = sum(share.floor for share in shares)
    ceilings = sum(share.ceiling for share in shares)
    if floors > 1.0 + EPS:
        scale = 1.0 / floors
        shares = [dataclasses.replace(s, floor=s.floor * scale) for s in shares]
    if ceilings < 1.0 - EPS:
        shares = [dataclasses.replace(s, ceiling=1.0) for s in shares]

    by_topic = {share.topic: share for share in shares}
    raw = {share.topic: max(share.weight, 0.0) for share in shares}
    if sum(raw.values()) <= 0:
        raw = dict.fromkeys(raw, 1.0)

    pinned: dict[str, float] = {}
    free = set(raw)

    for _ in range(len(shares) + 1):
        remaining = 1.0 - sum(pinned.values())
        total = sum(raw[topic] for topic in free)
        if total > 0:
            proposed = {topic: raw[topic] / total * remaining for topic in free}
        else:
            proposed = {topic: remaining / len(free) for topic in free}

        violations: dict[str, float] = {}
        for topic, value in proposed.items():
            bounds = by_topic[topic]
            if value < bounds.floor - EPS:
                violations[topic] = bounds.floor
            elif value > bounds.ceiling + EPS:
                violations[topic] = bounds.ceiling

        if not violations:
            return {**pinned, **proposed}

        pinned.update(violations)
        free -= set(violations)
        if not free:
            break

    total = sum(pinned.values())
    if abs(total - 1.0) > 1e-6:  # pragma: no cover - guarded by feasibility above
        raise InfeasibleWeights(
            f"weights settled at {total:.3f} rather than 1.0 with every topic at a bound."
        )
    return pinned


def check_feasible(shares: list[TopicShare]) -> None:
    """Refuse a configuration whose floors cannot all be honoured.

    The write-path counterpart to :func:`normalise`'s relaxation. Only floors
    are checked: ceilings that cannot reach 1.0 are a legitimate state reached
    by pausing topics, not a mistake anybody made.
    """
    floors = sum(share.floor for share in shares)
    if floors > 1.0 + EPS:
        raise InfeasibleWeights(
            f"floors sum to {floors:.3f}; at most 1.0 can be guaranteed. "
            "Lower a floor, or archive a topic."
        )


def draw_shares(rows, *, now: dt.datetime) -> dict[str, float]:
    """What fraction of seeds each topic gets right now.

    The number the interface should show beside the stored weight, because once
    a boost or a floor is involved the two differ — and it is the second one
    that decides what gets crawled.
    """
    active = [row for row in rows if row.status == DRAWING]
    return normalise(
        [
            TopicShare(row.topic, effective_weight(row, now=now), row.floor, row.ceiling)
            for row in active
        ]
    )


def draw_topic(shares: dict[str, float], *, rng: random.Random | None = None) -> str | None:
    """One topic, chosen with probability equal to its share (`B-26`, §10).

    Returns None for an empty pool: claim without a topic filter rather than stop. The
    generator is a parameter so tests can be exact. See
    docs/features/steering.md#drawing-a-topic.
    """
    pool = {topic: share for topic, share in shares.items() if share > 0}
    if not pool:
        return None

    total = sum(pool.values())
    point = (rng or random).random() * total
    cumulative = 0.0
    for topic, share in sorted(pool.items()):
        cumulative += share
        if point < cumulative:
            return topic
    # Only reachable through floating-point drift at the very top of the range.
    return sorted(pool)[-1]


# ---------------------------------------------------------------------------
# Writing: every change to the vector, and why (§10.1)
# ---------------------------------------------------------------------------
# Every function below takes an actor and a reason, neither defaulted, for
# `steering_log`.

#: Fields a caller may set directly. `weight` is not among them: it is a share
#: of a pool, so it is set through :func:`set_weight`, which redistributes.
BOUNDS = ("floor", "ceiling")


async def topics(sess: AsyncSession) -> list[TopicConfig]:
    return list(await sess.scalars(select(TopicConfig).order_by(TopicConfig.topic)))


async def record(
    sess: AsyncSession,
    *,
    actor: str,
    topic: str | None,
    field: str | None,
    old: object,
    new: object,
    reason: str,
    now: dt.datetime,
) -> None:
    """One audit row.

    Values are stringified, because the column is TEXT and the log has to hold a float, a status and
    a boolean without three columns.
    """
    sess.add(
        SteeringLog(
            changed_at=now,
            actor=actor,
            topic=topic,
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            reason=reason,
        )
    )


async def renormalise(
    sess: AsyncSession,
    *,
    actor: str,
    reason: str,
    now: dt.datetime,
    hold: dict[str, float] | None = None,
) -> dict[str, float]:
    """Rewrite the active pool so it sums to 1.0, and log every weight that moved.

    ``hold`` pins a topic at the exact share typed. Every weight that moved is logged,
    not only the one requested.
    """
    rows = await topics(sess)
    active = [row for row in rows if row.status == DRAWING]
    if not active:
        return {}

    held = hold or {}
    shares = [
        TopicShare(
            row.topic,
            effective_weight(row, now=now),
            held.get(row.topic, row.floor),
            held.get(row.topic, row.ceiling),
        )
        for row in active
    ]
    target = normalise(shares)

    for row in active:
        new = target[row.topic]
        if abs(new - row.weight) > 1e-6:
            await record(
                sess,
                actor=actor,
                topic=row.topic,
                field="weight",
                old=round(row.weight, 6),
                new=round(new, 6),
                reason=reason,
                now=now,
            )
            row.weight = new

    await sess.flush()
    return target


async def set_weight(
    sess: AsyncSession, topic: str, value: float, *, actor: str, reason: str, now: dt.datetime
) -> dict[str, float]:
    """Give one topic an exact share and redistribute the rest.

    Refuses a value outside the topic's own bounds rather than clamping it.
    """
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    if row.status != DRAWING:
        raise ValueError(f"{topic!r} is {row.status} and draws no seeds; change its status first.")
    if not (row.floor - EPS <= value <= row.ceiling + EPS):
        raise ValueError(
            f"{value:.3f} is outside {topic!r}'s bounds ({row.floor:.3f}–{row.ceiling:.3f})."
        )
    return await renormalise(sess, actor=actor, reason=reason, now=now, hold={topic: value})


async def set_status(
    sess: AsyncSession, topic: str, status: str, *, actor: str, reason: str, now: dt.datetime
) -> dict[str, float]:
    """Pause, archive, put into maintenance, or reactivate.

    Nothing is deleted (§10.2): the row keeps its weight, so the topic returns
    to roughly the share it left with and un-archiving is a status change rather
    than a rebuild.
    """
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    if row.status == status:
        return await renormalise(sess, actor=actor, reason=reason, now=now)

    await record(
        sess,
        actor=actor,
        topic=topic,
        field="status",
        old=row.status,
        new=status,
        reason=reason,
        now=now,
    )
    row.status = status
    return await renormalise(sess, actor=actor, reason=reason, now=now)


async def set_bounds(
    sess: AsyncSession,
    topic: str,
    *,
    actor: str,
    reason: str,
    now: dt.datetime,
    floor: float | None = None,
    ceiling: float | None = None,
) -> dict[str, float]:
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")

    wanted = {"floor": floor, "ceiling": ceiling}
    lower = row.floor if floor is None else floor
    upper = row.ceiling if ceiling is None else ceiling
    if lower > upper:
        raise ValueError(f"floor {lower:.3f} is above ceiling {upper:.3f}.")

    others = [
        TopicShare(other.topic, other.weight, other.floor, other.ceiling)
        for other in await topics(sess)
        if other.status == DRAWING and other.topic != topic
    ]
    if row.status == DRAWING:
        check_feasible([*others, TopicShare(topic, row.weight, lower, upper)])

    for field, value in wanted.items():
        if value is None or abs(value - getattr(row, field)) <= EPS:
            continue
        await record(
            sess,
            actor=actor,
            topic=topic,
            field=field,
            old=getattr(row, field),
            new=value,
            reason=reason,
            now=now,
        )
        setattr(row, field, value)

    return await renormalise(sess, actor=actor, reason=reason, now=now)


async def set_pinned(
    sess: AsyncSession, topic: str, pinned: bool, *, actor: str, reason: str, now: dt.datetime
) -> None:
    """Pinned topics are the ones autonomous adjustment may not touch (§10.1).

    No renormalisation: pinning changes who may move a weight, not the weight.
    """
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    if row.pinned == pinned:
        return
    await record(
        sess,
        actor=actor,
        topic=topic,
        field="pinned",
        old=row.pinned,
        new=pinned,
        reason=reason,
        now=now,
    )
    row.pinned = pinned
    await sess.flush()


async def set_description(
    sess: AsyncSession,
    topic: str,
    description: str | None,
    *,
    actor: str,
    reason: str,
    now: dt.datetime,
) -> None:
    """Say what a topic is about (task P2-21). Blank clears it.

    No renormalisation; logged anyway, since a description re-labels every source.
    """
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    cleaned = " ".join((description or "").split()) or None
    if row.description == cleaned:
        return
    await record(
        sess,
        actor=actor,
        topic=topic,
        field="description",
        old=row.description,
        new=cleaned,
        reason=reason,
        now=now,
    )
    row.description = cleaned
    await sess.flush()


async def set_boost(
    sess: AsyncSession,
    topic: str,
    *,
    factor: float | None,
    expires_at: dt.datetime | None,
    actor: str,
    reason: str,
    now: dt.datetime,
) -> None:
    """A temporary multiplier that removes itself.

    Factor and expiry both or neither, the expiry in the future. The stored weight is
    untouched; the boost multiplies at draw time.
    """
    row = await sess.get(TopicConfig, topic)
    if row is None:
        raise LookupError(f"no topic {topic!r}")
    if (factor is None) != (expires_at is None):
        raise ValueError("a boost needs both a factor and an expiry, or neither.")
    if factor is not None:
        if factor <= 0:
            raise ValueError("a boost factor must be positive.")
        if expires_at is not None and expires_at <= now:
            raise ValueError("a boost that has already expired changes nothing.")

    for field, value in (("boost_factor", factor), ("boost_expires_at", expires_at)):
        if getattr(row, field) != value:
            await record(
                sess,
                actor=actor,
                topic=topic,
                field=field,
                old=getattr(row, field),
                new=value,
                reason=reason,
                now=now,
            )
            setattr(row, field, value)
    await sess.flush()


async def add_topic(
    sess: AsyncSession,
    topic: str,
    *,
    floor: float,
    ceiling: float,
    actor: str,
    reason: str,
    now: dt.datetime,
    description: str | None = None,
) -> dict[str, float]:
    """Insert a topic and redistribute (§10.2's `add_topic`).

    It starts at weight 0 and gets its floor from the renormalisation.
    """
    if await sess.get(TopicConfig, topic) is not None:
        raise ValueError(f"{topic!r} already exists. Reactivate it rather than adding it again.")
    if floor > ceiling:
        raise ValueError(f"floor {floor:.3f} is above ceiling {ceiling:.3f}.")

    existing = [
        TopicShare(row.topic, row.weight, row.floor, row.ceiling)
        for row in await topics(sess)
        if row.status == DRAWING
    ]
    check_feasible([*existing, TopicShare(topic, 0.0, floor, ceiling)])

    sess.add(
        TopicConfig(
            topic=topic,
            weight=0.0,
            floor=floor,
            ceiling=ceiling,
            status=DRAWING,
            description=" ".join((description or "").split()) or None,
        )
    )
    await sess.flush()
    await record(
        sess,
        actor=actor,
        topic=topic,
        field="status",
        old=None,
        new=DRAWING,
        reason=reason,
        now=now,
    )
    return await renormalise(sess, actor=actor, reason=reason, now=now)
