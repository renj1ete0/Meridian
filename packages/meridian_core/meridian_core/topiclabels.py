"""Topics from what a page says, not from why it was crawled (task P2-21, §12.5).

A source is the normalised mean of its live chunk vectors; a topic is the embedding of
its name, description and approved vocabulary. Both are measured from a fixed reference
point, and a source carries every topic over an absolute floor and within a margin of
its best. NULL is unread, ``{}`` read and about none. Labels record the basis they were
decided under and go stale when it changes. No language model (§2.1). See
docs/features/topics.md#content-not-provenance.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import and_, exists, func, or_, select, type_coerce, update
from sqlalchemy.ext.asyncio import AsyncSession

from .chunks import in_sample
from .logging import get_logger
from .models import Chunk, GazetteerTerm, Source, TopicConfig
from .models.source import EMBEDDING_DIM

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# The numbers, and where they came from
# ---------------------------------------------------------------------------
# Calibrated on a silver set, then re-measured on the live corpus (`B-83`). Re-measure
# before moving any; see docs/features/topics.md#calibration.

#: The absolute floor. A topic below it is not a label however it ranks. Was
#: 0.45 (the silver set's line) until `B-83`.
LABEL_FLOOR = 0.50

#: How far below the best topic another may sit and still be a label.
LABEL_MARGIN = 0.04

#: Below this best-topic score a source is about nothing the corpus covers: the only
#: threshold `--demote-offtopic` reads. Clearly under LABEL_FLOOR, because a demotion
#: hands a source to the retention sweep.
OFFTOPIC_FLOOR = 0.30

#: Below this, a sample's best score holds back the rest of its document (`B-89`;
#: `chunks.SAMPLE_HEAD`). Under the floor because a sample misreads the whole by a few
#: hundredths. Not part of the basis: it orders embedding, never a label.
TRIAGE_FLOOR = LABEL_FLOOR - 0.04

#: A document at least this long needs :data:`LONG_TRIAGE_FLOOR` instead (`B-133`, ADR 0006):
#: long listings beat the ordinary floor on one matching line. Measured on fully embedded
#: sources; see docs/features/topics.md.
LONG_DOCUMENT = 1000
LONG_TRIAGE_FLOOR = LABEL_FLOOR - 0.02

#: Generic, topic-free phrases whose mean embedding is the reference point. Changing
#: the list changes the basis, so every source is re-examined.
REFERENCE_TEXTS: tuple[str, ...] = (
    "a",
    "the",
    "document",
    "page",
    "text",
    "information",
    "privacy policy personal data cookies terms of use",
    "contact us address telephone email opening hours",
    "search results",
    "about us our mission and history",
    "news and announcements",
    "home",
    "annual report",
    "frequently asked questions",
    "login register account",
    "table of contents",
    "copyright all rights reserved",
    "click here to read more",
    "events calendar",
    "careers job vacancies",
)

#: Bumped when the method changes in a way the constants above do not capture.
LABELLER_VERSION = 1

#: Aliases shorter than this are left out of a prototype: mostly acronyms, noise to
#: an embedder. The same floor as the URL matcher's.
MIN_ALIAS_LENGTH = 5

#: Topic statuses that do *not* label. Labels describe content, so pausing a
#: topic's crawl does not make the pages about it stop being about it; archiving
#: is the operator saying the topic is gone.
NOT_LABELLING = frozenset({"archived"})

_SPACE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Prototypes
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Prototype:
    """One topic, and the text that stands for it."""

    topic: str
    text: str


def topic_name(topic: str) -> str:
    """A slug as words: ``some-topic`` → ``some topic``."""
    return _SPACE.sub(" ", topic.replace("-", " ").replace("_", " ")).strip()


def prototype_text(topic: str, description: str | None, phrases: Iterable[str]) -> str:
    """The text a topic is embedded as.

    ``name: description; phrase; phrase``. Deterministic — phrases are de-duplicated
    case-insensitively and sorted — because the text is part of the basis fingerprint, and an order
    that varied between runs would re-label the corpus for nothing.
    """
    seen: dict[str, str] = {}
    for phrase in phrases:
        cleaned = _SPACE.sub(" ", phrase or "").strip()
        key = cleaned.lower().replace("-", " ")
        if cleaned and key not in seen:
            seen[key] = cleaned
    parts = []
    if description and description.strip():
        parts.append(_SPACE.sub(" ", description).strip())
    parts.extend(seen[key] for key in sorted(seen))
    name = topic_name(topic)
    return f"{name}: {'; '.join(parts)}" if parts else name


async def load_prototypes(sess: AsyncSession) -> list[Prototype]:
    """Every labelling topic's prototype, from the database. Sorted by topic.

    Vocabulary is the approved, unrejected gazetteer terms carrying the topic: canonical
    forms always, aliases only for unambiguous terms and when long enough.
    """
    rows = (
        await sess.execute(
            select(TopicConfig.topic, TopicConfig.description)
            .where(TopicConfig.status.not_in(sorted(NOT_LABELLING)))
            .order_by(TopicConfig.topic)
        )
    ).all()
    if not rows:
        return []

    phrases: dict[str, list[str]] = {topic: [] for topic, _ in rows}
    terms = await sess.scalars(
        select(GazetteerTerm).where(
            GazetteerTerm.approved.is_(True), GazetteerTerm.rejected_at.is_(None)
        )
    )
    for term in terms:
        for topic in term.topic_labels or ():
            if topic not in phrases:
                continue
            phrases[topic].append(term.canonical)
            if not term.ambiguous:
                phrases[topic].extend(
                    alias for alias in term.aliases or () if len(alias) >= MIN_ALIAS_LENGTH
                )

    return [
        Prototype(topic, prototype_text(topic, description, phrases[topic]))
        for topic, description in rows
    ]


def basis_fingerprint(prototypes: Sequence[Prototype], model: str | None) -> str:
    """What a label was computed under, as a short stable hash.

    Everything that can change a label is in it — except the source's own
    chunks, which are tracked by timestamp instead.
    """
    payload = {
        "version": LABELLER_VERSION,
        "model": model or "",
        "floor": LABEL_FLOOR,
        "margin": LABEL_MARGIN,
        "reference": list(REFERENCE_TEXTS),
        "topics": [[p.topic, p.text] for p in sorted(prototypes, key=lambda p: p.topic)],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"v{LABELLER_VERSION}:{digest[:16]}"


# ---------------------------------------------------------------------------
# Scoring — pure, so the thresholds can be tested with constructed vectors
# ---------------------------------------------------------------------------


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


@dataclasses.dataclass(frozen=True)
class Basis:
    """Prototype vectors, measured from the reference point, ready to score."""

    topics: tuple[str, ...]
    #: One unit row per topic, already centred on the reference.
    matrix: np.ndarray
    reference: np.ndarray
    fingerprint: str

    @classmethod
    def build(
        cls,
        prototypes: Mapping[str, Sequence[float]],
        reference_vectors: Sequence[Sequence[float]],
        fingerprint: str,
    ) -> Basis:
        """From raw prototype and reference embeddings, as the embedder returned them."""
        reference = (
            np.mean([_unit(np.asarray(v, dtype=np.float64)) for v in reference_vectors], axis=0)
            if reference_vectors
            else np.zeros(EMBEDDING_DIM)
        )
        topics = tuple(sorted(prototypes))
        matrix = (
            np.stack(
                [
                    _unit(_unit(np.asarray(prototypes[t], dtype=np.float64)) - reference)
                    for t in topics
                ]
            )
            if topics
            else np.zeros((0, len(reference)))
        )
        return cls(topics=topics, matrix=matrix, reference=reference, fingerprint=fingerprint)

    def scores(self, source_vector: Sequence[float]) -> dict[str, float]:
        """Each topic's similarity to a source, measured from the reference."""
        centred = _unit(_unit(np.asarray(source_vector, dtype=np.float64)) - self.reference)
        return {
            topic: round(float(score), 4)
            for topic, score in zip(self.topics, self.matrix @ centred, strict=True)
        }


def decide(
    scores: Mapping[str, float], *, floor: float = LABEL_FLOOR, margin: float = LABEL_MARGIN
) -> list[str]:
    """The labels a set of scores earns, best first.

    Every topic at or above ``floor`` and within ``margin`` of the best; ties break by
    name so the order is stable.
    """
    if not scores:
        return []
    best = max(scores.values())
    chosen = [t for t, s in scores.items() if s >= floor and s >= best - margin]
    return sorted(chosen, key=lambda t: (-scores[t], t))


def best_score(scores: Mapping[str, float] | None) -> float | None:
    return max(scores.values()) if scores else None


def is_offtopic(scores: Mapping[str, float] | None, *, floor: float = OFFTOPIC_FLOOR) -> bool:
    """Whether a source is about nothing the corpus covers. Unknown is not off-topic."""
    best = best_score(scores)
    return best is not None and best < floor


# ---------------------------------------------------------------------------
# The queue and the vectors
# ---------------------------------------------------------------------------


def _live(chunk=Chunk):
    return chunk.superseded_at.is_(None)


def awaiting_labels(fingerprint: str):
    """The predicate for "this source needs (re-)labelling".

    Has a live embedded chunk, no chunk of its *sample* still waiting for a vector, and
    is unexamined, examined under another basis, or rewritten since. Or: was labelled
    from its sample (`B-89`) and is now embedded whole.
    """
    embedded = exists().where(
        Chunk.source_id == Source.source_id, _live(), Chunk.embedding.is_not(None)
    )
    pending = exists().where(
        Chunk.source_id == Source.source_id, _live(), Chunk.embedding.is_(None)
    )
    sample_pending = exists().where(
        Chunk.source_id == Source.source_id, _live(), Chunk.embedding.is_(None), in_sample()
    )
    rewritten = exists().where(
        Chunk.source_id == Source.source_id,
        _live(),
        Chunk.created_at > Source.topics_examined_at,
    )
    return and_(
        embedded,
        ~sample_pending,
        or_(
            Source.topics_examined_at.is_(None),
            Source.topic_basis.is_distinct_from(fingerprint),
            rewritten,
            and_(Source.topic_sample_best.is_not(None), ~pending),
        ),
    )


async def still_pending(sess: AsyncSession, source_ids: Sequence[int]) -> set[int]:
    """Which of these sources have live chunks without a vector.

    Labels read from such a source now are read from its sample (`B-89`).
    """
    if not source_ids:
        return set()
    rows = await sess.scalars(
        select(Chunk.source_id)
        .where(Chunk.source_id.in_(list(source_ids)), _live(), Chunk.embedding.is_(None))
        .distinct()
    )
    return set(rows)


def triage_floor(*, long_document: bool) -> float:
    """The sample score a document's rest needs to be embedded early (`B-89`, `B-133`)."""
    return LONG_TRIAGE_FLOOR if long_document else TRIAGE_FLOOR


async def long_sources(sess: AsyncSession, source_ids: Sequence[int]) -> set[int]:
    """Which of these sources have at least :data:`LONG_DOCUMENT` live passages."""
    if not source_ids:
        return set()
    rows = await sess.scalars(
        select(Chunk.source_id)
        .where(
            Chunk.source_id.in_(list(source_ids)),
            _live(),
            Chunk.chunk_index >= LONG_DOCUMENT - 1,
        )
        .distinct()
    )
    return set(rows)


async def sources_awaiting(
    sess: AsyncSession, fingerprint: str, *, limit: int, after: int = 0
) -> list[int]:
    """The next ``limit`` source ids needing labels, past ``after``.

    A cursor, so a report-only pass that writes nothing still moves forward.
    """
    rows = await sess.scalars(
        select(Source.source_id)
        .where(Source.source_id > after, awaiting_labels(fingerprint))
        .order_by(Source.source_id)
        .limit(limit)
    )
    return list(rows)


async def count_awaiting(sess: AsyncSession, fingerprint: str) -> int:
    return int(
        await sess.scalar(
            select(func.count()).select_from(Source).where(awaiting_labels(fingerprint))
        )
        or 0
    )


async def source_vectors(sess: AsyncSession, source_ids: Sequence[int]) -> dict[int, np.ndarray]:
    """Each source's mean live chunk vector.

    Non-duplicate chunks where a source has any; a source made only of duplicates falls
    back to them.
    """
    if not source_ids:
        return {}
    vector = Vector(EMBEDDING_DIM)
    unique = type_coerce(func.avg(Chunk.embedding).filter(Chunk.duplicate_of.is_(None)), vector)
    everything = type_coerce(func.avg(Chunk.embedding), vector)
    rows = await sess.execute(
        select(Chunk.source_id, unique, everything)
        .where(Chunk.source_id.in_(list(source_ids)), _live(), Chunk.embedding.is_not(None))
        .group_by(Chunk.source_id)
    )
    out: dict[int, np.ndarray] = {}
    for source_id, mean_unique, mean_all in rows:
        chosen = mean_unique if mean_unique is not None else mean_all
        if chosen is not None:
            out[source_id] = np.asarray(chosen, dtype=np.float64)
    return out


async def record_labels(
    sess: AsyncSession,
    source_id: int,
    *,
    scores: Mapping[str, float],
    fingerprint: str,
    now: dt.datetime,
    sampled: bool = False,
) -> list[str]:
    """Write one source's labels, scores and basis. Returns the labels written.

    Replaces, never accumulates. ``sampled`` says the scores came from part of the text
    (`B-89`); the best is kept as ``topic_sample_best``, which the whole text clears.
    """
    labels = decide(scores)
    await sess.execute(
        update(Source)
        .where(Source.source_id == source_id)
        .values(
            topic_labels=labels,
            topic_scores=dict(scores),
            topic_basis=fingerprint,
            topics_examined_at=now,
            topic_sample_best=best_score(scores) if sampled else None,
        )
    )
    return labels


# ---------------------------------------------------------------------------
# Off-topic demotion — gated, and never part of a scheduled run
# ---------------------------------------------------------------------------


async def offtopic_candidates(
    sess: AsyncSession, fingerprint: str, *, floor: float = OFFTOPIC_FLOOR
) -> list[tuple[int, float]]:
    """Sources labelled under ``fingerprint`` whose best score is under ``floor``.

    Current basis only; already-junk sources and sources labelled from a sample are
    left out.
    """
    rows = await sess.execute(
        select(Source.source_id, Source.topic_scores).where(
            Source.topic_basis == fingerprint,
            Source.topic_scores.is_not(None),
            Source.topic_sample_best.is_(None),
            Source.retention_tier != "junk",
        )
    )
    out = []
    for source_id, scores in rows:
        if is_offtopic(scores, floor=floor):
            out.append((source_id, float(best_score(scores))))
    return sorted(out)


async def demote(sess: AsyncSession, source_ids: Sequence[int]) -> int:
    """Mark sources ``junk``. Returns how many changed. Deletes nothing.

    The tier is the decision, and the retention sweep (`P1-31`) is the deletion
    — which it still only performs with ``--apply``. Search, the map and
    synthesis already leave junk out.
    """
    if not source_ids:
        return 0
    result = await sess.execute(
        update(Source)
        .where(Source.source_id.in_(list(source_ids)), Source.retention_tier != "junk")
        .values(retention_tier="junk")
    )
    return int(result.rowcount or 0)
