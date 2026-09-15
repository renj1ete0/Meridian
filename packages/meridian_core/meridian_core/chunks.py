"""Writing chunks for a source (task P2-02, spec §5.3, §6.2, §6.3).

Chunks are the unit three separate things are built on — retrieval, the slow
loop's batch, and an edge's cited evidence — so how they are *written* matters
as much as how they are cut.

**A re-crawl replaces them.** A page that changed is a page whose old chunks
describe text that is no longer there, and §2.4's rule is to re-derive from
source rather than to patch. So a content change deletes the source's chunks and
writes new ones, inside one transaction: a source with half its old chunks and
half its new ones beside them is worse than either.

**New chunks get new ids, and that is the point.** §6.3's high-water mark is the
last `chunk_id` the slow loop consumed, so a replaced chunk is naturally picked
up again on the next pass — which is exactly what should happen to a page whose
content changed. Nothing has to notice the change or schedule the re-read.

**Replacement supersedes; it does not delete (`P1-32`).**
`edges.supporting_chunk_ids` is an array of ids with no foreign key behind it —
Postgres cannot enforce one on array elements — so deleting a chunk left every
edge citing it pointing at nothing. That failure is silent in the worst way:
§2.3 makes provenance mandatory, and an orphaned edge still *has* provenance. It
carries a list of ids, passes every check, and only following the citation
reveals there is nothing there. Nothing in the system follows.

So the old rows are stamped with `superseded_at` and stay. Citations keep
resolving; an edge keeps the text it was actually derived from, which matters
because the page has since changed and §2.4 re-derives from source chunks; and
the sweep can reclaim the ones nothing cites, as a decision a person makes
rather than one a crawl makes at write time.

Everything that serves the corpus filters on ``superseded_at IS NULL``. A
superseded chunk is text that is no longer on the page, and serving it would
have the corpus quote a document as saying something it no longer says.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk

log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class ChunkWrite:
    """One chunk to persist. Mirrors the columns and nothing else.

    Deliberately not the worker's ``TextChunk``: this package is imported by the
    API and the orchestrator, and neither has any business depending on the
    extractor's dataclasses to talk about rows.
    """

    text: str
    chunk_index: int
    page_or_offset: int | None = None


async def replace_chunks(
    sess: AsyncSession, source_id: int, chunks: Sequence[ChunkWrite], *, now=None
) -> tuple[int, int]:
    """Make ``chunks`` the live set for ``source_id``. Returns (written, superseded).

    Flushes; does not commit. The supersede and the insert belong to the
    caller's transaction on purpose — they are one change, and a crash between
    them leaves a source with no live chunks at all, which reads as "never
    extracted" rather than as "half replaced".
    """
    superseded = await supersede_chunks(sess, source_id, now=now)

    for chunk in chunks:
        if not chunk.text.strip():
            # An empty chunk satisfies the NOT NULL and means nothing. It would
            # embed to noise and cite nothing, so it is dropped here rather than
            # left for every consumer to guard against.
            continue
        sess.add(
            Chunk(
                source_id=source_id,
                text=chunk.text,
                chunk_index=chunk.chunk_index,
                page_or_offset=chunk.page_or_offset,
            )
        )

    await sess.flush()
    written = sum(1 for chunk in chunks if chunk.text.strip())
    if superseded:
        log.info(
            "chunks replaced",
            extra={"source_id": source_id, "written": written, "superseded": superseded},
        )
    return written, superseded


async def supersede_chunks(sess: AsyncSession, source_id: int, *, now=None) -> int:
    """Retire this source's live chunks without removing them. Flushes.

    Only the live ones. A source re-crawled twice has two generations of
    superseded chunks, and re-stamping the older set would move its timestamp
    forward — which is the one thing the column is for, and would make the
    sweep's "superseded more than N days ago" mean nothing.
    """
    stamp = now or dt.datetime.now(dt.UTC)
    result = await sess.execute(
        update(Chunk)
        .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
        .values(superseded_at=stamp)
    )
    await sess.flush()
    return result.rowcount or 0


async def delete_chunks(sess: AsyncSession, source_id: int) -> int:
    """Remove every chunk for a source, superseded ones included. Returns how many.

    Not what a re-crawl does — see :func:`supersede_chunks`. This is for the
    caller that means it: a source being removed entirely, where leaving its
    chunks would leave rows referring to a source that no longer exists.
    """
    result = await sess.execute(delete(Chunk).where(Chunk.source_id == source_id))
    await sess.flush()
    return result.rowcount or 0


async def chunk_count(
    sess: AsyncSession, source_id: int | None = None, *, live_only: bool = True
) -> int:
    """How many chunks exist, for one source or for the whole corpus.

    Live by default. Every caller asking "how big is the corpus" means the text
    that is on the pages now, and a count that silently included retired
    generations would grow every time a page changed.
    """
    query = select(func.count()).select_from(Chunk)
    if source_id is not None:
        query = query.where(Chunk.source_id == source_id)
    if live_only:
        query = query.where(Chunk.superseded_at.is_(None))
    return await sess.scalar(query) or 0


async def superseded_uncited(sess: AsyncSession, *, before=None) -> int:
    """How many retired chunks no edge cites — what the sweep could reclaim.

    The "cited" half is the same EXISTS the retention sweep uses on raw files
    (§5.4), and for the same reason: an edge's evidence is not reclaimable
    space, it is the thing that makes the edge checkable.
    """
    return await sess.scalar(select(func.count()).select_from(_reclaimable(before).subquery())) or 0


async def purge_superseded(sess: AsyncSession, *, before=None) -> int:
    """Delete retired chunks that no edge cites. Returns how many. Commits.

    Deliberately not called by anything on a timer. A superseded chunk is the
    text an edge *would* have been derived from if one had been written, and a
    crawl that reclaimed it automatically would be making a retention decision
    at the moment it is least able to judge it. The sweep reports the number and
    a person passes ``--apply``, exactly as for raw files.
    """
    ids = select(_reclaimable(before).subquery().c.chunk_id)
    result = await sess.execute(delete(Chunk).where(Chunk.chunk_id.in_(ids)))
    await sess.commit()
    return result.rowcount or 0


#: Every table whose provenance is an array of chunk ids. All three carry
#: `supporting_chunk_ids` (§2.3), and all three are reasons a retired chunk must
#: stay: an edge's evidence is not reclaimable space, it is the thing that makes
#: the edge checkable.
#:
#: Written as SQL rather than built with `~exists()` because the clause is
#: negated, and SQLAlchemy cannot negate a text fragment — which is how the
#: first version of this failed, loudly and immediately, rather than by quietly
#: matching everything.
_UNCITED = (
    "NOT EXISTS (SELECT 1 FROM edges e WHERE chunks.chunk_id = ANY(e.supporting_chunk_ids))"
    " AND NOT EXISTS (SELECT 1 FROM observations o"
    " WHERE chunks.chunk_id = ANY(o.supporting_chunk_ids))"
    " AND NOT EXISTS (SELECT 1 FROM attribute_values a"
    " WHERE chunks.chunk_id = ANY(a.supporting_chunk_ids))"
)


def _reclaimable(before=None):
    """Superseded chunks that no edge, observation or attribute value cites."""
    query = select(Chunk.chunk_id).where(Chunk.superseded_at.is_not(None), sql_text(_UNCITED))
    if before is not None:
        query = query.where(Chunk.superseded_at < before)
    return query


def as_writes(chunks: Iterable[object]) -> list[ChunkWrite]:
    """Adapt anything with ``text``, ``index`` and ``offset`` into writes.

    The seam between the worker's chunker and this package. Duck-typed rather
    than importing `worker.extract.chunk`, because `meridian_core` is what the
    API and orchestrator depend on and it must not gain a dependency on a
    service in order to describe its own table.
    """
    return [
        ChunkWrite(
            text=chunk.text,  # type: ignore[attr-defined]
            chunk_index=chunk.index,  # type: ignore[attr-defined]
            page_or_offset=chunk.offset,  # type: ignore[attr-defined]
        )
        for chunk in chunks
    ]


async def chunks_without_embeddings(
    sess: AsyncSession, *, limit: int = 256, after_id: int = 0
) -> list[Chunk]:
    """The next batch of chunks that have no vector yet (task `P2-01`).

    Ordered by id and resumable through ``after_id`` rather than by offset. A
    backfill that pages with OFFSET re-scans everything it has already read on
    every page, and worse, shifts under its own feet as the crawl writes new
    chunks in the middle of the run.

    `embedding IS NULL` is the whole queue. `P2-02` writes chunks with no vector
    by design — embedding is a separate pass so the fetch loop never waits on a
    model — so a NULL here means "not embedded yet" and nothing else.
    """
    rows = await sess.execute(
        select(Chunk)
        # Superseded chunks are excluded: embedding text that is no longer on
        # the page spends the model's time producing a vector nothing may search.
        .where(
            Chunk.embedding.is_(None),
            Chunk.superseded_at.is_(None),
            Chunk.chunk_id > after_id,
        )
        .order_by(Chunk.chunk_id)
        .limit(limit)
    )
    return list(rows.scalars())


async def store_embeddings(sess: AsyncSession, vectors: Mapping[int, Sequence[float]]) -> int:
    """Attach vectors to chunks by id. Returns how many landed. Flushes.

    Skips a chunk that has vanished rather than failing the batch: a source
    re-crawled between the read and the write has had its chunks replaced
    (`replace_chunks`), and the new ones are already in the queue behind this
    batch. Losing the batch over one deleted row would make a long backfill
    fragile in exactly the situation it is most likely to meet.
    """
    if not vectors:
        return 0

    rows = await sess.execute(select(Chunk).where(Chunk.chunk_id.in_(list(vectors))))
    written = 0
    for chunk in rows.scalars():
        chunk.embedding = list(vectors[chunk.chunk_id])
        written += 1

    await sess.flush()
    if written != len(vectors):
        log.info(
            "some chunks vanished before their vectors were stored",
            extra={"requested": len(vectors), "written": written},
        )
    return written


async def embedding_backlog(sess: AsyncSession) -> int:
    """How many chunks are still waiting for a vector.

    The number §12.5's health line wants: a backlog that only grows means the
    embedder has stopped, which is otherwise invisible — the crawl keeps
    working and the corpus keeps growing and none of it becomes searchable.
    """
    return (
        await sess.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.embedding.is_(None), Chunk.superseded_at.is_(None))
        )
        or 0
    )
