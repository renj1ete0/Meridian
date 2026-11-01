"""The attribute schema's gate and audit (tasks `P7-01`, `P7-02`, spec §7.3).

A proposed attribute becomes active only through a mechanical gate: enough independent sources,
a set of values that actually splits the entities, and room under the cap (or a weaker attribute
to displace). Each active attribute is audited at most once a month, and retired only after
failing consecutive audits. Retiring changes a status and deletes nothing; `restore` undoes it.
Every decision is logged in `attribute_events` with the measurements it was made on. No model
is called here (§2.1). See docs/features/knowledge-graph.md#attribute-schema.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AttributeDefinition, AttributeEvent, AttributeValue, Chunk, Edge, Run, Source
from .validation import (
    ValidationError,
    check_chunks_resolve,
    check_nodes_exist,
    check_provenance,
)
from .writes import WriteResult, tag_candidate

#: §7.3 "≥N independent sources": distinct sources behind a candidate's values, copies folded.
EVIDENCE_SOURCES: Final[int] = 3
#: Fewer tagged entities than this and a split cannot be judged either way.
MIN_ENTITIES: Final[int] = 4
#: Normalised entropy (0–1) below which an attribute does not usefully split its entities.
MIN_DISCRIMINATION: Final[float] = 0.5
#: §7.3 "~12 active attributes", counted over what applies to one topic: the globals plus that
#: topic's own.
CAP: Final[int] = 12
#: Consecutive failed audits before retirement. Globals carry cross-topic reasoning and get the
#: higher bar (§7.1).
RETIRE_AFTER: Final[dict[str, int]] = {"topic_local": 2, "global": 3}
#: An attribute is audited at most this often, however often the pass runs.
AUDIT_GAP: Final[dt.timedelta] = dt.timedelta(days=28)
#: The window "usage frequency" is measured over.
USAGE_WINDOW: Final[dt.timedelta] = dt.timedelta(days=30)
#: A newly activated or restored attribute is measured but not failed for this long: nothing
#: has had a chance to tag it yet.
GRACE: Final[dt.timedelta] = dt.timedelta(days=30)
#: Explanatory power is the strongest of §7.3's four signals.
EXPLANATORY_WEIGHT: Final[float] = 2.0

_NAME = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def normalise_name(raw: str) -> str | None:
    """An attribute name in the schema's spelling (`fleet_size`), or None if it has none."""
    name = re.sub(r"[^a-z0-9]+", "_", raw.strip().casefold()).strip("_")
    return name if _NAME.match(name) else None


def bucket(value: str | None, value_numeric: float | None) -> str | None:
    """The class a value falls in, for counting a split.

    Text is compared case- and space-insensitively; a number by its sign and order of
    magnitude, since every distinct reading of a quantity would otherwise be its own class.
    """
    if value_numeric is not None:
        if value_numeric == 0:
            return "#0"
        sign = "-" if value_numeric < 0 else "+"
        return f"#{sign}{math.floor(math.log10(abs(value_numeric)))}"
    if value is None:
        return None
    text = " ".join(value.casefold().split())
    return text or None


def discrimination(classes: Sequence[str]) -> float | None:
    """How usefully a set of values splits its entities, 0 to 1; None with too few to judge.

    Shannon entropy normalised by its maximum for the classes seen, so one value everywhere
    is 0. Scaled down, to 0, as the classes pass half the entities: values all worded
    differently split nothing either (`B-170`).
    """
    n = len(classes)
    if n < MIN_ENTITIES:
        return None
    counts = Counter(classes)
    k = len(counts)
    if k < 2:
        return 0.0
    entropy = -sum((c / n) * math.log(c / n) for c in counts.values())
    score = entropy / math.log(k)
    if k > n / 2:
        # Falls to 0 as every entity gets its own class.
        score *= 2 * (n - k) / n
    return round(score, 4)


@dataclasses.dataclass(frozen=True)
class Signals:
    """What one attribute is measured on (§7.3)."""

    entities: int
    discrimination: float | None
    sources: int
    used_in_window: int
    coverage: float
    explanatory: int

    def utility(self) -> float:
        """One number to rank attributes by: who is weakest at the cap, who is stronger."""
        return (
            (self.discrimination or 0.0) * math.log1p(self.entities)
            + self.coverage
            + EXPLANATORY_WEIGHT * math.log1p(self.explanatory)
        )

    def as_json(self) -> dict[str, Any]:
        """The signals as logged, utility included."""
        return {**dataclasses.asdict(self), "utility": round(self.utility(), 4)}


def audit_verdict(signals: Signals, *, in_grace: bool, corpus_tagged: bool) -> tuple[bool, str]:
    """Whether an attribute passes one audit, and why (§7.3).

    Explanatory power passes on its own. Without it, an attribute fails for values that do not
    split its entities, or for going unused while others are tagged. `corpus_tagged` is False
    when nothing at all was tagged in the window: an idle synthesis is no evidence of disuse.
    """
    if in_grace:
        return True, "recently activated; measured, not judged"
    if signals.explanatory:
        return True, f"appears in {signals.explanatory} comparison(s)"
    if signals.discrimination is not None and signals.discrimination < MIN_DISCRIMINATION:
        return False, (
            f"discrimination {signals.discrimination:.2f} is below {MIN_DISCRIMINATION:.2f}"
        )
    if signals.used_in_window == 0:
        if corpus_tagged:
            return False, f"no values written in the last {USAGE_WINDOW.days} days"
        return True, f"nothing was tagged in the last {USAGE_WINDOW.days} days; use not judged"
    return True, "used, and splits its entities"


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What happened to one attribute in a pass."""

    name: str
    kind: str
    reason: str


async def measure(sess: AsyncSession, definition: AttributeDefinition, now: dt.datetime) -> Signals:
    """The four signals for one attribute, from its current-schema values."""
    rows = (
        await sess.execute(
            select(
                AttributeValue.value,
                AttributeValue.value_numeric,
                AttributeValue.supporting_chunk_ids,
                AttributeValue.tagged_at,
            ).where(
                AttributeValue.attribute_id == definition.attribute_id,
                AttributeValue.schema_version == definition.schema_version,
            )
        )
    ).all()
    classes = [c for c in (bucket(r.value, r.value_numeric) for r in rows) if c is not None]
    chunk_ids = sorted({chunk_id for r in rows for chunk_id in r.supporting_chunk_ids})
    sources = 0
    if chunk_ids:
        # A copy of a source is not an independent voice (`B-44`).
        sources = int(
            await sess.scalar(
                select(
                    func.count(func.distinct(func.coalesce(Source.duplicate_of, Source.source_id)))
                )
                .select_from(Chunk)
                .join(Source, Source.source_id == Chunk.source_id)
                .where(Chunk.chunk_id.in_(chunk_ids))
            )
            or 0
        )
    since = now - USAGE_WINDOW
    used = sum(1 for r in rows if r.tagged_at is not None and r.tagged_at >= since)
    return Signals(
        entities=len(rows),
        discrimination=discrimination(classes),
        sources=sources,
        used_in_window=used,
        coverage=await _coverage(sess, definition, len(rows)),
        explanatory=await _explanatory(sess, definition.name),
    )


def _peers(definition: AttributeDefinition):
    """The active attributes that apply where this one does."""
    clause = AttributeDefinition.status == "active"
    if definition.scope == "topic_local":
        return clause & (AttributeDefinition.topic == definition.topic)
    return clause


async def _coverage(sess: AsyncSession, definition: AttributeDefinition, tagged: int) -> float:
    """Of the entities carrying any attribute that applies here, the share carrying this one."""
    if not tagged:
        return 0.0
    ids = select(AttributeDefinition.attribute_id).where(
        _peers(definition) | (AttributeDefinition.attribute_id == definition.attribute_id)
    )
    population = await sess.scalar(
        select(func.count(func.distinct(AttributeValue.entity_id))).where(
            AttributeValue.attribute_id.in_(ids)
        )
    )
    return round(tagged / population, 4) if population else 0.0


async def _explanatory(sess: AsyncSession, name: str) -> int:
    """Comparison edges whose dimension or disanalogy names the attribute (§7.2, §7.3)."""
    words = r"\m" + "[ _-]?".join(re.escape(part) for part in name.split("_")) + r"\M"
    return int(
        await sess.scalar(
            select(func.count()).where(
                or_(Edge.similarity_dimension.op("~*")(words), Edge.disanalogy.op("~*")(words))
            )
        )
        or 0
    )


async def _log(
    sess: AsyncSession,
    definition: AttributeDefinition,
    kind: str,
    reason: str,
    *,
    now: dt.datetime,
    signals: Signals | None = None,
    passed: bool | None = None,
    run: Run | None = None,
    unless_repeated: bool = False,
) -> None:
    """Append to the schema log. `unless_repeated` skips an entry the last one already says."""
    if unless_repeated:
        last = await sess.scalar(
            select(AttributeEvent)
            .where(AttributeEvent.attribute_id == definition.attribute_id)
            .order_by(AttributeEvent.event_id.desc())
            .limit(1)
        )
        if last is not None and (last.kind, last.reason) == (kind, reason):
            return
    sess.add(
        AttributeEvent(
            attribute_id=definition.attribute_id,
            kind=kind,
            reason=reason,
            passed=passed,
            signals=signals.as_json() if signals is not None else {},
            run_id=run.run_id if run is not None else None,
            created_at=now,
        )
    )
    await sess.flush()


async def active_count(sess: AsyncSession, definition: AttributeDefinition) -> int:
    """How many active attributes apply wherever this one would, at the fullest topic."""
    rows = (
        await sess.execute(
            select(AttributeDefinition.scope, AttributeDefinition.topic, func.count())
            .where(AttributeDefinition.status == "active")
            .group_by(AttributeDefinition.scope, AttributeDefinition.topic)
        )
    ).all()
    globals_ = sum(n for scope, _, n in rows if scope == "global")
    local = {topic: n for scope, topic, n in rows if scope == "topic_local"}
    if definition.scope == "topic_local":
        return globals_ + local.get(definition.topic, 0)
    return globals_ + max(local.values(), default=0)


async def _set_status(
    sess: AsyncSession,
    definition: AttributeDefinition,
    status: str,
    kind: str,
    reason: str,
    *,
    now: dt.datetime,
    signals: Signals | None = None,
    run: Run | None = None,
) -> Verdict:
    definition.status = status
    if status == "active":
        definition.consecutive_audit_failures = 0
    await _log(sess, definition, kind, reason, now=now, signals=signals, run=run)
    return Verdict(definition.name, kind, reason)


async def judge(
    sess: AsyncSession,
    definition: AttributeDefinition,
    *,
    now: dt.datetime,
    cap: int = CAP,
    run: Run | None = None,
) -> Verdict:
    """Run `P7-01`'s gate over one proposed attribute.

    Refused (still proposed, so evidence can accumulate) without enough independent sources or
    without a useful split. At the cap it displaces the weakest attribute of its own scope only
    when that one has already failed an audit and the candidate scores higher; otherwise it is
    held. Refusals and holds are logged only when their reason changes.
    """
    if definition.status != "proposed":
        raise ValidationError(
            "attribute_proposed", f"{definition.name!r} is {definition.status}, not proposed."
        )
    signals = await measure(sess, definition, now)
    if signals.sources < EVIDENCE_SOURCES:
        reason = f"evidence from {signals.sources} of {EVIDENCE_SOURCES} independent sources"
    elif signals.discrimination is None:
        reason = f"{signals.entities} entities tagged; a split needs {MIN_ENTITIES}"
    elif signals.discrimination < MIN_DISCRIMINATION:
        reason = f"discrimination {signals.discrimination:.2f} is below {MIN_DISCRIMINATION:.2f}"
    else:
        reason = None
    if reason is not None:
        await _log(
            sess,
            definition,
            "refused",
            reason,
            now=now,
            signals=signals,
            run=run,
            unless_repeated=True,
        )
        return Verdict(definition.name, "refused", reason)

    count = await active_count(sess, definition)
    if count < cap:
        return await _set_status(
            sess,
            definition,
            "active",
            "activated",
            "passed the gate with room under the cap",
            now=now,
            signals=signals,
            run=run,
        )

    # One displacement frees one slot; over the cap by more, it would not make room.
    weakest = await _weakest(sess, definition, now) if count == cap else None
    if weakest is not None and signals.utility() > weakest[1].utility():
        displaced, displaced_signals = weakest
        await _set_status(
            sess,
            displaced,
            "retired",
            "retired",
            f"displaced at the cap by {definition.name!r}",
            now=now,
            signals=displaced_signals,
            run=run,
        )
        return await _set_status(
            sess,
            definition,
            "active",
            "activated",
            f"passed the gate; displaced {displaced.name!r} at the cap",
            now=now,
            signals=signals,
            run=run,
        )
    reason = (
        f"{count} active against a cap of {cap}, and no weaker attribute of its scope to displace"
    )
    await _log(
        sess, definition, "held", reason, now=now, signals=signals, run=run, unless_repeated=True
    )
    return Verdict(definition.name, "held", reason)


async def _weakest(
    sess: AsyncSession, candidate: AttributeDefinition, now: dt.datetime
) -> tuple[AttributeDefinition, Signals] | None:
    """The lowest-utility active attribute of the candidate's scope that has failed an audit."""
    stmt = select(AttributeDefinition).where(
        AttributeDefinition.status == "active",
        AttributeDefinition.scope == candidate.scope,
        AttributeDefinition.consecutive_audit_failures >= 1,
    )
    if candidate.scope == "topic_local":
        stmt = stmt.where(AttributeDefinition.topic == candidate.topic)
    found = [(d, await measure(sess, d, now)) for d in await sess.scalars(stmt)]
    return min(found, key=lambda pair: (pair[1].utility(), pair[0].name), default=None)


async def propose_from_tag(
    sess: AsyncSession,
    run: Run | None,
    *,
    attribute: str,
    entity_id: int,
    supporting_chunk_ids: Sequence[int],
    produced_by: str | None,
    model: str | None,
    quality_tier: int | None,
    value: str | None = None,
    value_numeric: float | None = None,
    confidence: float | None = None,
    now: dt.datetime,
) -> tuple[AttributeDefinition, WriteResult]:
    """A tag naming no attribute in the schema, kept as evidence for a proposed one.

    The first such tag creates the definition as `proposed` (global, with no description) and
    logs it; every one adds a candidate value. Refuses a name with no schema spelling, and a
    name that is active or retired.
    """
    name = normalise_name(attribute)
    if name is None:
        raise ValidationError(
            "attribute_name", f"{attribute!r} cannot be spelled as an attribute name."
        )
    # Checked before the definition exists, so a refused tag leaves no empty proposal behind.
    check_provenance(produced_by=produced_by, model=model, quality_tier=quality_tier)
    await check_nodes_exist(sess, [entity_id])
    await check_chunks_resolve(sess, supporting_chunk_ids)
    definition = await sess.scalar(
        select(AttributeDefinition).where(AttributeDefinition.name == name)
    )
    if definition is None:
        definition = AttributeDefinition(name=name, scope="global", status="proposed")
        sess.add(definition)
        await sess.flush()
        await _log(sess, definition, "proposed", "first tagged by a model", now=now, run=run)
    result = await tag_candidate(
        sess,
        run,
        definition,
        entity_id=entity_id,
        supporting_chunk_ids=supporting_chunk_ids,
        produced_by=produced_by,
        model=model,
        quality_tier=quality_tier,
        value=value,
        value_numeric=value_numeric,
        confidence=confidence,
        now=now,
    )
    return definition, result


async def _in_grace(sess: AsyncSession, definition: AttributeDefinition, now: dt.datetime) -> bool:
    since = await sess.scalar(
        select(func.max(AttributeEvent.created_at)).where(
            AttributeEvent.attribute_id == definition.attribute_id,
            AttributeEvent.kind.in_(("activated", "restored")),
        )
    )
    return since is not None and since > now - GRACE


async def audit_one(
    sess: AsyncSession, definition: AttributeDefinition, *, now: dt.datetime, corpus_tagged: bool
) -> Verdict:
    """One audit of one active attribute (`P7-02`): measure, judge, count, retire at the bar."""
    signals = await measure(sess, definition, now)
    passed, reason = audit_verdict(
        signals, in_grace=await _in_grace(sess, definition, now), corpus_tagged=corpus_tagged
    )
    definition.discrimination = signals.discrimination
    definition.last_audited_at = now
    definition.consecutive_audit_failures = (
        0 if passed else definition.consecutive_audit_failures + 1
    )
    await _log(sess, definition, "audited", reason, now=now, signals=signals, passed=passed)
    bar = RETIRE_AFTER[definition.scope]
    if not passed and definition.consecutive_audit_failures >= bar:
        return await _set_status(
            sess,
            definition,
            "retired",
            "retired",
            f"failed {definition.consecutive_audit_failures} consecutive audits: {reason}",
            now=now,
            signals=signals,
        )
    if passed:
        return Verdict(definition.name, "audited", f"passed: {reason}")
    return Verdict(
        definition.name,
        "audited",
        f"failed {definition.consecutive_audit_failures} of {bar}: {reason}",
    )


async def run_pass(sess: AsyncSession, *, now: dt.datetime, cap: int = CAP) -> list[Verdict]:
    """Audit every active attribute that is due, then judge every proposed one.

    Audits first, so a retirement opens the slot a waiting candidate is judged against.
    Does not commit.
    """
    corpus_tagged = bool(
        await sess.scalar(
            select(func.count()).where(AttributeValue.tagged_at >= now - USAGE_WINDOW)
        )
    )
    due = await sess.scalars(
        select(AttributeDefinition)
        .where(
            AttributeDefinition.status == "active",
            or_(
                AttributeDefinition.last_audited_at.is_(None),
                AttributeDefinition.last_audited_at <= now - AUDIT_GAP,
            ),
        )
        .order_by(AttributeDefinition.name)
    )
    verdicts = [await audit_one(sess, d, now=now, corpus_tagged=corpus_tagged) for d in list(due)]
    proposed = await sess.scalars(
        select(AttributeDefinition)
        .where(AttributeDefinition.status == "proposed")
        .order_by(AttributeDefinition.name)
    )
    verdicts += [await judge(sess, d, now=now, cap=cap) for d in list(proposed)]
    return verdicts


async def restore(sess: AsyncSession, name: str, *, now: dt.datetime, cap: int = CAP) -> Verdict:
    """Bring a retired attribute back, with its values; refused when the cap has no room."""
    definition = await _named(sess, name)
    if definition.status != "retired":
        raise ValidationError("attribute_retired", f"{name!r} is {definition.status}, not retired.")
    if await active_count(sess, definition) >= cap:
        raise ValidationError(
            "attribute_cap", f"Restoring {name!r} would exceed the cap of {cap} active attributes."
        )
    return await _set_status(sess, definition, "active", "restored", "restored by hand", now=now)


async def retire(sess: AsyncSession, name: str, *, now: dt.datetime) -> Verdict:
    """Retire an active or proposed attribute by hand. Its values are kept."""
    definition = await _named(sess, name)
    if definition.status == "retired":
        raise ValidationError("attribute_retired", f"{name!r} is already retired.")
    return await _set_status(sess, definition, "retired", "retired", "retired by hand", now=now)


async def _named(sess: AsyncSession, name: str) -> AttributeDefinition:
    definition = await sess.scalar(
        select(AttributeDefinition).where(AttributeDefinition.name == name)
    )
    if definition is None:
        raise ValidationError("attribute_exists", f"No attribute {name!r}.")
    return definition
