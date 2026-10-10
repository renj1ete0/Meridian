"""Writing chunks for a source (task P2-02, spec §5.3, §6.2, §6.3).

A re-crawl replaces a source's chunks in one transaction; the new ones get new ids, so
the slow loop re-reads them. Replaced chunks are superseded (`superseded_at`), never
deleted, because edges cite them; everything that serves the corpus filters on
``superseded_at IS NULL``. Also the embedding queue and its tiers. See
docs/features/extraction.md#superseded-chunks.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Collection, Iterable, Mapping, Sequence

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .embedtext import continues_table, table_head
from .logging import get_logger
from .models import Chunk, Source
from .storable import storable

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

    Flushes; does not commit: the supersede and the insert are one change, in the
    caller's transaction.
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
                text=storable(chunk.text),
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

    Only the live ones, so an older generation keeps its timestamp.
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

    Deliberately not on a timer: the sweep reports, and a person passes ``--apply``.
    """
    ids = select(_reclaimable(before).subquery().c.chunk_id)
    result = await sess.execute(delete(Chunk).where(Chunk.chunk_id.in_(ids)))
    await sess.commit()
    return result.rowcount or 0


#: The column every provenance-carrying table names its evidence in (§2.3).
CITATION_COLUMN = "supporting_chunk_ids"


def citing_tables() -> tuple[str, ...]:
    """Every table with a ``supporting_chunk_ids`` column, read from the models.

    Derived rather than listed (`B-46`): a hand-written list once missed entities.
    """
    return tuple(
        sorted(t.name for t in Chunk.metadata.tables.values() if CITATION_COLUMN in t.columns)
    )


#: Every reason a retired chunk must stay: something cites it. SQL rather than
#: `~exists()`, which cannot negate a text fragment. Table names come from the models.
def _cited(chunk_ref: str) -> str:
    """SQL true when the chunk ``chunk_ref`` names is evidence for anything."""
    return " OR ".join(
        f"EXISTS (SELECT 1 FROM {table} c WHERE {chunk_ref} = ANY(c.{CITATION_COLUMN}))"
        for table in citing_tables()
    )


_UNCITED = f"NOT ({_cited('chunks.chunk_id')})"


async def cited_source_ids(sess: AsyncSession) -> set[int]:
    """Sources with at least one chunk that anything with provenance cites.

    The one definition every pass that retires or rewrites material asks —
    the raw-file sweep, the furniture pass, the re-chunk pass. There had been
    three, each naming a different subset of the citing tables.
    """
    rows = await sess.execute(
        sql_text(f"SELECT DISTINCT k.source_id FROM chunks k WHERE {_cited('k.chunk_id')}")
    )
    return {row[0] for row in rows}


def _reclaimable(before=None):
    """Superseded chunks that nothing with provenance cites."""
    query = select(Chunk.chunk_id).where(Chunk.superseded_at.is_not(None), sql_text(_UNCITED))
    if before is not None:
        query = query.where(Chunk.superseded_at < before)
    return query


def as_writes(chunks: Iterable[object]) -> list[ChunkWrite]:
    """Adapt anything with ``text``, ``index`` and ``offset`` into writes.

    Duck-typed, because `meridian_core` must not import `worker.extract.chunk`.
    """
    return [
        ChunkWrite(
            text=chunk.text,  # type: ignore[attr-defined]
            chunk_index=chunk.index,  # type: ignore[attr-defined]
            page_or_offset=chunk.offset,  # type: ignore[attr-defined]
        )
        for chunk in chunks
    ]


#: Embedding tiers, in the order the backfill serves them (`B-66`). Junk is in no
#: tier. See docs/features/embedding.md#tiers.
EMBED_TIERS = ("first", "then", "last")

#: The tier served newest first (`B-75`); the others go oldest first.
NEWEST_FIRST_TIER = "first"

#: A long document is embedded from a sample first (`B-89`): its opening passages,
#: then every SAMPLE_STRIDE-th. See docs/features/embedding.md#sampling.
SAMPLE_HEAD = 16
SAMPLE_STRIDE = 16


def in_sample(chunk=None):
    """Whether a passage is in its source's sample.

    Needs no count of the source's passages, so it costs the tier query nothing but arithmetic on
    the row.
    """
    chunk = Chunk if chunk is None else chunk
    return or_(chunk.chunk_index < SAMPLE_HEAD, chunk.chunk_index % SAMPLE_STRIDE == 0)


def _held():
    """The rest of a source whose sample has not earned it (`B-89`).

    Past the sample, a passage waits in the last tier until the labeller has read the
    sample, and stays there if it scored under `topiclabels.TRIAGE_FLOOR`.
    """
    from .topiclabels import LONG_DOCUMENT, LONG_TRIAGE_FLOOR, TRIAGE_FLOOR

    # A live passage at index LONG_DOCUMENT - 1 or beyond: one probe of uq_chunks_live_index.
    longer = aliased(Chunk)
    long_document = exists().where(
        longer.source_id == Source.source_id,
        longer.chunk_index >= LONG_DOCUMENT - 1,
        longer.superseded_at.is_(None),
    )
    best = Source.topic_sample_best
    return and_(
        ~in_sample(),
        # Spelled out rather than a bare `<`: NULL < x is NULL, and a NULL
        # here would put the passage in no tier at all — never embedded.
        or_(
            Source.topics_examined_at.is_(None),
            and_(
                best.is_not(None),
                or_(best < TRIAGE_FLOOR, and_(best < LONG_TRIAGE_FLOOR, long_document)),
            ),
        ),
    )


def _host_of(url_column):
    """`boilerplate.host_key` in SQL: lower-case host, port and a leading www. dropped."""
    host = func.split_part(func.split_part(url_column, "://", 2), "/", 1)
    return func.regexp_replace(func.lower(host), r"^www\.|:\d+$", "", "g")


def _host_standing(*, off_topic: bool):
    from .hostscores import MIN_EXAMINED, OFFTOPIC_SHARE
    from .models import HostScore

    share = HostScore.on_topic * 1.0 / func.nullif(HostScore.examined, 0)
    cond = share < OFFTOPIC_SHARE if off_topic else share >= OFFTOPIC_SHARE
    return exists().where(
        HostScore.host == _host_of(Source.url), HostScore.examined >= MIN_EXAMINED, cond
    )


def _directed():
    """Somebody or something *chose* the page — the directed claim's definition, not a copy."""
    from .models import QueueTask
    from .queueing import FOLLOWED_SOURCES

    return exists().where(
        QueueTask.url_or_query == Source.url, QueueTask.seed_source.not_in(FOLLOWED_SOURCES)
    )


def _a_copy():
    """A source marked as a copy of an earlier one (`B-44`), whatever found it.

    Last rather than no tier (`B-127`): the mark is re-judged daily, and the near
    rule compares mean vectors. See docs/features/duplicates.md.
    """
    return Source.duplicate_of.is_not(None)


def embed_tier(tier: str):
    """The predicate for one embedding tier, over ``Chunk`` joined to ``Source``."""
    not_junk = Source.retention_tier != "junk"
    # `B-160`: a page's sample on a host not judged off-topic is how an unjudged host gets
    # judged; served oldest first behind the crawl's own, it waited out every crawl.
    first = or_(
        _directed(),
        _host_standing(off_topic=False),
        and_(in_sample(), ~_host_standing(off_topic=True)),
    )
    waits = or_(_held(), _a_copy())
    if tier == "first":
        return and_(not_junk, first, ~waits)
    if tier == "then":
        return and_(not_junk, ~first, ~_host_standing(off_topic=True), ~waits)
    if tier == "last":
        return and_(not_junk, or_(waits, and_(~_directed(), _host_standing(off_topic=True))))
    raise ValueError(f"no embedding tier {tier!r}; expected one of {EMBED_TIERS}")


async def chunks_without_embeddings(
    sess: AsyncSession,
    *,
    limit: int = 256,
    after_id: int = 0,
    tier: str | None = None,
    newest_first: bool = False,
    exclude: Collection[int] = (),
) -> list[Chunk]:
    """The next batch of chunks that have no vector yet (task `P2-01`).

    Ordered by id and resumable through ``after_id``, never by OFFSET. ``tier``
    (`B-66`) narrows to one of :data:`EMBED_TIERS`; ``newest_first`` (`B-75`) takes
    the highest ids, and ``exclude`` steps past a failed batch. See
    docs/features/embedding.md#tiers.
    """
    stmt = (
        select(Chunk)
        # Superseded chunks are excluded: embedding text that is no longer on
        # the page spends the model's time producing a vector nothing may search.
        .where(
            Chunk.embedding.is_(None),
            Chunk.superseded_at.is_(None),
            Chunk.chunk_id > after_id,
        )
        .order_by(Chunk.chunk_id.desc() if newest_first else Chunk.chunk_id)
        .limit(limit)
    )
    if exclude:
        stmt = stmt.where(Chunk.chunk_id.not_in(list(exclude)))
    if tier is not None:
        stmt = stmt.join(Source, Source.source_id == Chunk.source_id).where(embed_tier(tier))
    rows = await sess.execute(stmt)
    return list(rows.scalars())


async def store_embeddings(
    sess: AsyncSession, vectors: Mapping[int, Sequence[float]], *, view: int | None = None
) -> int:
    """Attach vectors to chunks by id. Returns how many landed. Flushes.

    ``view`` records which `embedtext.VIEW_VERSION` the vectors were computed
    from (`B-49`); a caller embedding raw text leaves it None.

    Skips a chunk that has vanished rather than failing the batch: a re-crawl replaced
    it, and its successor is already queued.
    """
    if not vectors:
        return 0

    rows = await sess.execute(select(Chunk).where(Chunk.chunk_id.in_(list(vectors))))
    written = 0
    for chunk in rows.scalars():
        chunk.embedding = list(vectors[chunk.chunk_id])
        chunk.embedding_view = view
        written += 1

    await sess.flush()
    if written != len(vectors):
        log.info(
            "some chunks vanished before their vectors were stored",
            extra={"requested": len(vectors), "written": written},
        )
    return written


async def embedding_backlog(sess: AsyncSession, *, valuable_only: bool = False) -> int:
    """How many chunks are still waiting for a vector.

    The number §12.5's health line wants. ``valuable_only`` (`B-66`) counts the
    ``first`` and ``then`` tiers only, what backpressure waits for; see
    docs/features/embedding.md#backpressure.
    """
    stmt = (
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.embedding.is_(None), Chunk.superseded_at.is_(None))
    )
    if valuable_only:
        stmt = stmt.join(Source, Source.source_id == Chunk.source_id).where(
            or_(embed_tier("first"), embed_tier("then"))
        )
    return (await sess.scalar(stmt)) or 0


#: How far back a continued table's header is looked for, in passages of the same source.
TABLE_HEAD_LOOKBACK = 40


async def table_heads(sess: AsyncSession, chunks: Sequence[Chunk]) -> dict[int, str]:
    """For passages that continue a table, the header row from where the table began (`B-195`).

    Walks back through the same source's live passages, newest first, and stops at the first
    that holds the header, or at one that is not part of a table at all (the table ended).
    """
    heads: dict[int, str] = {}
    for chunk in chunks:
        if not continues_table(chunk.text):
            continue
        earlier = await sess.scalars(
            select(Chunk.text)
            .where(
                Chunk.source_id == chunk.source_id,
                Chunk.chunk_index < chunk.chunk_index,
                Chunk.superseded_at.is_(None),
            )
            .order_by(Chunk.chunk_index.desc())
            .limit(TABLE_HEAD_LOOKBACK)
        )
        for text in earlier:
            head = table_head(text)
            if head:
                heads[chunk.chunk_id] = head
                break
            if "|" not in text:
                break
    return heads
