"""Topics per passage: the source labeller's method, applied to each chunk (task P2-24).

Each live embedded chunk is scored on its own vector against the same prototypes and
reference, beside the source's labels. Passage floor and margin equal the source's; a
passage mostly of link labels (:data:`LISTING_SHARE`) is labelled ``{}``. Rows go
stale with the passage basis or the embedding view. No language model (§2.1). See
docs/features/topics.md#passages.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence

import numpy as np
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .embedtext import link_text_share
from .models import Chunk, ChunkTopics, Source
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

    The source basis plus the passage thresholds, so anything that re-labels sources
    re-labels passages, and a passage threshold re-labels passages alone.
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
    # An alias, so the EXISTS stays its own scan when the outer query also joins
    # `chunk_topics`; otherwise SQLAlchemy correlates both away and leaves no FROM.
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
    """Write a batch of passage labels.

    Replaces, never accumulates — for the reason `record_labels` does: a label is a claim about the
    vector as it is now under the topics as they are now. Returns rows written.
    """
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


def carries_all_topics(topics: Sequence[str]):
    """A predicate over ``Chunk`` joined to ``Source`` carrying every one of ``topics``.

    The passage's own labels and its source's must between them carry each topic
    (`B-72`).
    """
    wanted = list(topics)
    row = aliased(ChunkTopics)
    combined = func.array_cat(
        func.coalesce(Source.topic_labels, sql_text("'{}'::text[]")),
        func.coalesce(row.topic_labels, sql_text("'{}'::text[]")),
    )
    return or_(
        Source.topic_labels.op("@>")(wanted),
        exists().where(row.chunk_id == Chunk.chunk_id, combined.op("@>")(wanted)),
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
