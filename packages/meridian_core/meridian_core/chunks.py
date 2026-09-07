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

**But replacement is not free, and the schema does not yet say so.**
`edges.supporting_chunk_ids` is an array of ids with no foreign key behind it,
so an edge whose evidence is deleted keeps pointing at nothing. Today no edges
exist, so nothing is orphaned — see `P1-32`, which is where superseding rather
than deleting has to be worked out, and which needs the graph to exist first.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import delete, func, select
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
    sess: AsyncSession, source_id: int, chunks: Sequence[ChunkWrite]
) -> tuple[int, int]:
    """Make ``chunks`` the complete set for ``source_id``. Returns (written, deleted).

    Flushes; does not commit. The delete and the insert belong to the caller's
    transaction on purpose — they are one change, and a crash between them
    leaves a source with no chunks at all, which reads as "never extracted"
    rather than as "half replaced".
    """
    deleted = await delete_chunks(sess, source_id)

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
    if deleted:
        log.info(
            "chunks replaced",
            extra={"source_id": source_id, "written": written, "deleted": deleted},
        )
    return written, deleted


async def delete_chunks(sess: AsyncSession, source_id: int) -> int:
    """Remove every chunk for a source. Returns how many. Flushes."""
    result = await sess.execute(delete(Chunk).where(Chunk.source_id == source_id))
    await sess.flush()
    return result.rowcount or 0


async def chunk_count(sess: AsyncSession, source_id: int | None = None) -> int:
    """How many chunks exist, for one source or for the whole corpus."""
    query = select(func.count()).select_from(Chunk)
    if source_id is not None:
        query = query.where(Chunk.source_id == source_id)
    return await sess.scalar(query) or 0


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
        .where(Chunk.embedding.is_(None), Chunk.chunk_id > after_id)
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
        await sess.scalar(select(func.count()).select_from(Chunk).where(Chunk.embedding.is_(None)))
        or 0
    )
