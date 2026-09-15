"""`/api/explore/*` — the read surface (task P2-07, spec §12.5, §12.6).

Every route here reads through `meridian_ro`. §12.6 makes the prefix the
role boundary so that adding auth later is middleware on a path rather than a
refactor of each handler, and that only holds if nothing under this prefix ever
reaches for a writable session. Nothing here does; `deps` does not offer one.

**The shape follows what reading actually requires.** §12.5's bottleneck is
reading time, not generation, and §2 principle 3 is that nothing is assertable
without a citation you can follow back to a file. So a hit carries its source
inline, and there is a route for the chunks *around* a hit — having found a
passage, the next thing a reader wants is its context, and a surface that makes
that a second search sends them back through the ranking to find a neighbour.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from meridian_core.export import to_bibtex, to_markdown
from meridian_core.models import Chunk, Source
from meridian_core.schemas.enums import SourceTier
from meridian_core.schemas.search import CorpusStatsRead, SearchResponse, SourceChunksRead
from meridian_core.schemas.source import ChunkRead, SourceRead
from meridian_core.search import DEFAULT_CANDIDATES, SearchFilters
from meridian_core.stats import corpus_stats

from ..deps import ReadSession
from ..search_service import WindowTooDeep, paged_search

router = APIRouter(prefix="/api/explore", tags=["explore"])

#: How many sources one export may name. A bibliography is assembled by hand
#: from results somebody read; a request for a thousand is a client looping over
#: the corpus, which is what `list_new_since` is for.
MAX_EXPORT_SOURCES = 200

#: Caps on one page. The upper bound is not politeness — a chunk is up to 2000
#: characters, so an uncapped limit is a request that can ask for megabytes of
#: text and a response nobody renders.
MAX_LIMIT = 100
DEFAULT_LIMIT = 20


# `Annotated[...]` rather than `= Query(...)` defaults throughout. Both work;
# this one keeps the default an ordinary value, which means a handler stays
# callable from a test without constructing FastAPI parameter objects — and it
# is what FastAPI's own documentation now recommends.
@router.get("/search", response_model=SearchResponse)
async def explore_search(
    sess: ReadSession,
    q: Annotated[
        str, Query(description="Search text. Empty returns no hits rather than an error.")
    ] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    source_tier: Annotated[
        list[SourceTier] | None, Query(description="Repeat to allow several.")
    ] = None,
    language: Annotated[list[str] | None, Query()] = None,
    published_after: Annotated[dt.date | None, Query()] = None,
    published_before: Annotated[dt.date | None, Query()] = None,
    include_duplicates: Annotated[
        bool,
        Query(
            description="Near-duplicates are excluded by default — the novelty gate marked "
            "them for a reason. Include them to see why something is missing."
        ),
    ] = False,
    include_junk: Annotated[
        bool,
        Query(description="§5.4's junk tier: material a retention sweep will eventually drop."),
    ] = False,
    candidates: Annotated[
        int,
        Query(
            ge=1,
            le=1000,
            description="How deep each arm goes before fusion. Fusion can only reorder what "
            "the arms handed it, so this bounds how far paging can reach.",
        ),
    ] = DEFAULT_CANDIDATES,
) -> SearchResponse:
    """Hybrid retrieval over the corpus.

    An empty `q` returns an empty result rather than a 422. An empty search box
    is a state a UI has on first render, and making the client special-case its
    own initial paint to avoid an error is a worse boundary than returning the
    honest answer: nothing was asked, so nothing was found.

    Check `degraded` on every response. This deployment has no embedder, so the
    vector arm does not run and results are matched on words rather than
    meaning — see `api/search_service.py` for why, and what would change it.
    """
    filters = SearchFilters(
        source_tiers=source_tier,
        languages=language,
        published_after=published_after,
        published_before=published_before,
        include_duplicates=include_duplicates,
        include_junk=include_junk,
    )
    try:
        return await paged_search(
            sess, q, filters=filters, limit=limit, offset=offset, candidates=candidates
        )
    except WindowTooDeep as exc:
        # 422, not 500: the request is the thing that is wrong, and the message
        # names both remedies.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stats", response_model=CorpusStatsRead)
async def explore_stats(sess: ReadSession) -> CorpusStatsRead:
    """What the corpus holds.

    Behind the Explore landing state's counts. `searchable_chunks` is
    deliberately separate from `chunks`: §12.5 turns on a reader being able to
    tell "we never collected this" from "we collected it and filtered it", and
    one number for both erases exactly that.
    """
    return CorpusStatsRead.model_validate(await corpus_stats(sess))


@router.get("/sources/{source_id}", response_model=SourceRead)
async def explore_source(source_id: int, sess: ReadSession) -> SourceRead:
    """One source, with everything recorded about how it was acquired.

    Including `extractor` (`P1-44`) and the OCR columns, because "this source
    has no text" and "this source is a scan nobody has run OCR on" are different
    answers and §6.5 makes metadata-only a valid resting state rather than a
    failure.
    """
    source = await sess.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"no source {source_id}")
    return SourceRead.model_validate(source)


@router.get("/sources/{source_id}/chunks", response_model=SourceChunksRead)
async def explore_source_chunks(
    source_id: int,
    sess: ReadSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SourceChunksRead:
    """A source's chunks in document order — the context around a hit.

    Ordered by `chunk_index`, which is document order, so an offset here means
    what an offset normally means. That is not true of `/search`, where the
    order is a fused rank; keeping the two kinds of paging in separate routes
    stops one being mistaken for the other.

    A source that exists with no chunks returns an empty list, not a 404. §6.5:
    metadata-only is a resting state, and a reader who followed a link here
    needs to see that the source is real and unextracted rather than absent.
    """
    if await sess.get(Source, source_id) is None:
        raise HTTPException(status_code=404, detail=f"no source {source_id}")

    rows = (
        await sess.execute(
            select(Chunk)
            .where(Chunk.source_id == source_id)
            .order_by(Chunk.chunk_index)
            .offset(offset)
            .limit(limit + 1)
        )
    ).scalars()
    chunks = list(rows)

    return SourceChunksRead(
        source_id=source_id,
        chunks=[ChunkRead.model_validate(c) for c in chunks[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(chunks) > limit,
    )


@router.get("/chunks/{chunk_id}", response_model=ChunkRead)
async def explore_chunk(chunk_id: int, sess: ReadSession) -> ChunkRead:
    """One chunk, including the novelty gate's verdict on it.

    `duplicate_of` and `nearest_similarity` are exposed for the reason §12.5
    gives: a chunk filtered as a near-duplicate and a chunk that was never
    crawled look identical from a result set, and only one of them is worth
    investigating.
    """
    chunk = await sess.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"no chunk {chunk_id}")
    return ChunkRead.model_validate(chunk)


@router.get("/export/bibtex", response_class=PlainTextResponse)
async def explore_export_bibtex(
    sess: ReadSession,
    source_id: Annotated[list[int], Query(description="Sources to cite. Repeat the key.")],
) -> str:
    """A BibTeX bibliography for the given sources (`P6-15`, §12.5).

    Explicit ids rather than a search query, deliberately. A bibliography is
    something a person assembled — they read the results, kept some, and are
    exporting *those*. Exporting a whole result set would produce a file whose
    contents depend on a ranking that moves as the corpus grows, which is not a
    citation list, it is a snapshot of an opinion.

    Plain text, because that is what a `.bib` file is. A JSON envelope would put
    every consumer one `json.loads` and one escape away from a file they could
    have saved directly.
    """
    if not source_id:
        raise HTTPException(status_code=422, detail="Name at least one source_id.")
    if len(source_id) > MAX_EXPORT_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_EXPORT_SOURCES} sources per export; asked for {len(source_id)}.",
        )

    rows = (await sess.execute(select(Source).where(Source.source_id.in_(source_id)))).scalars()
    return to_bibtex(list(rows))


@router.get("/export/markdown", response_class=PlainTextResponse)
async def explore_export_markdown(
    sess: ReadSession,
    source_id: Annotated[list[int], Query(description="Sources to export. Repeat the key.")],
) -> str:
    """Passages as Markdown, grouped under their sources (`P6-15`, §12.5)."""
    if not source_id:
        raise HTTPException(status_code=422, detail="Name at least one source_id.")
    if len(source_id) > MAX_EXPORT_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_EXPORT_SOURCES} sources per export; asked for {len(source_id)}.",
        )

    rows = (
        await sess.execute(
            select(Chunk, Source)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.source_id.in_(source_id), Chunk.duplicate_of.is_(None))
            .order_by(Chunk.source_id, Chunk.chunk_index)
        )
    ).all()
    return to_markdown([(chunk, source) for chunk, source in rows])
