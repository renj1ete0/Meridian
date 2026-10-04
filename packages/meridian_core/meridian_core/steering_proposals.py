"""Steering proposals that apply by default (task P6-38, spec §10, §10.1, §10.2).

The operator's rule, in their words: *by default you propose what to steer, and
if I don't select, it will be steered that way.* §10.2 already says the system
steers itself and nothing waits for approval; this module is how it does so
without surprising anybody. Every change is proposed first, in plain words with
the numbers behind it, waits a window (`steering_proposal_window_hours` in the
global policy row) for an objection, and then applies itself through
:mod:`meridian_core.steering` — so it lands in `steering_log` like any other
change, and is undone the way any other change is.

**The signal is measured, and heuristic.** The worker never calls a model
(§2.1), so a proposal comes from counting: each active topic's share of the
*new on-topic sources* of the last ``LOOKBACK_HOURS``, against the share of the
crawl its weight gives it, and each topic's *yield* — new on-topic sources per
fetch — against the crawl's as a whole. Two findings are acted on:

- **Starved** — the topic produced well under the share its weight promises
  (or it is thin by :mod:`meridian_core.gaps`' measure and behind), *and* more
  crawl would help: either the draw is not reaching it (fewer fetches than its
  share) or the fetches it does get yield. Proposed: a boost,
  ×``BOOST_FACTOR`` for ``BOOST_HOURS``. A boost is the gentlest lever there
  is, because it removes itself (§10). Boosting a topic whose fetches already
  find nothing only spends more crawl finding nothing.
- **Inefficient** — it takes at least its share of the fetches and yields
  under ``LOW_YIELD_RATIO`` of the crawl's average per fetch. Proposed: its
  baseline weight lowered by a small, capped step, never below its floor, and
  never for a thin topic. Lowering a weight frees crawl for the topics that
  turn it into sources; raising one would be a permanent change made on a
  day's evidence, and a boost already does that job temporarily.

  This replaced an "over-served" rule (`B-64`) that cut a topic for producing
  *more* than its share — which on a live crawl meant cutting exactly the
  topics the crawl was doing well on. Producing a lot is the goal; producing
  little per fetch is the waste.

Fetches are attributed to the topic that drew them and sources to the topics
their content carries, so yield is a heuristic across the two: a fetch drawn
for one topic that lands a page about another counts for the other. Over a
day's crawl that is the signal wanted — which topics' crawl turns into
on-topic pages at all.

**Bounds, each of them a failure this is built against:**

- One proposal per topic per pass, one change per proposal; and at most one
  pending per topic and kind, held by a partial unique index.
- Weights stay within floor and ceiling (``steering.set_weight`` refuses
  anything else), and one proposal moves a weight by at most
  ``MAX_WEIGHT_STEP`` and at most ``MAX_RELATIVE_STEP`` of itself.
- Boosts always expire; a topic already boosted is not boosted again.
- Pinned topics are never proposed for (§10.1: autonomous adjustment may not
  touch them), and neither are paused, archived or maintenance ones.
- **A topic anybody changed in the last ``QUIET_HOURS`` is left alone.** The
  operator's manual choice wins, and the evidence for a proposal has to be
  measured *after* the last change — otherwise a weight lowered this morning
  is lowered again this evening on the same day's numbers, and ratchets to
  its floor.
- Too little evidence (``MIN_FETCHES``, ``MIN_NEW_SOURCES``) proposes nothing.

**Silence is consent only while the basis holds.** A pending proposal is
superseded, never applied, when the signal it rested on has gone, when the
topic has been paused, archived or pinned, when somebody else changed it after
it was proposed, or when its weight has moved underneath it. Superseding is
the safe direction: the next pass proposes again if the signal is still there.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from . import gaps, steering, timefmt
from .logging import get_logger
from .models import (
    FetchAttempt,
    FetchPolicy,
    Notification,
    QueueTask,
    Source,
    SteeringLog,
    SteeringProposal,
    TopicConfig,
)
from .policy import GLOBAL_DOMAIN

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# The window, from the global policy row
# ---------------------------------------------------------------------------

#: The key in `fetch_policy['*'].settings`. Stripped by `resolve_policy`.
WINDOW_KEY = "steering_proposal_window_hours"

#: Mirrors `config/fetch_policy.yaml` and the migration that backfills it; a
#: drift test holds the three together.
DEFAULT_WINDOW_HOURS = 12.0

#: A window shorter than an hour gives nobody a chance to object, which is the
#: whole difference between a proposal and a change; one longer than a week
#: applies on evidence a week stale. Outside these the default is used, loudly.
MIN_WINDOW_HOURS = 1.0
MAX_WINDOW_HOURS = 168.0


def parse_window(raw: object) -> float:
    """Hours from the stored value, or the default when it is unusable.

    A read never raises: a malformed setting must not stop the pass that would
    otherwise keep steering. It says so in the log instead.
    """
    if raw is None:
        return DEFAULT_WINDOW_HOURS
    if isinstance(raw, bool):
        hours = math.nan
    else:
        try:
            hours = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            hours = math.nan
    if not (MIN_WINDOW_HOURS <= hours <= MAX_WINDOW_HOURS):
        log.warning(
            "unusable steering proposal window; using the default",
            extra={"value": repr(raw), "default_hours": DEFAULT_WINDOW_HOURS},
        )
        return DEFAULT_WINDOW_HOURS
    return hours


async def window_hours(sess: AsyncSession) -> float:
    row = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == GLOBAL_DOMAIN))
    return parse_window((row.settings or {}).get(WINDOW_KEY) if row else None)


# ---------------------------------------------------------------------------
# The heuristic — pure, so every threshold is testable without a database
# ---------------------------------------------------------------------------

#: How far back the signal is measured. A day, so a proposal is not made on one
#: quiet hour, and so that it matches ``QUIET_HOURS``: the evidence for a
#: proposal is always measured entirely after the last change to the topic.
LOOKBACK_HOURS = 24

#: Below either, the pass proposes nothing. Shares of a handful of fetches are
#: noise, and a crawl that has stopped says nothing about any one topic.
MIN_FETCHES = 100
MIN_NEW_SOURCES = 20

#: Starved: new on-topic sources below this fraction of the draw share.
STARVED_RATIO = 0.5
#: Inefficient: new on-topic sources per fetch below this fraction of the
#: crawl's average (`B-64`). Half, so ordinary variation between topics — some
#: subjects simply publish less — is not read as waste.
LOW_YIELD_RATIO = 0.5

BOOST_FACTOR = 1.5
BOOST_HOURS = 24

#: A weight proposal moves the weight by at most this much, absolute ...
MAX_WEIGHT_STEP = 0.05
#: ... and by at most this fraction of itself, so a small topic is not halved.
MAX_RELATIVE_STEP = 0.25
#: A smaller step than this is not worth a proposal anybody has to read.
MIN_WEIGHT_STEP = 0.01

#: No proposal for a topic with any `steering_log` row this recent.
QUIET_HOURS = 24

#: A pending weight proposal is superseded if the weight moved this far since.
WEIGHT_DRIFT = 0.02

#: `steering_proposals.actor`: the pass that measured the signal.
PROPOSER = "steerproposals"
#: `steering_log.actor` for a change applied because nobody objected.
ACTOR = "proposal"


@dataclasses.dataclass(frozen=True)
class TopicMeasure:
    """What the pass knows about one active topic."""

    topic: str
    weight: float
    floor: float
    ceiling: float
    #: The share of seeds the topic draws now, boost and bounds included.
    share: float
    boosted: bool
    fetches: int
    new_sources: int
    pinned: bool = False
    #: Somebody changed it within ``QUIET_HOURS``.
    quiet: bool = False
    #: Its labelled sources in all, when Gaps calls it thin; else None.
    thin_sources: int | None = None


@dataclasses.dataclass(frozen=True)
class Draft:
    """A proposal before it is written: one change to one topic."""

    topic: str
    kind: str  # boost | weight
    current: float
    proposed: float
    reason: str
    evidence: dict[str, Any]


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def draft_proposals(
    measures: list[TopicMeasure], *, lookback_hours: int = LOOKBACK_HOURS
) -> list[Draft]:
    """At most one proposal per topic, from one pass's measurements.

    ``measures`` is every *active* topic, eligible or not: the totals the
    shares are taken of have to include a pinned topic's fetches, or pinning
    one topic would make every other look starved.
    """
    total_fetches = sum(m.fetches for m in measures)
    total_new = sum(m.new_sources for m in measures)
    if total_fetches < MIN_FETCHES or total_new < MIN_NEW_SOURCES:
        return []
    # One active topic draws everything whatever its weight; nothing to move.
    if len(measures) < 2:
        return []

    mean_yield = total_new / total_fetches

    out: list[Draft] = []
    for m in sorted(measures, key=lambda m: m.topic):
        if m.pinned or m.quiet or m.boosted or m.share <= 0:
            continue
        new_share = _share(m.new_sources, total_new)
        fetch_share = _share(m.fetches, total_fetches)
        # None when the topic drew no fetches: no yield to judge, which is
        # itself the finding a boost answers.
        topic_yield = m.new_sources / m.fetches if m.fetches else None
        yields = topic_yield is None or topic_yield >= LOW_YIELD_RATIO * mean_yield
        evidence: dict[str, Any] = {
            "lookback_hours": lookback_hours,
            "draw_share": round(m.share, 4),
            "weight": round(m.weight, 4),
            "floor": m.floor,
            "ceiling": m.ceiling,
            "new_sources": m.new_sources,
            "new_sources_total": total_new,
            "new_source_share": round(new_share, 4),
            "fetches": m.fetches,
            "fetches_total": total_fetches,
            "fetch_share": round(fetch_share, 4),
            "yield_per_fetch": None if topic_yield is None else round(topic_yield, 4),
            "mean_yield_per_fetch": round(mean_yield, 4),
        }
        if m.thin_sources is not None:
            evidence["labelled_sources"] = m.thin_sources

        starved = new_share < STARVED_RATIO * m.share
        thin_and_behind = m.thin_sources is not None and new_share < m.share
        under_drawn = fetch_share < m.share
        if (starved or thin_and_behind) and (under_drawn or yields):
            reason = (
                f"{m.topic} is weighted for {m.share:.0%} of the crawl and produced "
                f"{new_share:.0%} of new on-topic sources in the last {lookback_hours} hours "
                f"({m.new_sources} of {total_new})."
            )
            if m.thin_sources is not None:
                reason += (
                    f" It has {m.thin_sources} labelled sources in all, fewer than "
                    f"{gaps.THIN_SOURCES}."
                )
            reason += (
                f" A ×{BOOST_FACTOR:g} boost for {BOOST_HOURS} hours draws it more often, "
                "then ends by itself."
            )
            out.append(
                Draft(
                    m.topic,
                    "boost",
                    1.0,
                    BOOST_FACTOR,
                    reason,
                    {**evidence, "boost_hours": BOOST_HOURS},
                )
            )
            continue

        inefficient = (
            topic_yield is not None
            and topic_yield < LOW_YIELD_RATIO * mean_yield
            and fetch_share >= m.share
        )
        # Never a thin topic: a topic short of sources is not one to take crawl
        # from, however poorly its crawl is doing — that wants better seeds.
        if inefficient and m.thin_sources is None:
            step = min(MAX_WEIGHT_STEP, m.weight * MAX_RELATIVE_STEP, m.weight - m.floor)
            if step < MIN_WEIGHT_STEP:
                continue
            proposed = round(m.weight - step, 4)
            if not (m.floor - steering.EPS <= proposed <= m.ceiling + steering.EPS):
                continue
            reason = (
                f"{m.topic} took {fetch_share:.0%} of fetches in the last {lookback_hours} "
                f"hours and produced {m.new_sources} new on-topic sources — "
                f"{topic_yield:.2f} per fetch against {mean_yield:.2f} across the crawl. "
                f"Lowering its weight from {m.weight:.2f} to {proposed:.2f} moves crawl to "
                "topics that turn it into sources."
            )
            out.append(Draft(m.topic, "weight", round(m.weight, 4), proposed, reason, evidence))
    return out


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


async def measure(sess: AsyncSession, *, now: dt.datetime) -> list[TopicMeasure]:
    """Every active topic, with the last ``LOOKBACK_HOURS`` counted.

    Fetches are attributed to the topic of the queue task that asked for
    them — the topic the draw chose. New sources are attributed by their
    content labels, which is what "on-topic" means everywhere else (`P2-21`).
    Duplicates of an earlier source are not new.
    """
    rows = [row for row in await steering.topics(sess) if row.status == steering.DRAWING]
    if not rows:
        return []
    names = [row.topic for row in rows]
    shares = steering.draw_shares(rows, now=now)
    since = now - dt.timedelta(hours=LOOKBACK_HOURS)

    fetches = dict(
        (
            await sess.execute(
                select(QueueTask.topic, func.count(FetchAttempt.attempt_id))
                .join(QueueTask, QueueTask.task_id == FetchAttempt.task_id)
                .where(FetchAttempt.attempted_at >= since, QueueTask.topic.in_(names))
                .group_by(QueueTask.topic)
            )
        ).all()
    )
    label = func.unnest(Source.topic_labels).table_valued("value").render_derived("t")
    new_sources = dict(
        (
            await sess.execute(
                select(label.c.value, func.count())
                .select_from(Source)
                .join(label, true())
                .where(Source.created_at >= since, Source.duplicate_of.is_(None))
                .where(label.c.value.in_(names))
                .group_by(label.c.value)
            )
        ).all()
    )
    quiet = set(
        await sess.scalars(
            select(SteeringLog.topic)
            .where(SteeringLog.changed_at >= now - dt.timedelta(hours=QUIET_HOURS))
            .where(SteeringLog.topic.in_(names))
            .distinct()
        )
    )
    thin = {
        gap.subject: int(gap.evidence.get("sources") or 0)
        for gap in await gaps.topic_coverage(sess, today=now.date())
        if gap.kind == "thin"
    }

    return [
        TopicMeasure(
            topic=row.topic,
            weight=row.weight,
            floor=row.floor,
            ceiling=row.ceiling,
            share=shares.get(row.topic, 0.0),
            boosted=steering.boost_is_active(row, now=now),
            fetches=int(fetches.get(row.topic, 0)),
            new_sources=int(new_sources.get(row.topic, 0)),
            pinned=row.pinned,
            quiet=row.topic in quiet,
            thin_sources=thin.get(row.topic),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Writing proposals down
# ---------------------------------------------------------------------------


async def pending(sess: AsyncSession) -> list[SteeringProposal]:
    return list(
        await sess.scalars(
            select(SteeringProposal)
            .where(SteeringProposal.status == "pending")
            .order_by(SteeringProposal.apply_after, SteeringProposal.proposal_id)
        )
    )


def _same(existing: SteeringProposal, draft: Draft) -> bool:
    """Whether a pending proposal already says what this draft says.

    Kept rather than replaced, because replacing restarts the window: a signal
    that holds all day, re-proposed hourly with a fresh window each time, would
    never apply at all.
    """
    return (
        existing.kind == draft.kind
        and abs(existing.proposed_value - draft.proposed) < MIN_WEIGHT_STEP / 2
        and abs(existing.current_value - draft.current) < WEIGHT_DRIFT
    )


def title_for(proposal: SteeringProposal) -> str:
    """The proposal in a line, for a notification and a log."""
    if proposal.kind == "boost":
        hours = (proposal.evidence or {}).get("boost_hours", BOOST_HOURS)
        return f"Boost {proposal.topic} ×{proposal.proposed_value:g} for {hours} hours"
    return f"{proposal.topic} weight {proposal.current_value:.2f} → {proposal.proposed_value:.2f}"


@dataclasses.dataclass
class PassReport:
    created: list[int] = dataclasses.field(default_factory=list)
    kept: list[int] = dataclasses.field(default_factory=list)
    superseded: list[int] = dataclasses.field(default_factory=list)
    applied: list[int] = dataclasses.field(default_factory=list)
    failed: list[int] = dataclasses.field(default_factory=list)
    window_hours: float = DEFAULT_WINDOW_HOURS


def _supersede(proposal: SteeringProposal, note: str, *, now: dt.datetime) -> None:
    proposal.status = "superseded"
    proposal.decided_at = now
    proposal.decided_by = PROPOSER
    proposal.note = note


async def record_drafts(
    sess: AsyncSession,
    drafts: list[Draft],
    *,
    now: dt.datetime,
    window: float,
    report: PassReport | None = None,
) -> PassReport:
    """Write this pass's drafts; supersede what they replace or no longer confirm.

    A pending proposal this pass did not re-derive is superseded: its signal is
    gone, or the topic stopped being eligible. The next pass proposes again if
    the signal returns, so the cost of superseding is a restarted window and
    the cost of not superseding is a change applied on evidence that no longer
    holds.
    """
    report = report or PassReport(window_hours=window)
    by_key = {(p.topic, p.kind): p for p in await pending(sess)}
    drafted = {(d.topic, d.kind) for d in drafts}

    for key, proposal in by_key.items():
        if key in drafted:
            continue
        problem = await basis_problem(sess, proposal, now=now)
        _supersede(
            proposal,
            problem or f"the signal no longer holds over the last {LOOKBACK_HOURS} hours",
            now=now,
        )
        report.superseded.append(proposal.proposal_id)
    await sess.flush()

    for draft in drafts:
        existing = by_key.get((draft.topic, draft.kind))
        if existing is not None and _same(existing, draft):
            report.kept.append(existing.proposal_id)
            continue
        apply_after = now + dt.timedelta(hours=window)
        row = SteeringProposal(
            created_at=now,
            actor=PROPOSER,
            topic=draft.topic,
            kind=draft.kind,
            current_value=draft.current,
            proposed_value=draft.proposed,
            expires_at=(
                apply_after + dt.timedelta(hours=BOOST_HOURS) if draft.kind == "boost" else None
            ),
            reason=draft.reason,
            evidence=draft.evidence,
            apply_after=apply_after,
            status="pending",
        )
        if existing is not None:
            # Flushed before the insert: the partial unique index allows one
            # pending row per topic and kind, and it is checked per statement.
            _supersede(existing, "replaced by a newer proposal", now=now)
            await sess.flush()
            report.superseded.append(existing.proposal_id)
        sess.add(row)
        await sess.flush()
        if existing is not None:
            existing.note = f"replaced by proposal #{row.proposal_id}"
        report.created.append(row.proposal_id)
        await notify(sess, row)
    await sess.flush()
    return report


async def notify(sess: AsyncSession, proposal: SteeringProposal) -> None:
    """One notification per new proposal, in the Approvals group (§8)."""
    zone = await timefmt.display_zone(sess)
    sess.add(
        Notification(
            notification_type="steering_proposal",
            title=f"Proposed: {title_for(proposal)}",
            body=(
                f"{proposal.reason} Applies by itself at "
                f"{timefmt.format_instant(proposal.apply_after, zone)} unless rejected in Admin."
            ),
            payload={
                "proposal_id": proposal.proposal_id,
                "topic": proposal.topic,
                "kind": proposal.kind,
            },
            surface="admin",
        )
    )
    await sess.flush()


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


async def basis_problem(
    sess: AsyncSession, proposal: SteeringProposal, *, now: dt.datetime
) -> str | None:
    """Why a pending proposal no longer stands, or None if it does."""
    row = await sess.get(TopicConfig, proposal.topic)
    if row is None:
        return f"{proposal.topic} no longer exists"
    if row.status != steering.DRAWING:
        return f"{proposal.topic} is {row.status} now"
    if row.pinned:
        return f"{proposal.topic} was pinned; autonomous changes may not touch it"
    touched = await sess.scalar(
        select(SteeringLog.actor)
        .where(
            SteeringLog.topic == proposal.topic,
            SteeringLog.changed_at > proposal.created_at,
            SteeringLog.actor != ACTOR,
        )
        .order_by(SteeringLog.changed_at.desc())
        .limit(1)
    )
    if touched is not None:
        return f"{proposal.topic} was changed by {touched} after this was proposed"
    if proposal.kind == "boost" and steering.boost_is_active(row, now=now):
        return f"{proposal.topic} is already boosted"
    if proposal.kind == "weight":
        if abs(row.weight - proposal.current_value) > WEIGHT_DRIFT:
            return (
                f"{proposal.topic}'s weight moved from {proposal.current_value:.2f} to "
                f"{row.weight:.2f} after this was proposed"
            )
        if not (row.floor - steering.EPS <= proposal.proposed_value <= row.ceiling + steering.EPS):
            return f"{proposal.proposed_value:.2f} is outside {proposal.topic}'s bounds now"
    return None


async def _apply(
    sess: AsyncSession, proposal: SteeringProposal, *, actor: str, reason: str, now: dt.datetime
) -> None:
    """Make the change through `steering`, which logs it. Raises what it raises."""
    if proposal.kind == "boost":
        hours = int((proposal.evidence or {}).get("boost_hours", BOOST_HOURS))
        expires = now + dt.timedelta(hours=hours)
        await steering.set_boost(
            sess,
            proposal.topic,
            factor=proposal.proposed_value,
            expires_at=expires,
            actor=actor,
            reason=reason,
            now=now,
        )
        proposal.expires_at = expires
    else:
        await steering.set_weight(
            sess, proposal.topic, proposal.proposed_value, actor=actor, reason=reason, now=now
        )
    proposal.status = "applied"
    proposal.applied_at = now


def _window_of(proposal: SteeringProposal) -> str:
    hours = (proposal.apply_after - proposal.created_at).total_seconds() / 3600
    return f"{hours:g}h"


async def apply_due(
    sess: AsyncSession, *, now: dt.datetime, report: PassReport | None = None
) -> PassReport:
    """Apply every pending proposal whose window has passed and whose basis holds.

    Each in a savepoint, so one refused change is recorded as `failed` and the
    rest still apply.
    """
    report = report or PassReport()
    for proposal in await pending(sess):
        if proposal.apply_after > now:
            continue
        problem = await basis_problem(sess, proposal, now=now)
        if problem is not None:
            _supersede(proposal, problem, now=now)
            report.superseded.append(proposal.proposal_id)
            continue
        reason = f"auto-applied after {_window_of(proposal)} with no objection: {proposal.reason}"
        try:
            async with sess.begin_nested():
                await _apply(sess, proposal, actor=ACTOR, reason=reason, now=now)
                proposal.decided_by = ACTOR
                proposal.decided_at = now
        except (ValueError, LookupError, steering.InfeasibleWeights) as exc:
            proposal.status = "failed"
            proposal.decided_by = ACTOR
            proposal.decided_at = now
            proposal.note = str(exc)
            report.failed.append(proposal.proposal_id)
            log.warning(
                "steering proposal refused",
                extra={"proposal_id": proposal.proposal_id, "error": str(exc)},
            )
            continue
        report.applied.append(proposal.proposal_id)
    await sess.flush()
    return report


async def run_pass(sess: AsyncSession, *, now: dt.datetime) -> PassReport:
    """Measure, propose, then apply what is due. Flushes; the caller commits."""
    window = await window_hours(sess)
    report = PassReport(window_hours=window)
    drafts = draft_proposals(await measure(sess, now=now))
    await record_drafts(sess, drafts, now=now, window=window, report=report)
    await apply_due(sess, now=now, report=report)
    return report


# ---------------------------------------------------------------------------
# The operator's decisions
# ---------------------------------------------------------------------------


class NotPending(ValueError):
    """A decision on a proposal that has already been decided."""


async def _pending_one(sess: AsyncSession, proposal_id: int) -> SteeringProposal:
    proposal = await sess.get(SteeringProposal, proposal_id, with_for_update=True)
    if proposal is None:
        raise LookupError(f"no proposal {proposal_id}")
    if proposal.status != "pending":
        raise NotPending(f"proposal {proposal_id} is {proposal.status}, not pending.")
    return proposal


async def accept(
    sess: AsyncSession, proposal_id: int, *, actor: str, now: dt.datetime
) -> SteeringProposal:
    """Apply now.

    The operator's acceptance is the decision, so the only basis checked is the one `steering`
    itself enforces — plus that the topic still draws, because a boost on a paused topic changes
    nothing and says it did. Flushes; the caller commits, and rolls back on a raise.
    """
    proposal = await _pending_one(sess, proposal_id)
    row = await sess.get(TopicConfig, proposal.topic)
    if row is None:
        raise LookupError(f"no topic {proposal.topic!r}")
    if row.status != steering.DRAWING:
        raise ValueError(f"{proposal.topic!r} is {row.status}; accept it once it is active again.")
    await _apply(
        sess,
        proposal,
        actor=actor,
        reason=f"accepted proposal #{proposal_id}: {proposal.reason}",
        now=now,
    )
    proposal.decided_by = actor
    proposal.decided_at = now
    await sess.flush()
    return proposal


#: The `steering_log` field a decision on a proposal is recorded under.
LOG_FIELD = "proposal"


async def reject(
    sess: AsyncSession, proposal_id: int, *, actor: str, note: str | None, now: dt.datetime
) -> SteeringProposal:
    """Never applies.

    Logged, which also keeps the topic quiet for ``QUIET_HOURS`` — a rejected proposal is not
    re-proposed the next hour.
    """
    proposal = await _pending_one(sess, proposal_id)
    cleaned = " ".join((note or "").split()) or None
    proposal.status = "rejected"
    proposal.decided_by = actor
    proposal.decided_at = now
    proposal.note = cleaned
    await steering.record(
        sess,
        actor=actor,
        topic=proposal.topic,
        field=LOG_FIELD,
        old=None,
        new=f"rejected: {title_for(proposal)}",
        reason=f"rejected proposal #{proposal_id}: {cleaned or 'no reason given'}",
        now=now,
    )
    await sess.flush()
    return proposal


async def recent(sess: AsyncSession, *, limit: int = 20) -> list[SteeringProposal]:
    return list(
        await sess.scalars(
            select(SteeringProposal)
            .where(SteeringProposal.status != "pending")
            .order_by(
                func.coalesce(SteeringProposal.decided_at, SteeringProposal.created_at).desc(),
                SteeringProposal.proposal_id.desc(),
            )
            .limit(limit)
        )
    )
