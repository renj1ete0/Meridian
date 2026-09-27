"""Topics from what a page says, not from why it was crawled (task P2-21, §12.5).

`P2-14` gave sources a topic by recording the queue topic that caused the fetch,
plus whatever the URL path matched. That is provenance — the reason the crawler
went there — and it is wrong about content in exactly the cases that matter: a
crawl pursuing one topic follows a site's navigation into pages about something
else entirely, and every one of them was stamped with the topic being pursued.
Provenance now lives in ``sources.crawled_for``; ``topic_labels`` is this
module's answer to a different question: *which topics is this text about?*

**The method.** A source is the normalised mean of its live chunk vectors. A
topic is the embedding of a short text built from what the database says about
it — its name, its description, and its approved vocabulary. Both are measured
from a fixed reference point (below), and a source carries every topic whose
similarity clears an absolute floor *and* sits within a margin of its best
topic. Zero, one or several — a page comparing two topics is about both, and a
page about neither is labelled ``{}``.

**Why a reference point.** Raw cosine between these embeddings is compressed
into a narrow band: every page shares a large common component with every other
("this is a web page"), so an unrelated page and an on-topic one differ by a
few hundredths. Subtracting that shared component first widens the gap. The
obvious reference is the corpus mean, and it was measured and rejected: it
makes a source's labels depend on what *else* is in the corpus, so a crawl that
spends a month on one topic would quietly move every other source's labels. The
reference here is the mean embedding of a fixed list of generic, topic-free
phrases, embedded by the same model — it depends on nothing but the model.

**NULL and ``{}`` stay different.** NULL is "no pass has read this content";
``{}`` is "read, and about none of the topics". A source with no embedded text
stays NULL, because nothing has been examined — calling it off-topic would be a
claim about text nobody has.

**Labels go stale, and the queue knows when.** Every labelled source records the
*basis* it was labelled under — a fingerprint of the model, the thresholds, the
reference, and every topic's prototype text — and when it was examined. A topic
added, archived or re-described changes the basis, so every source is
re-examined; a re-crawl that wrote new chunks makes that one source stale. There
is no other state, and no stored vectors to invalidate.

Nothing here calls a language model (§2.1): it needs an embedding model, the one
the corpus was built with, and arithmetic.
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
#
# Calibrated against a real crawled corpus of a few thousand sources, every one
# embedded, scored against prototypes built exactly as below. A title-and-URL
# heuristic gave a silver set: pages that name a topic outright as positives,
# and pages plainly about nothing the corpus covers (clinical condition pages,
# court rules, privacy and contact pages, unrelated academic listings) as
# negatives.
#
#   - Area under the ROC curve ~0.985 with the reference point, ~0.97 without.
#   - At LABEL_FLOOR ~80% of positives keep their topic and under 1% of
#     negatives gain one. Read side by side, the band just below it (0.42–0.45)
#     was mostly institutional landing pages, publication listings and legal
#     indexes that mention a topic among many — so the floor sits above them
#     rather than at the "best balanced accuracy" point near 0.40, which let
#     roughly one negative in twenty through.
#   - Sources made *only* of duplicate chunks separate far worse (AUC ~0.75):
#     their text is mostly the navigation they share with the page they
#     duplicate. They are still labelled — see `source_vectors` — and are also
#     the sources search already hides.
#
# Re-measured on the live corpus (`B-83`), which the silver set did not
# resemble: it held no generic government pages, and a crawl of government
# sites is mostly those. Judged by reading, sources whose best score sat in
# 0.45–0.48 were right about one time in six, 0.48–0.50 one in three, 0.50–0.52
# about half, and 0.55 and over every time sampled. Agency "about" pages,
# budget speeches, tax and careers pages share a topic's vocabulary without
# being about it. At 0.50, each true label given up removes nearly four false
# ones; at 0.52 the trade is about even, so the floor stops at 0.50.
#
# Re-measure before moving any of these; `python -m worker.retopic` prints the
# distribution it saw, which is where the next calibration starts.

#: The absolute floor. A topic below it is not a label however it ranks. Was
#: 0.45 (the silver set's line) until `B-83`.
LABEL_FLOOR = 0.50

#: How far below the best topic another may sit and still be a label. Adjacent
#: topics in one field score close together on a page about either; inside this
#: margin the page was, when read, usually about both (a study of one topic's
#: vehicles inside another's service model), and past it the second score was
#: the field's shared vocabulary. Wider admitted third labels on generic pages.
LABEL_MARGIN = 0.04

#: Below this best-topic score a source is about nothing the corpus covers —
#: the only threshold `--demote-offtopic` reads, and overridable there. Clearly
#: under LABEL_FLOOR on purpose: labelling is re-derived every pass, while a
#: demotion hands a source to the retention sweep. On the calibration corpus
#: every sampled source under it was off-topic by content, and the handful of
#: silver positives under it were search-result pages and bot-wall or error
#: pages whose *titles* named a topic and whose text did not.
OFFTOPIC_FLOOR = 0.30

#: Below this, a sample's best score holds back the rest of its document
#: (`B-89`; `chunks.SAMPLE_HEAD`). Under the floor rather than at it because a
#: sample misreads the whole text by a few hundredths: on a live corpus, holding
#: at LABEL_FLOOR would have held back about one on-topic long document in ten,
#: and at this line about one in fifty — none of those over 300 passages —
#: while still holding back most of the off-topic ones. Holding orders the
#: embedding and deletes nothing, so a miss costs a delay, not a document. Not
#: part of the basis: it decides what is embedded next, never a label.
TRIAGE_FLOOR = LABEL_FLOOR - 0.04

#: Generic, topic-free phrases whose mean embedding is the reference point.
#: What every page shares — navigation, boilerplate, the vocabulary of being a
#: document at all. Changing the list changes the basis, so every source is
#: re-examined; that is the right cost for moving the origin everything is
#: measured from.
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

#: Aliases shorter than this are left out of a prototype. They are mostly
#: acronyms, which an embedder reads as noise at best and as a different
#: expansion at worst — the same reasoning, and the same number, as the URL
#: matcher's floor.
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

    ``name: description; phrase; phrase``. Deterministic — phrases are
    de-duplicated case-insensitively and sorted — because the text is part of
    the basis fingerprint, and an order that varied between runs would re-label
    the corpus for nothing.
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

    Vocabulary is the approved, unrejected gazetteer terms carrying the topic —
    canonical forms always, aliases only when the term is unambiguous and the
    alias is long enough to mean something. An unapproved term is a proposal
    awaiting a person (§5.6) and does not get to move labels.
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

    Every topic at or above ``floor`` and within ``margin`` of the best. Best
    first because consumers that can show only one — a map colours a point once
    — should show the one the text is most about. Ties break by name so the
    order is stable.
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

    Has at least one live embedded chunk, has no chunk of its *sample* still
    waiting for a vector, and is either unexamined, examined under a different
    basis, or has live chunks newer than its examination (a re-crawl rewrote
    it). Or: was labelled from its sample and is now embedded whole.

    The sample and not every chunk (`B-89`). Waiting for all of them made a
    long document's labels, and so whether it was worth embedding, cost the
    whole document; the sample (`chunks.in_sample`) spans the text rather than
    being whichever half embedded first, which is what waiting guarded
    against. A label read from it is provisional — ``topic_sample_best``
    records it — and is read again from the whole text once that is embedded.
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
    """Which of these sources have live chunks without a vector — whose labels,
    if read now, are read from their sample (`B-89`)."""
    if not source_ids:
        return set()
    rows = await sess.scalars(
        select(Chunk.source_id)
        .where(Chunk.source_id.in_(list(source_ids)), _live(), Chunk.embedding.is_(None))
        .distinct()
    )
    return set(rows)


async def sources_awaiting(
    sess: AsyncSession, fingerprint: str, *, limit: int, after: int = 0
) -> list[int]:
    """The next ``limit`` source ids needing labels, past ``after``. A cursor, so a
    report-only pass that writes nothing still moves forward."""
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

    Non-duplicate chunks where a source has any: a chunk the novelty gate
    marked a duplicate is text the corpus already holds elsewhere, and on a
    real site that is mostly the navigation and footer repeated on every page
    — exactly the part of a page that says nothing about its subject. A source
    made *only* of duplicates falls back to them rather than going unlabelled,
    because its text is still its text.
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

    Replaces, never accumulates — unlike ``crawled_for``. A label is a claim
    about the text as it is now, under the topics as they are now, and the
    previous answer is exactly what a re-examination exists to supersede.

    ``sampled`` says the scores came from part of the text (`B-89`); the best
    of them is kept as ``topic_sample_best``, which decides whether the rest is
    embedded and marks the labels for reading again. Whole text clears it.
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

    Only under the current basis: a score computed against topics that have
    since changed says nothing about whether the page is off-topic *now*.
    Already-junk sources are left out, so the count is what a demotion would
    change. So are sources labelled from a sample (`B-89`): junk is never
    embedded, so demoting on part of a text would stop the rest from ever
    being read — the one outcome holding back was designed not to have.
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
