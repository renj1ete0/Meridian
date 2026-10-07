"""Deciding whether two mentions are the same thing (task `P4-02`, §5.5).

Four steps, each its own function: normalise, block, score, decide. Never across node
types. The reversible merge (`P4-03`) lives below, separate from the scoring. See
docs/features/knowledge-graph.md#resolution and #merges-and-reversal.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import difflib
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import (
    AttributeDefinition,
    AttributeValue,
    Edge,
    Entity,
    GazetteerTerm,
    MergeLog,
    Observation,
)

log = get_logger(__name__)

#: Above this, merge without asking. Set where a false merge is very unlikely
#: rather than where merges become common: §16 is explicit that conflation is
#: invisible once done, so the auto band has to be the boring one.
AUTO_MERGE = 0.90

#: Below this, they are different entities and nothing is queued. A low
#: threshold here would fill the adjudication queue with obvious non-matches
#: and train whoever reads it to approve without looking.
SEPARATE_BELOW = 0.55

#: How the three signals are weighted. Context is the heaviest (§5.5): two things
#: sharing a name sit in different neighbourhoods.
WEIGHTS = {"string": 0.3, "embedding": 0.3, "context": 0.4}

#: Candidates `block` will consider. Not a limit on correctness — a blocking
#: stage exists to keep scoring off the whole table, and an entity with more
#: than this many plausible near-names is a signal in itself.
MAX_CANDIDATES = 25

__all__ = [
    "AUTO_MERGE",
    "MAX_CANDIDATES",
    "SEPARATE_BELOW",
    "WEIGHTS",
    "Candidate",
    "Verdict",
    "block",
    "decide",
    "expansions_from_gazetteer",
    "MergeError",
    "RepeatedEdges",
    "fold_repeated_edges",
    "merge",
    "reverse",
    "revive",
    "normalise",
    "score",
    "snapshot",
]

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")

#: Words that carry no identity ("the", "Ltd"). Kept short: a long list starts removing
#: words that do distinguish ("National" does).
_NOISE = frozenset({"the", "a", "an", "of", "and", "ltd", "limited", "inc", "plc", "pte"})


def normalise(name: str, *, expansions: dict[str, str] | None = None) -> str:
    """Step 1. Lowercase, strip punctuation, expand known abbreviations.

    Unicode goes to NFKD without combining marks, so accented and plain spellings
    match. ``expansions`` comes from the gazetteer (§5.6) and applies per token.
    """
    folded = unicodedata.normalize("NFKD", name.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _SPACE.sub(" ", _PUNCT.sub(" ", folded)).strip()

    expansions = expansions or {}
    tokens = [expansions.get(token, token) for token in folded.split()]
    # Re-split: an expansion is usually several words.
    tokens = " ".join(tokens).split()
    kept = [token for token in tokens if token not in _NOISE]

    # If a name is *entirely* noise, keep it rather than returning nothing —
    # an empty normalisation matches every other empty one, which is the worst
    # possible outcome for a resolver.
    return " ".join(kept or tokens)


async def expansions_from_gazetteer(sess: AsyncSession) -> dict[str, str]:
    """Abbreviation → expansion, from the approved gazetteer terms (§5.6).

    Only approved rows. A proposed term is a suggestion nobody has checked, and
    letting it rewrite names during resolution would let an unreviewed
    suggestion silently merge two entities.
    """
    rows = (
        (await sess.execute(select(GazetteerTerm).where(GazetteerTerm.approved.is_(True))))
        .scalars()
        .all()
    )

    table: dict[str, str] = {}
    for row in rows:
        canonical = (row.canonical or "").strip().casefold()
        if not canonical:
            continue
        for alias in row.aliases or ():
            key = (alias or "").strip().casefold()
            # Only single-token aliases: this expands token by token, and a
            # multi-word alias would never match a single token anyway.
            if key and key != canonical and " " not in key:
                table[key] = canonical
    return table


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One entity that might be the same thing, and why it was considered."""

    entity: Entity
    #: Which blocking rule surfaced it, for a log line that explains a merge.
    via: str


@dataclasses.dataclass(frozen=True)
class Verdict:
    """A score and what to do about it."""

    entity_id: int
    score: float
    band: str  # merge | adjudicate | separate
    #: The three signals, kept separately. A single number cannot be argued
    #: with; "string 0.9, context 0.1" is a merge somebody should look at.
    signals: dict[str, float]

    @property
    def merges(self) -> bool:
        return self.band == "merge"


def _tokens(normalised: str) -> frozenset[str]:
    return frozenset(normalised.split())


def string_similarity(left: str, right: str) -> float:
    """Token-set overlap, with a character-level tiebreak.

    `difflib` rather than a new dependency; see docs/features/knowledge-graph.md#resolution.
    """
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0

    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    # Weighted toward tokens, but a pure-token score of 0 on "walkability" vs
    # "walkable" would throw away a real signal.
    return 0.75 * jaccard + 0.25 * ratio


def embedding_similarity(
    left: Sequence[float] | None, right: Sequence[float] | None
) -> float | None:
    """Cosine similarity, or None when either side has no embedding.

    None rather than 0.0, and the difference matters: an un-embedded entity is
    unknown on this axis, and scoring it as maximally dissimilar would make
    every entity written before the backfill unmergeable.
    """
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    # Vectors are stored normalised (`embeddings.py`), so the norms are 1 and
    # the dot product is the cosine. Clamped because floating point puts it
    # fractionally outside [-1, 1] often enough to matter.
    return max(0.0, min(1.0, dot))


def context_overlap(left: Sequence[int] | None, right: Sequence[int] | None) -> float | None:
    """How much two entities' evidence overlaps, or None when either has none.

    Jaccard over the chunks each entity was drawn from, so a much-cited entity does not
    overlap with everything.
    """
    if not left or not right:
        return None
    a, b = set(left), set(right)
    return len(a & b) / len(a | b)


def score(left: Entity, right: Entity, *, expansions: dict[str, str] | None = None) -> Verdict:
    """Step 3. Combine the three signals into one number and a band.

    Absent signals are dropped and the weights renormalised, never counted as zero.
    """
    exp = expansions or {}
    signals: dict[str, float] = {
        "string": string_similarity(
            normalise(left.canonical_name, expansions=exp),
            normalise(right.canonical_name, expansions=exp),
        )
    }

    embedded = embedding_similarity(left.embedding, right.embedding)
    if embedded is not None:
        signals["embedding"] = embedded

    shared = context_overlap(left.supporting_chunk_ids, right.supporting_chunk_ids)
    if shared is not None:
        signals["context"] = shared

    weight = sum(WEIGHTS[name] for name in signals)
    combined = sum(signals[name] * WEIGHTS[name] for name in signals) / weight

    return Verdict(
        entity_id=right.entity_id,
        score=combined,
        band=decide(combined),
        signals=signals,
    )


def decide(combined: float) -> str:
    """Step 4. The three bands.

    High merges, low separates, the middle band (kept narrow) is for adjudication (§5.5).
    """
    if combined >= AUTO_MERGE:
        return "merge"
    if combined < SEPARATE_BELOW:
        return "separate"
    return "adjudicate"


async def block(
    sess: AsyncSession,
    name: str,
    node_type: str,
    *,
    embedding: Sequence[float] | None = None,
    expansions: dict[str, str] | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[Candidate]:
    """Step 2. Everything worth scoring against, and nothing else.

    Same node type always. Name and alias overlap, unioned with embedding kNN; entities
    that already redirect are excluded. See docs/features/knowledge-graph.md#resolution.
    """
    # The name as written *and* as expanded (`B-35`): expanded alone, an acronym never
    # found the node literally named by it.
    tokens = sorted(
        {
            token
            for variant in (normalise(name), normalise(name, expansions=expansions))
            for token in variant.split()
            if len(token) > 2
        }
    )

    base = select(Entity).where(
        Entity.node_type == node_type,
        Entity.redirects_to.is_(None),
    )

    found: dict[int, str] = {}
    rows: list[Entity] = []

    # Any shared token is enough to be considered; the exact name always is, since a
    # two-letter name has no token long enough to search by.
    like = [Entity.canonical_name.ilike(f"%{token}%") for token in tokens]
    exact = func.lower(Entity.canonical_name) == name.strip().lower()
    if name.strip():
        # Exact matches first, so common-word hits cannot push them past the limit; then
        # by id, so ties break the same way on every read (`B-37`).
        by_name = (
            (
                await sess.execute(
                    base.where(or_(exact, *like))
                    .order_by(exact.desc(), Entity.entity_id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in by_name:
            found[row.entity_id] = "name"
            rows.append(row)

    if embedding is not None:
        nearest = (
            (
                await sess.execute(
                    base.where(Entity.embedding.is_not(None))
                    .order_by(Entity.embedding.cosine_distance(list(embedding)))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in nearest:
            if row.entity_id not in found:
                found[row.entity_id] = "embedding"
                rows.append(row)

    log.info(
        "resolution candidates",
        # `entity_name`, not `name`: an `extra` key shadowing a `LogRecord` attribute
        # raises. See docs/features/knowledge-graph.md#logging-extra-keys.
        extra={"entity_name": name, "node_type": node_type, "candidates": len(rows)},
    )
    return [Candidate(entity=row, via=found[row.entity_id]) for row in rows[:limit]]


# ---------------------------------------------------------------------------
# Acting on the decision, reversibly (task `P4-03`, §5.5)
# ---------------------------------------------------------------------------
# See docs/features/knowledge-graph.md#merges-and-reversal.


class MergeError(RuntimeError):
    """A merge that must not happen, or a reversal that cannot."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


#: The tables a merge re-points, and the columns in each that name an entity.
#: One place, so `merge` and `reverse` cannot disagree about what moved.
_REFERENCES: dict[str, tuple[type, tuple[str, ...]]] = {
    "edges": (Edge, ("from_node", "to_node")),
    "attribute_values": (AttributeValue, ("entity_id",)),
    "observations": (Observation, ("subject_entity_id", "geography_entity_id")),
}

#: What a fold may change on the surviving edge. The citation-like lists
#: accumulate; the judgement moves only to a strictly higher tier, which is
#: `add_edge`'s rule for the same claim arriving twice (§11.12).
_EDGE_JUDGEMENT = (
    "confidence",
    "stance",
    "certainty",
    "produced_by",
    "model",
    "quality_tier",
    "produced_at",
)
_EDGE_FOLD_FIELDS = (
    "supporting_chunk_ids",
    "topic_labels",
    "contested_with",
    "valid_from",
    "valid_to",
    "similarity_dimension",
    "disanalogy",
    "created_by",
    *_EDGE_JUDGEMENT,
)

#: The same for an attribute value. The value itself travels with the
#: judgement: which of two readings of one attribute an entity carries is the
#: judgement.
_VALUE_JUDGEMENT = (
    "value",
    "value_numeric",
    "value_json",
    "confidence",
    "produced_by",
    "model",
    "quality_tier",
    "produced_at",
    "tagged_at",
)
_VALUE_FOLD_FIELDS = ("supporting_chunk_ids", *_VALUE_JUDGEMENT)


async def merge(
    sess: AsyncSession,
    source_id: int,
    target_id: int,
    *,
    decided_by: str,
    verdict: Verdict | None = None,
) -> MergeLog:
    """Absorb ``source_id`` into ``target_id``, reversibly.

    The source is kept as a redirect, never deleted. Refuses across node types, into
    itself, and from or into an entity that already redirects. The moved ids are
    recorded for `reverse`, and a claim or attribute the target already holds is
    folded (`B-41`). See docs/features/knowledge-graph.md#merges-and-reversal.
    """
    source = await sess.get(Entity, source_id, with_for_update=True)
    target = await sess.get(Entity, target_id, with_for_update=True)
    if source is None or target is None:
        raise MergeError("missing", f"No entity {source_id if source is None else target_id}.")
    if source_id == target_id:
        raise MergeError("self", "An entity cannot be merged into itself.")
    if source.node_type != target.node_type:
        raise MergeError(
            "node_type",
            f"{source.node_type} and {target.node_type} are different kinds of thing "
            f"(§5.5: never merge across node types).",
        )
    if source.redirects_to is not None:
        raise MergeError("chain", f"Entity {source_id} already redirects; merge the target.")
    if target.redirects_to is not None:
        raise MergeError("chain", f"Entity {target_id} is itself a redirect; it is not a home.")

    # Before the move, not after: the table's one-value-per-attribute
    # constraint would refuse the move itself.
    combined = await _fold_attribute_values(sess, source_id, target_id, folded_by=decided_by)

    moved: dict[str, list[int]] = {}
    moved_columns: dict[str, dict[str, list[str]]] = {}
    for table, (model, columns) in _REFERENCES.items():
        moved[table], moved_columns[table] = await _reassign(
            sess, model, columns, source_id, target_id
        )

    combined += await _fold_edges(sess, moved["edges"], folded_by=decided_by)

    target_fields = ("aliases", "merged_from", "supporting_chunk_ids")
    target_before = snapshot(target, target_fields)
    # The aliases come too. A merge that dropped them would lose the very
    # spellings that caused the merge, so the next mention fragments again.
    target.aliases = sorted(
        {*(target.aliases or ()), *(source.aliases or ()), source.canonical_name}
    )
    target.merged_from = sorted(
        {*(target.merged_from or ()), source_id, *(source.merged_from or ())}
    )
    target.supporting_chunk_ids = sorted(
        {*(target.supporting_chunk_ids or ()), *(source.supporting_chunk_ids or ())}
    )
    source.redirects_to = target_id

    entry = MergeLog(
        source_entity_id=source_id,
        target_entity_id=target_id,
        score=verdict.score if verdict else None,
        signals=dict(verdict.signals) if verdict else None,
        decided_by=decided_by,
        moved_edge_ids=moved["edges"],
        moved_attribute_value_ids=moved["attribute_values"],
        moved_observation_ids=moved["observations"],
        moved_columns=moved_columns,
        combined=combined or None,
        target_fields={"before": target_before, "after": snapshot(target, target_fields)},
    )
    sess.add(entry)
    await sess.flush()

    log.info(
        "entities merged",
        extra={
            "source_entity_id": source_id,
            "target_entity_id": target_id,
            "decided_by": decided_by,
            "edges": len(moved["edges"]),
            "folded": len(combined),
        },
    )
    return entry


async def reverse(sess: AsyncSession, merge_id: int, *, reversed_by: str) -> MergeLog:
    """Undo one merge, exactly.

    Moves back the rows *this* merge moved and splits its folds, newest first, keeping
    what the survivor gained since. The log row is stamped, not deleted.
    """
    entry = await sess.get(MergeLog, merge_id, with_for_update=True)
    if entry is None:
        raise MergeError("missing", f"No merge {merge_id}.")
    if entry.reversed_at is not None:
        raise MergeError("already", f"Merge {merge_id} was already reversed.")

    source = await sess.get(Entity, entry.source_entity_id, with_for_update=True)
    target = await sess.get(Entity, entry.target_entity_id, with_for_update=True)
    if source is None or target is None:
        raise MergeError("missing", "One side of this merge no longer exists.")

    records = list(entry.combined or ())
    # Every survivor is checked before anything is touched, so a reversal that
    # cannot finish leaves nothing half-split.
    for record in records:
        model, _ = _REFERENCES[record["table"]]
        if await sess.get(model, record["survivor_id"]) is None:
            raise MergeError(
                "order",
                f"{record['table']} row {record['survivor_id']} that merge {merge_id} folded "
                "into is gone; reverse the later merge that took it first.",
            )

    for record in reversed(records):
        await _unfold(sess, record)

    moved_ids = {
        "edges": entry.moved_edge_ids,
        "attribute_values": entry.moved_attribute_value_ids,
        "observations": entry.moved_observation_ids,
    }
    for table, (model, columns) in _REFERENCES.items():
        per_row = None if entry.moved_columns is None else entry.moved_columns.get(table, {})
        await _restore(sess, model, columns, moved_ids[table], entry, per_row)

    source.redirects_to = None
    if entry.target_fields:
        _restore_fields(target, entry.target_fields["before"], entry.target_fields["after"])
    else:
        # Logged before `B-41`: only the provenance was known to be this merge's.
        target.merged_from = [i for i in (target.merged_from or ()) if i != entry.source_entity_id]
    entry.reversed_at = dt.datetime.now(dt.UTC)
    entry.reversed_by = reversed_by
    await sess.flush()

    log.info(
        "merge reversed",
        extra={"merge_id": merge_id, "reversed_by": reversed_by, "unfolded": len(records)},
    )
    return entry


async def _reassign(
    sess: AsyncSession, model, columns, source_id: int, target_id: int
) -> tuple[list[int], dict[str, list[str]]]:
    """Point every ``columns`` reference at the target, and report which rows.

    Reads the ids first and updates by primary key, and reports which columns moved
    per row, so a reversal moves back exactly those.
    """
    pk_column = list(model.__table__.primary_key.columns)[0]
    per_row: dict[str, list[str]] = {}
    for column in columns:
        attribute = getattr(model, column)
        rows = (await sess.execute(select(model).where(attribute == source_id))).scalars().all()
        for row in rows:
            setattr(row, column, target_id)
            per_row.setdefault(str(getattr(row, pk_column.name)), []).append(column)
    await sess.flush()
    return sorted(int(key) for key in per_row), per_row


async def _restore(
    sess: AsyncSession,
    model,
    columns,
    ids,
    entry: MergeLog,
    per_row: dict[str, list[str]] | None = None,
) -> None:
    """Point the recorded rows back at the source.

    ``per_row`` names the columns that moved; without it (before `B-41`) every column
    naming the target is moved back.
    """
    if not ids:
        return
    pk_column = list(model.__table__.primary_key.columns)[0]
    rows = (await sess.execute(select(model).where(pk_column.in_(list(ids))))).scalars().all()
    for row in rows:
        moved = columns if per_row is None else per_row.get(str(getattr(row, pk_column.name)), ())
        for column in moved:
            if getattr(row, column) == entry.target_entity_id:
                setattr(row, column, entry.source_entity_id)
    await sess.flush()


# ---------------------------------------------------------------------------
# Folding a repeated claim into the one already held (task `B-41`)
# ---------------------------------------------------------------------------


def _stronger(incoming: int | None, existing: int | None) -> bool:
    """`add_edge`'s rule: only a strictly higher tier replaces a judgement.

    An equal tier disagreeing with itself is a contradiction to record, not a
    value to overwrite; and a lower tier never replaces a higher one (§11.12).
    """
    return incoming is not None and (existing or 0) < incoming


def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def snapshot(row, fields: Sequence[str] | None = None) -> dict[str, Any]:
    """A row as JSON: every column, unless ``fields`` narrows it.

    Columns are read from the table, so one added later is kept without a change here.
    """
    names = fields or [column.key for column in row.__table__.columns]
    return {name: _jsonable(getattr(row, name)) for name in names}


def revive(model, snap: dict[str, Any]) -> dict[str, Any]:
    """The inverse of `snapshot`: column values ready for the model."""
    out: dict[str, Any] = {}
    for name, value in snap.items():
        column = model.__table__.columns[name]
        try:
            kind = column.type.python_type
        except NotImplementedError:  # pragma: no cover - types without one
            kind = None
        if value is not None and kind is dt.datetime:
            value = dt.datetime.fromisoformat(value)
        elif value is not None and kind is dt.date:
            value = dt.date.fromisoformat(value)
        out[name] = value
    return out


def _is_list(model, name: str) -> bool:
    return isinstance(model.__table__.columns[name].type, ARRAY)


def _restore_fields(row, before: dict[str, Any], after: dict[str, Any]) -> None:
    """Take back what a fold gave ``row``, and nothing it gained since.

    Lists lose exactly the items the fold added and regain the ones it removed; a
    scalar goes back only if it still holds what the fold set.
    """
    model = type(row)
    for name, was in before.items():
        now = after.get(name)
        current = _jsonable(getattr(row, name))
        if _is_list(model, name):
            added = set(now or ()) - set(was or ())
            removed = set(was or ()) - set(now or ())
            kept = (set(current or ()) - added) | removed
            setattr(row, name, sorted(kept) if kept or was is not None else None)
        elif current == now:
            setattr(row, name, revive(model, {name: was})[name])


async def _fold_edge(
    sess: AsyncSession, survivor: Edge, folded: Edge, *, folded_by: str
) -> dict[str, Any]:
    """Fold ``folded`` into ``survivor``: one claim, every citation.

    Citations, topic labels and contradictions unite; the judgement moves only to a
    strictly higher tier; empty paired fields fill in pairs. The folded row leaves `edges`
    and is returned whole for `merge_log.combined`.
    """
    absorbed = snapshot(folded)
    before = snapshot(survivor, _EDGE_FOLD_FIELDS)

    survivor.supporting_chunk_ids = sorted(
        {*(survivor.supporting_chunk_ids or ()), *(folded.supporting_chunk_ids or ())}
    )
    labels = {*(survivor.topic_labels or ()), *(folded.topic_labels or ())}
    if labels:
        survivor.topic_labels = sorted(labels)
    contested = {*(survivor.contested_with or ()), *(folded.contested_with or ())} - {
        survivor.edge_id,
        folded.edge_id,
    }
    if contested or survivor.contested_with is not None:
        survivor.contested_with = sorted(contested)

    if _stronger(folded.quality_tier, survivor.quality_tier):
        for name in _EDGE_JUDGEMENT:
            setattr(survivor, name, getattr(folded, name))
    for pair in (("valid_from", "valid_to"), ("similarity_dimension", "disanalogy")):
        if all(getattr(survivor, name) is None for name in pair):
            for name in pair:
                setattr(survivor, name, getattr(folded, name))
    if survivor.created_by is None:
        survivor.created_by = folded.created_by

    references = []
    others = (
        (
            await sess.execute(
                select(Edge)
                .where(
                    Edge.contested_with.any(folded.edge_id),
                    Edge.edge_id.not_in([survivor.edge_id, folded.edge_id]),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for other in others:
        was = list(other.contested_with or ())
        other.contested_with = sorted((set(was) - {folded.edge_id}) | {survivor.edge_id})
        references.append(
            {"edge_id": other.edge_id, "before": was, "after": list(other.contested_with)}
        )

    await sess.delete(folded)
    await sess.flush()
    return {
        "table": "edges",
        "survivor_id": survivor.edge_id,
        "absorbed": absorbed,
        "survivor_before": before,
        "survivor_after": snapshot(survivor, _EDGE_FOLD_FIELDS),
        "contested_refs": references,
        "folded_by": folded_by,
        "at": dt.datetime.now(dt.UTC).isoformat(),
    }


async def _fold_edges(
    sess: AsyncSession, moved_ids: Sequence[int], *, folded_by: str
) -> list[dict[str, Any]]:
    """Fold every moved edge that landed on a claim already held.

    The survivor is the edge the target already had (the lowest id), or the lower id of
    two moved edges. Only moved edges are folded, and observations never are.
    """
    if not moved_ids:
        return []
    moved = set(moved_ids)
    rows = (await sess.execute(select(Edge).where(Edge.edge_id.in_(moved)))).scalars().all()
    triples = sorted({(row.from_node, row.relation_type, row.to_node) for row in rows})

    records: list[dict[str, Any]] = []
    for from_node, relation_type, to_node in triples:
        group = (
            (
                await sess.execute(
                    select(Edge)
                    .where(
                        Edge.from_node == from_node,
                        Edge.relation_type == relation_type,
                        Edge.to_node == to_node,
                    )
                    .order_by(Edge.edge_id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(group) < 2:
            continue
        held = [edge for edge in group if edge.edge_id not in moved]
        survivor = held[0] if held else group[0]
        for edge in group:
            if edge is not survivor and edge.edge_id in moved:
                records.append(await _fold_edge(sess, survivor, edge, folded_by=folded_by))
    return records


async def _fold_attribute_values(
    sess: AsyncSession, source_id: int, target_id: int, *, folded_by: str
) -> list[dict[str, Any]]:
    """Fold the source's attribute values into the target's where both have one.

    The target's row survives; citations unite; the value moves only to a strictly
    higher tier. The attribute's usage count drops by one, which `reverse` restores.
    """
    rows = (
        (
            await sess.execute(
                select(AttributeValue)
                .where(AttributeValue.entity_id == source_id)
                .order_by(AttributeValue.value_id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    records: list[dict[str, Any]] = []
    for folded in rows:
        held = await sess.scalar(
            select(AttributeValue)
            .where(
                AttributeValue.entity_id == target_id,
                AttributeValue.attribute_id == folded.attribute_id,
                AttributeValue.schema_version == folded.schema_version,
            )
            .with_for_update()
        )
        if held is None:
            continue
        absorbed = snapshot(folded)
        before = snapshot(held, _VALUE_FOLD_FIELDS)
        held.supporting_chunk_ids = sorted(
            {*(held.supporting_chunk_ids or ()), *(folded.supporting_chunk_ids or ())}
        )
        if _stronger(folded.quality_tier, held.quality_tier):
            for name in _VALUE_JUDGEMENT:
                setattr(held, name, getattr(folded, name))
        definition = await sess.get(AttributeDefinition, folded.attribute_id)
        if definition is not None:
            definition.usage_count = max(0, definition.usage_count - 1)
        await sess.delete(folded)
        await sess.flush()
        records.append(
            {
                "table": "attribute_values",
                "survivor_id": held.value_id,
                "absorbed": absorbed,
                "survivor_before": before,
                "survivor_after": snapshot(held, _VALUE_FOLD_FIELDS),
                "contested_refs": [],
                "folded_by": folded_by,
                "at": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
    return records


async def _unfold(sess: AsyncSession, record: dict[str, Any]) -> None:
    """Split one fold: the survivor as it was, the folded row back whole."""
    model, _ = _REFERENCES[record["table"]]
    survivor = await sess.get(model, record["survivor_id"], with_for_update=True)
    _restore_fields(survivor, record["survivor_before"], record["survivor_after"])

    for reference in record.get("contested_refs") or ():
        other = await sess.get(Edge, reference["edge_id"], with_for_update=True)
        if other is not None:
            _restore_fields(
                other,
                {"contested_with": reference["before"]},
                {"contested_with": reference["after"]},
            )

    sess.add(model(**revive(model, record["absorbed"])))
    if model is AttributeValue:
        definition = await sess.get(AttributeDefinition, record["absorbed"]["attribute_id"])
        if definition is not None:
            definition.usage_count += 1
    await sess.flush()


# ---------------------------------------------------------------------------
# Repairing the folds that merges before `B-41` did not make
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RepeatedEdges:
    """What a repair pass found, and what it did or would do."""

    #: Triples held by more than one edge.
    groups: int = 0
    #: Edges folded (or, reporting, that would be) — one per row removed.
    folded: int = 0
    #: (merge_id, survivor edge id, folded edge ids) for each attributed group.
    attributed: list[tuple[int, int, list[int]]] = dataclasses.field(default_factory=list)
    #: Edge ids of groups no unreversed merge explains. Reported, not touched.
    unattributed: list[list[int]] = dataclasses.field(default_factory=list)


async def fold_repeated_edges(
    sess: AsyncSession, *, apply: bool, folded_by: str = "repair"
) -> RepeatedEdges:
    """Fold the duplicate edges earlier merges left behind (`B-41`).

    Report by default; ``apply`` writes. Each fold is logged on the newest unreversed
    merge that explains it; a group no merge explains is reported and left. Idempotent.
    """
    groups = (
        await sess.execute(
            select(
                Edge.from_node,
                Edge.to_node,
                func.array_agg(aggregate_order_by(Edge.edge_id, Edge.edge_id)),
            )
            .group_by(Edge.from_node, Edge.relation_type, Edge.to_node)
            .having(func.count() > 1)
            .order_by(func.min(Edge.edge_id))
        )
    ).all()
    merges = (
        (
            await sess.execute(
                select(MergeLog)
                .where(MergeLog.reversed_at.is_(None))
                .order_by(MergeLog.merge_id.desc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    report = RepeatedEdges(groups=len(groups))
    for from_node, to_node, ids in groups:
        entry = next(
            (
                m
                for m in merges
                if m.target_entity_id in (from_node, to_node)
                and set(m.moved_edge_ids or ()) & set(ids)
            ),
            None,
        )
        moved = set(entry.moved_edge_ids or ()) if entry is not None else set()
        held = [i for i in ids if i not in moved]
        survivor_id = held[0] if held else ids[0]
        folded_ids = [i for i in ids if i in moved and i != survivor_id]
        if entry is None or not folded_ids:
            report.unattributed.append(list(ids))
            continue
        report.attributed.append((entry.merge_id, survivor_id, folded_ids))
        report.folded += len(folded_ids)
        if not apply:
            continue
        survivor = await sess.get(Edge, survivor_id, with_for_update=True)
        records = []
        for folded_id in folded_ids:
            folded = await sess.get(Edge, folded_id, with_for_update=True)
            records.append(await _fold_edge(sess, survivor, folded, folded_by=folded_by))
        # Reassigned, not appended in place: JSONB is not mutation-tracked.
        entry.combined = [*(entry.combined or ()), *records]
        await sess.flush()

    log.info(
        "repeated edges",
        extra={
            "groups": report.groups,
            "folded": report.folded,
            "unattributed": len(report.unattributed),
            "applied": apply,
        },
    )
    return report
