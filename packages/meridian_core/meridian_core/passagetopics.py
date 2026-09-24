"""Topics per passage: the source labeller's method, applied to each chunk (task P2-24).

A source carries one set of labels, decided from the mean of its chunk vectors
(:mod:`meridian_core.topiclabels`). That is the right unit for a web page and a
coarse one for a long report or a book: a document mostly about one topic with
a chapter on another is labelled with the first, and the chapter is invisible
to a topic filter and to Gaps. So each live, embedded chunk is scored too —
against the same prototypes, from the same reference point, under the same
:class:`~meridian_core.topiclabels.Basis` — and keeps the labels its own vector
earns. Source labels are untouched: this adds a finer answer beside them.

**Thresholds of their own.** A single passage is noisier than a document mean:
averaging many chunks cancels the incidental vocabulary each one carries, and
one chunk has nobody to cancel it. Measured on a real crawl, passage scores sit
lower and spread wider than source scores, and the band just under the source
floor was mostly listings and abstracts in adjacent fields. So:

- :data:`PASSAGE_FLOOR` is the source floor, not lower. Read side by side, the
  0.40–0.45 band in documents *not* about the topic was mostly noise; above it,
  mostly passages genuinely about the topic inside a document about a
  neighbouring one — which is exactly what this exists to find.
- :data:`PASSAGE_MARGIN` is the source margin.
- :data:`LISTING_SHARE`: a passage whose visible text is mostly link labels is
  a listing (a directory of regulations, a publication list), and it is
  examined and labelled ``{}``. Its vector is the average of what it links to,
  and it scored topics that none of its entries is about. This is structure,
  not a model, and it removed most of the false positives the floor did not.

**Stale when the basis moves or the vector does.** Each row records the passage
basis — the source basis fingerprint plus these thresholds — and the embedding
view the scored vector came from. A topic added or re-described changes the
first; a re-embed under a new view changes the second. A re-crawl writes new
chunks, which have no row until they have a vector. Superseded chunks keep
their row (harmless: nothing that serves the corpus reads a superseded chunk)
and lose it with the chunk.

Nothing here calls a language model (§2.1).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence

import numpy as np
from sqlalchemy import and_, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .embedtext import link_text_share
from .models import Chunk, ChunkTopics
from .topiclabels import LABEL_FLOOR, LABEL_MARGIN, decide

#: The absolute floor for a passage label. See the module docstring for why it
#: is the source floor and not lower.
PASSAGE_FLOOR = LABEL_FLOOR

#: How far below its best topic another may sit and still label the passage.
PASSAGE_MARGIN = LABEL_MARGIN

#: Above this share of visible text in link labels, a passage is a listing and
#: is labelled ``{}``. Most prose has little or no link text; the listings that
#: scored a topic on the calibration crawl sat far above this line.
LISTING_SHARE = 0.5

#: Bumped when the passage method changes in a way the constants do not capture.
PASSAGE_VERSION = 1

#: Chunks per transaction in the pass.
BATCH = 2000


def passage_fingerprint(source_fingerprint: str) -> str:
    """The basis a passage label is decided under.

    Built on the source basis, so everything that re-labels sources — a topic
    added, archived or re-described, a different model, a moved reference —
    re-labels passages too; plus the passage thresholds, so moving one of
    those re-labels passages without touching sources.
    """
    payload = {
        "source": source_fingerprint,
        "version": PASSAGE_VERSION,
        "floor": PASSAGE_FLOOR,
        "margin": PASSAGE_MARGIN,
        "listing": LISTING_SHARE,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"{source_fingerprint}/p{PASSAGE_VERSION}:{digest[:8]}"


def is_listing(text: str) -> bool:
    return link_text_share(text) > LISTING_SHARE


def decide_passage(scores: Mapping[str, float], text: str) -> list[str]:
    """The labels one passage earns, best first. ``[]`` for a listing."""
    if is_listing(text):
        return []
    return decide(scores, floor=PASSAGE_FLOOR, margin=PASSAGE_MARGIN)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def awaiting_passage_labels(fingerprint: str):
    """The predicate for "this chunk needs (re-)labelling".

    Live and embedded, and without a row under this basis for the vector it
    has now. ``IS NOT DISTINCT FROM`` on the view because NULL is a real view
    (vectors computed before views existed) and NULL = NULL is not true.
    """
    # An alias, so the EXISTS stays its own scan when the outer query also
    # joins `chunk_topics` (the queue reads the previous labels that way);
    # without it SQLAlchemy correlates both tables away and the subquery has
    # no FROM at all.
    row = aliased(ChunkTopics)
    current = exists().where(
        row.chunk_id == Chunk.chunk_id,
        row.topic_basis == fingerprint,
        row.embedding_view.is_not_distinct_from(Chunk.embedding_view),
    )
    return and_(Chunk.superseded_at.is_(None), Chunk.embedding.is_not(None), ~current)


@dataclasses.dataclass(frozen=True)
class Passage:
    chunk_id: int
    source_id: int
    text: str
    vector: np.ndarray
    embedding_view: int | None
    #: What the row said before this pass: None when there was no row.
    before: list[str] | None


async def passages_awaiting(
    sess: AsyncSession, fingerprint: str, *, limit: int = BATCH, after: int = 0
) -> list[Passage]:
    """The next ``limit`` chunks needing labels past ``after``, with their vectors.

    A cursor, like the source queue, so a report-only pass still moves forward.
    """
    rows = await sess.execute(
        select(
            Chunk.chunk_id,
            Chunk.source_id,
            Chunk.text,
            Chunk.embedding,
            Chunk.embedding_view,
            ChunkTopics.topic_labels,
        )
        .outerjoin(ChunkTopics, ChunkTopics.chunk_id == Chunk.chunk_id)
        .where(Chunk.chunk_id > after, awaiting_passage_labels(fingerprint))
        .order_by(Chunk.chunk_id)
        .limit(limit)
    )
    return [
        Passage(
            chunk_id,
            source_id,
            text,
            np.asarray(vector, dtype=np.float64),
            view,
            list(before) if before is not None else None,
        )
        for chunk_id, source_id, text, vector, view, before in rows
    ]


async def count_awaiting_passages(sess: AsyncSession, fingerprint: str) -> int:
    return int(
        await sess.scalar(
            select(func.count()).select_from(Chunk).where(awaiting_passage_labels(fingerprint))
        )
        or 0
    )


async def record_passage_labels(
    sess: AsyncSession,
    rows: Sequence[tuple[Passage, Mapping[str, float], list[str]]],
    *,
    fingerprint: str,
    now: dt.datetime,
) -> int:
    """Write a batch of passage labels. Replaces, never accumulates — for the
    reason `record_labels` does: a label is a claim about the vector as it is
    now under the topics as they are now. Returns rows written."""
    if not rows:
        return 0
    values = [
        {
            "chunk_id": passage.chunk_id,
            "topic_labels": labels,
            "topic_scores": dict(scores),
            "topic_basis": fingerprint,
            "embedding_view": passage.embedding_view,
            "topics_examined_at": now,
        }
        for passage, scores, labels in rows
    ]
    stmt = insert(ChunkTopics).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChunkTopics.chunk_id],
        set_={
            "topic_labels": stmt.excluded.topic_labels,
            "topic_scores": stmt.excluded.topic_scores,
            "topic_basis": stmt.excluded.topic_basis,
            "embedding_view": stmt.excluded.embedding_view,
            "topics_examined_at": stmt.excluded.topics_examined_at,
        },
    )
    await sess.execute(stmt)
    return len(values)


# ---------------------------------------------------------------------------
# Reading them back
# ---------------------------------------------------------------------------


def on_topic_passage(topics: Sequence[str]):
    """A predicate over ``Chunk``: this passage is labelled with any of ``topics``.

    Correlated on ``Chunk.chunk_id``, so it drops into any statement that has
    ``chunks`` in its FROM — search's arms, Gaps' counts.
    """
    row = aliased(ChunkTopics)  # never correlated away; see `awaiting_passage_labels`
    return exists().where(
        row.chunk_id == Chunk.chunk_id,
        row.topic_labels.op("&&")(list(topics)),
    )


async def passage_topics_for(sess: AsyncSession, chunk_ids: Sequence[int]) -> dict[int, list[str]]:
    """Each examined chunk's labels. A chunk with no row is absent: not examined."""
    if not chunk_ids:
        return {}
    rows = await sess.execute(
        select(ChunkTopics.chunk_id, ChunkTopics.topic_labels).where(
            ChunkTopics.chunk_id.in_(list(chunk_ids))
        )
    )
    return {chunk_id: list(labels) for chunk_id, labels in rows}
