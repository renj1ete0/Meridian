"""Attention as a weight vector over topics (task P6-12, spec §10, §10.1).

§10 opens with the whole model in one line: "attention is a weight vector over
topics; seeds are drawn proportionally". Everything here serves that sentence,
and the parts worth reading are the three places the obvious implementation is
wrong.

**Normalising is not dividing by the total.** Every active topic has a floor —
§10's "5–10% minimum so nothing fully stalls" — and a ceiling. Proportional
scaling violates both the moment one topic dominates, and a floor that is
silently violated is the guarantee not existing: the topic stalls, which is the
exact failure the floor was written to prevent. So the pool is filled by
clamping and redistributing until nothing is outside its bounds.

**A boost is applied at read time and never cleared.** §10 wants "steer back
later without needing to remember", and the way that promise breaks is a boost
stored into `weight` and a cleanup job that does not run. An expired boost here
is simply not applied; nothing has to notice it expired.

**Archived and paused topics leave the pool entirely.** §10.2: archiving "drops
out of the weight-normalization pool, exactly like maintenance mode but
permanent until reversed". Their stored weight is untouched, so un-archiving is
a status change rather than a rebuild — "nothing is deleted, so returning costs
nothing" applies to whole topics.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

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

    Raised by the write paths only. A configuration where the floors sum past
    1.0 is a mistake somebody is in the middle of making, and the moment to say
    so is while they are making it — refusing `add_topic` with "you cannot
    guarantee seven topics twenty percent each" is useful, and discovering it
    three weeks later from a stalled crawl is not.

    Reads never raise it. :func:`normalise` relaxes instead, because a stored
    configuration that has become infeasible must not take down the screen that
    would let somebody fix it.
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

    **Both** a factor and an expiry, or no boost. §10 makes decay the mechanism
    that removes a boost — "handled by expiry, not by memory" — so a factor
    stored without an expiry is a permanent multiplier wearing a temporary
    one's clothes, and applying it would make the mode's central promise false
    for whoever set it.
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

    Clamp-and-redistribute rather than divide-by-total. Each pass scales the
    still-free topics into whatever the clamped ones left behind; anything that
    lands outside its bounds is pinned there and the pass runs again. It settles
    in at most one pass per topic, because a topic pinned in one pass is never
    freed by a later one.

    Two degenerate inputs are handled rather than left to produce NaN: an empty
    set returns nothing, and a set whose weights are all zero is spread evenly
    before clamping — a fresh topic added at weight 0 alongside others at 0
    should end up sharing, not dividing by zero.

    **Bounds that cannot be met are relaxed here rather than raising**, and the
    two are relaxed differently because they fail differently.

    Ceilings yield whenever they cannot reach 1.0, and that is not a degenerate
    case: pausing every topic but one leaves a single topic with a ceiling of
    0.6, and the seeds still have to come from somewhere. A ceiling is a guard
    against one topic crowding out the others; with no others to crowd out it
    constrains nothing, and enforcing it would mean drawing 60% of a pool and
    leaving the rest undrawn.

    Floors are scaled down proportionally when they sum past 1.0, so every topic
    still keeps a share in the same ratio it was promised. That configuration is
    a mistake, and the write paths refuse to create it — but a read must not
    throw, because the screen that would let somebody fix it is the one reading.
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


# ---------------------------------------------------------------------------
# Writing: every change to the vector, and why (§10.1)
# ---------------------------------------------------------------------------
#
# `steering_log` is not optional. §10.1: "with two writers, the alternative is
# opening the UI in a month and not knowing why a weight is where it is." So
# every function below takes an actor and a reason, and neither has a default —
# a logged change with no reason answers the question no better than no log.

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
    """One audit row. Values are stringified, because the column is TEXT and the
    log has to hold a float, a status and a boolean without three columns."""
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

    ``hold`` pins a topic at an exact share — what "set walkability to 40%"
    means. Without it, setting a weight and then scaling everything including
    that weight gives the person a different number from the one they typed,
    which reads as the control not working.

    Every consequent change is logged, not just the requested one. The question
    §10.1 exists to answer is "why is this topic at 0.18", and the answer is
    usually a change somebody made to a different topic.
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
    Silently clamping a number somebody typed shows them a different one and
    offers no explanation — and the bounds are the thing they would need to
    change.
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

    Both or neither, and an expiry in the future. §10 makes decay the mechanism
    — "steer back later without needing to remember" — so a factor with no
    expiry is a permanent change that will be remembered as temporary, which is
    the one outcome the mode exists to prevent.

    The stored weight is untouched. The boost multiplies at draw time, so when
    it expires the baseline is still there with nothing to restore.
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
) -> dict[str, float]:
    """Insert a topic and redistribute (§10.2's `add_topic`).

    It starts at weight 0 and gets its floor from the renormalisation, which is
    the right amount of attention for something with no seeds behind it yet:
    §10.2 calls adding a topic "a small repeat of cold start", and a topic with
    a large share and no hand-seeded sources spends that share on nothing.
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

    sess.add(TopicConfig(topic=topic, weight=0.0, floor=floor, ceiling=ceiling, status=DRAWING))
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
