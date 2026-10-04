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
from collections.abc import Collection, Iterable, Mapping, Sequence

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import Chunk, Source

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


#: The column every provenance-carrying table names its evidence in (§2.3).
CITATION_COLUMN = "supporting_chunk_ids"


def citing_tables() -> tuple[str, ...]:
    """Every table with a ``supporting_chunk_ids`` column, read from the models.

    Derived rather than listed (`B-46`). The list was written out by hand with
    edges, observations and attribute values, and entities — which carry the
    same column — were left out, so a sweep could delete a retired chunk that
    only an entity cited. A table that gains provenance later is covered the
    moment its model declares the column.
    """
    return tuple(
        sorted(t.name for t in Chunk.metadata.tables.values() if CITATION_COLUMN in t.columns)
    )


#: Every reason a retired chunk must stay: an edge's (or entity's, or
#: observation's) evidence is not reclaimable space, it is the thing that makes
#: the claim checkable.
#:
#: Written as SQL rather than built with `~exists()` because the clause is
#: negated, and SQLAlchemy cannot negate a text fragment — which is how the
#: first version of this failed, loudly and immediately, rather than by quietly
#: matching everything. Table names come from the models, never from input.
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


#: Embedding tiers, in the order the backfill serves them (`B-66`).
#: ``first`` — passages of directed sources (a search result, a person's seed, a
#: cited paper) or of hosts judged on-topic; ``then`` — everything else that is
#: not junk; ``last`` — hosts judged off-topic, the rest of a long document
#: whose sample has not earned it (`B-89`), and copies of an earlier source
#: (`B-127`). Junk is in no tier.
EMBED_TIERS = ("first", "then", "last")

#: The tier served newest first (`B-75`); the others go oldest first.
NEWEST_FIRST_TIER = "first"

#: A long document is embedded from a sample first (`B-89`): its opening
#: passages and then every SAMPLE_STRIDE-th, so the sample spans the whole
#: text rather than its front matter. A source of up to SAMPLE_HEAD passages
#: is all sample. Chosen on a live corpus by labelling fully embedded sources
#: from the sample and from everything: at 16/16 the sample's best score was
#: within about 0.015 of the whole text's at the median, for about a sixth of
#: the embedding on sources of 40 passages or more.
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

    Past the sample, a passage waits in the last tier until the labeller has
    read the sample — and stays there if the sample scored under
    `topiclabels.TRIAGE_FLOOR`. The large documents a crawl brings back are
    mostly off-topic (bills, data dictionaries, index pages of thousands of
    passages), and without this each one costs hours of embedding before the
    labeller may say so. Held is not dropped: the last tier is still served.
    """
    from .topiclabels import TRIAGE_FLOOR

    return and_(
        ~in_sample(),
        # Spelled out rather than a bare `<`: NULL < x is NULL, and a NULL
        # here would put the passage in no tier at all — never embedded.
        or_(
            Source.topics_examined_at.is_(None),
            and_(Source.topic_sample_best.is_not(None), Source.topic_sample_best < TRIAGE_FLOOR),
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

    Search, the map, Gaps and synthesis all leave a copy out, so a vector for
    the rest of one is spent on nothing a reader sees (`B-127`). Last rather
    than no tier: the mark is re-judged daily and cleared when it no longer
    holds, and the near and translation rules compare mean vectors, so a copy
    is never kept from the embedder for good.
    """
    return Source.duplicate_of.is_not(None)


def embed_tier(tier: str):
    """The predicate for one embedding tier, over ``Chunk`` joined to ``Source``."""
    not_junk = Source.retention_tier != "junk"
    first = or_(_directed(), _host_standing(off_topic=False))
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

    Ordered by id and resumable through ``after_id`` rather than by offset. A
    backfill that pages with OFFSET re-scans everything it has already read on
    every page, and worse, shifts under its own feet as the crawl writes new
    chunks in the middle of the run.

    `embedding IS NULL` is the whole queue. `P2-02` writes chunks with no vector
    by design — embedding is a separate pass so the fetch loop never waits on a
    model — so a NULL here means "not embedded yet" and nothing else.

    ``tier`` (`B-66`) narrows the queue to one of :data:`EMBED_TIERS`. The
    embedder is the slowest stage on modest hardware, so under a free crawl its
    backlog is permanent, and oldest-first spends it on whatever the crawl
    happened to fetch first. Junk is in no tier and is never embedded.

    ``newest_first`` (`B-75`) takes the highest ids, still above ``after_id``:
    labels, host judgments and steering all wait on a vector, so the passages
    a crawl just fetched are the ones whose embedding tells it something. With
    no advancing cursor, ``exclude`` is how a failed batch is stepped past.
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

    The number §12.5's health line wants: a backlog that only grows means the
    embedder has stopped, which is otherwise invisible — the crawl keeps
    working and the corpus keeps growing and none of it becomes searchable.

    ``valuable_only`` (`B-66`) counts the ``first`` and ``then`` tiers only —
    what backpressure should wait for. Passages of hosts judged off-topic are
    embedded last, if ever; counting them would pause the crawl for good.
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
