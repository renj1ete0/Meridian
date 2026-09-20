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
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import desc as sql_desc
from sqlalchemy import func, or_, select

from meridian_core import annotations
from meridian_core.export import to_bibtex, to_markdown
from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    Chunk,
    Edge,
    Entity,
    FetchAttempt,
    Figure,
    Notification,
    SavedView,
    Source,
)
from meridian_core.queueing import queue_depth
from meridian_core.schemas.annotations import AnnotationsRead
from meridian_core.schemas.enums import SourceTier
from meridian_core.schemas.graph import EntityRead
from meridian_core.schemas.runs import NotificationRead
from meridian_core.schemas.search import (
    CorpusStatsRead,
    CrawlProgressRead,
    FigureRefRead,
    NodeAttributeRead,
    NodeDetailRead,
    NotificationsRead,
    SearchHitRead,
    SearchResponse,
    SourceChunksRead,
    SourceFiguresRead,
)
from meridian_core.schemas.source import ChunkRead, SourceRead
from meridian_core.schemas.views import SavedViewRead, SavedViewsRead
from meridian_core.search import DEFAULT_CANDIDATES, SearchFilters, page_unit_for
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

#: How many supporting chunks one node panel carries. An entity with a dozen
#: attributes can cite a hundred chunks, and a panel that returned all of them
#: would be a page of prose where §12.5 asked for evidence a reader can follow.
MAX_SUPPORTING_CHUNKS = 40

#: How many of the reader's own notes the node panel carries. Lower than the
#: chunk cap and deliberately so: the panel shows the most recent few and the
#: notes list shows the rest. A node somebody has annotated forty times is a
#: node they are working on, and burying its attributes under their own backlog
#: is not what §12.5 asked the panel for.
MAX_PANEL_ANNOTATIONS = 10

#: How many notes one export may carry. Notes are the reader's own writing, so
#: this is a page cap rather than a courtesy one — the whole point of §12.5's
#: export is that the material can leave, and a cap that made "all of them"
#: unreachable would defeat it.
MAX_EXPORT_ANNOTATIONS = 500


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
    topic: Annotated[
        list[str] | None,
        Query(
            description="Repeat to allow several; a source matching any of them is kept. "
            "Sources crawled before topics were recorded carry none and are excluded."
        ),
    ] = None,
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
        topics=topic,
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
async def explore_stats(
    sess: ReadSession,
    since: Annotated[
        dt.datetime | None,
        Query(description="Count what arrived after this instant (P6-11). ISO 8601."),
    ] = None,
) -> CorpusStatsRead:
    """What the corpus holds.

    Behind the Explore landing state's counts. `searchable_chunks` is
    deliberately separate from `chunks`: §12.5 turns on a reader being able to
    tell "we never collected this" from "we collected it and filtered it", and
    one number for both erases exactly that.
    """
    return CorpusStatsRead.model_validate(await corpus_stats(sess, since=since))


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
            # Live chunks only (`P1-32`). A source page showing retired text
            # would present passages the document no longer contains, in
            # document order, as though it did.
            .where(Chunk.source_id == source_id, Chunk.superseded_at.is_(None))
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
            .where(
                Chunk.source_id.in_(source_id),
                Chunk.duplicate_of.is_(None),
                Chunk.superseded_at.is_(None),
            )
            .order_by(Chunk.source_id, Chunk.chunk_index)
        )
    ).all()
    return to_markdown([(chunk, source) for chunk, source in rows])


def _raw_is_served() -> bool:
    """Whether this deployment serves stored raw files (`P6-14`).

    Off unless asked. §12.5 wants page-accurate links for the operator reading
    their own corpus, and the raw store holds copies of third-party material —
    serving it is redistribution, which is a decision rather than a default.
    `P3-10`'s `grants.raw_files` is the finer-grained version for guests; this is
    the deployment-level switch beneath it.
    """
    return os.environ.get("MERIDIAN_SERVE_RAW", "").strip().lower() in {"1", "true", "yes"}


@router.get("/sources/{source_id}/figures", response_model=SourceFiguresRead)
async def explore_source_figures(source_id: int, sess: ReadSession) -> SourceFiguresRead:
    """Figures extracted from one source (`P6-14`, §6.6).

    Captions and alt text, which is what ingestion extracts — §6.6's "start with
    captions, not vision". `vlm_description` stays empty until somebody spends
    against `P7-07`.
    """
    source = await sess.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id}.")

    rows = (
        await sess.execute(
            select(Figure).where(Figure.source_id == source_id).order_by(Figure.figure_id)
        )
    ).scalars()

    served = _raw_is_served() and source.raw_file_path is not None
    figures = []
    for figure in rows:
        raw_url = None
        if served:
            # `#page=N` is what a PDF viewer reads, and it is the whole of
            # "page-accurate" — the alternative is a link to page one and a
            # reader scrolling for the figure the caption promised.
            fragment = f"#page={figure.page}" if figure.page else ""
            raw_url = f"/api/explore/sources/{source_id}/raw{fragment}"
        figures.append(
            FigureRefRead(
                figure_id=figure.figure_id,
                source_id=figure.source_id,
                caption=figure.caption,
                alt_text=figure.alt_text,
                image_url=figure.image_url,
                page=figure.page,
                source_title=source.title,
                source_url=source.url,
                raw_url=raw_url,
            )
        )

    return SourceFiguresRead(source_id=source_id, figures=figures, raw_available=_raw_is_served())


@router.get("/sources/{source_id}/raw")
async def explore_source_raw(source_id: int, sess: ReadSession) -> FileResponse:
    """The stored raw file for one source (`P6-14`, §5.4).

    **The path comes from the database, never from the request.** The caller
    supplies an integer; `raw_file_path` is read off the row. That is not a
    hardened traversal check, it is the absence of anything to traverse — there
    is no user-supplied path in this handler at all, which is a stronger
    property than validating one would be.

    Off unless `MERIDIAN_SERVE_RAW` says otherwise. The raw store holds copies of
    third-party material kept as a research archive (§14.2), and serving it is a
    different act from serving what was extracted from it.
    """
    if not _raw_is_served():
        raise HTTPException(
            status_code=404,
            detail="This deployment does not serve raw files. Set MERIDIAN_SERVE_RAW to enable.",
        )

    source = await sess.get(Source, source_id)
    if source is None or not source.raw_file_path:
        raise HTTPException(status_code=404, detail=f"No stored file for source {source_id}.")

    root = Path(os.environ.get("MERIDIAN_RAW_ROOT", "/data/raw")).resolve()
    target = (root / source.raw_file_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        # Belt to the braces above: a `raw_file_path` written before `P1-11`'s
        # containment check, or a corpus restored from elsewhere, must not be
        # able to reach outside the store either.
        raise HTTPException(status_code=404, detail=f"No stored file for source {source_id}.")

    return FileResponse(target, media_type=(source.extra or {}).get("media_type"))


@router.get("/notifications", response_model=NotificationsRead)
async def explore_notifications(
    sess: ReadSession,
    notification_type: Annotated[
        list[str] | None, Query(description="Filter by type. Repeat the key.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationsRead:
    """What happened while nobody was looking (`P6-08`, §12.5, §13.3).

    The in-app counterpart to the Telegram digest, reading the same rows — an
    alert is recorded before it is delivered (`P5-07`), so a deployment with no
    bot token still has somewhere to see what would have been sent.

    Filterable by type rather than by read state, per the model's own reasoning:
    the useful question is "what finished" or "what needs a decision", not "what
    have I glanced at".
    """
    query = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if notification_type:
        query = query.where(Notification.notification_type.in_(notification_type))

    rows = list((await sess.execute(query)).scalars())

    # Counted across *all* types, not just the filtered ones. A panel showing
    # "alerts (0)" while three seed proposals wait is the filter hiding the
    # thing the reader came for.
    counts = dict(
        (
            await sess.execute(
                select(Notification.notification_type, func.count()).group_by(
                    Notification.notification_type
                )
            )
        ).all()
    )

    return NotificationsRead(
        notifications=[NotificationRead.model_validate(row) for row in rows],
        counts_by_type=counts,
        unread=sum(1 for row in rows if row.read_at is None),
    )


@router.get("/nodes/{entity_id}", response_model=NodeDetailRead)
async def explore_node(entity_id: int, sess: ReadSession) -> NodeDetailRead:
    """One node, with what is claimed about it and what justified each claim.

    §12.5's node detail panel, assembled in one request: description, attribute
    tags with confidence, the supporting chunks with their source and tier, and
    how many of this node's edges §9 marked contested.

    **Superseded chunks are still shown here**, and that is the one place in the
    read surface where they are. Everywhere else a superseded chunk is text the
    page no longer carries and serving it would misquote a document (`P1-32`).
    Here it is the evidence an attribute was actually derived from, and §2.4
    re-derives from source chunks — so a tag whose chunk has since been replaced
    must still be followable, or the tag becomes an assertion with a citation
    that resolves to nothing.
    """
    entity = await sess.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"No entity {entity_id}.")

    rows = (
        await sess.execute(
            select(AttributeValue, AttributeDefinition)
            .join(
                AttributeDefinition,
                AttributeDefinition.attribute_id == AttributeValue.attribute_id,
            )
            .where(AttributeValue.entity_id == entity_id)
        )
    ).all()

    attributes = [
        NodeAttributeRead(
            value_id=value.value_id,
            name=definition.name,
            scope=definition.scope,
            topic=definition.topic,
            value=value.value,
            value_numeric=value.value_numeric,
            confidence=value.confidence,
            quality_tier=value.quality_tier,
            supporting_chunk_ids=list(value.supporting_chunk_ids or ()),
        )
        for value, definition in rows
    ]
    # Confidence first, then name. Insertion order would put whatever was tagged
    # first at the top, which is a fact about the crawl and not about the node.
    attributes.sort(key=lambda a: (-(a.confidence or 0.0), a.name))

    cited = sorted({chunk_id for a in attributes for chunk_id in a.supporting_chunk_ids})
    supporting = await _hydrate_chunks(sess, cited[:MAX_SUPPORTING_CHUNKS])

    contested = await sess.scalar(
        select(func.count())
        .select_from(Edge)
        .where(
            or_(Edge.from_node == entity_id, Edge.to_node == entity_id),
            Edge.contested_with.is_not(None),
        )
    )

    mine = await annotations.listing(sess, about=entity_id, limit=MAX_PANEL_ANNOTATIONS)

    return NodeDetailRead(
        entity=EntityRead.model_validate(entity),
        attributes=attributes,
        supporting=supporting,
        contested_edges=int(contested or 0),
        annotations=mine.annotations,
    )


async def _hydrate_chunks(sess, chunk_ids: list[int]) -> list[SearchHitRead]:
    """Chunks with the source fields that make them citable.

    The same shape a search hit has, deliberately: a reader following evidence
    from a node and a reader following it from a result list are doing the same
    thing, and two shapes for it would mean two renderers that can drift.
    """
    if not chunk_ids:
        return []

    rows = (
        await sess.execute(
            select(Chunk, Source)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.chunk_id.in_(chunk_ids))
            .order_by(Chunk.source_id, Chunk.chunk_index)
        )
    ).all()

    return [
        SearchHitRead(
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            text=chunk.text,
            page_or_offset=chunk.page_or_offset,
            chunk_index=chunk.chunk_index,
            url=source.url,
            title=source.title,
            source_tier=source.source_tier,
            publication_date=source.publication_date,
            language=source.language,
            topic_labels=source.topic_labels,
            page_unit=page_unit_for((source.extra or {}).get("media_type")),
            media_type=(source.extra or {}).get("media_type"),
            duplicate_of=chunk.duplicate_of,
            # Not a ranked result: nothing scored these, and a score of 0 beside
            # a search hit's 0.016 would read as a very bad match rather than as
            # a different kind of thing.
            score=0.0,
            lexical_rank=None,
            vector_rank=None,
        )
        for chunk, source in rows
    ]


@router.get("/views", response_model=SavedViewsRead)
async def list_views(sess: ReadSession) -> SavedViewsRead:
    """Saved views, most recently opened first (task P6-09, §12.5).

    A **read** under `/api/explore`, while every write to them is under
    `/api/admin` — which looks inconsistent for something a reader creates while
    reading, and is not. §12.6 splits the prefixes by *mutation*, and the
    consequence here is the one that matters: saved views are shared state with
    no per-viewer scoping, so on an instance shared with somebody else
    (`P3-06`'s grants) a guest must be able to open the owner's views and must
    not be able to add to them.

    A view never opened sorts last rather than being hidden. Somebody saved it
    and did not come back; disappearing it would be the system deciding that was
    a mistake.
    """
    rows = await sess.scalars(
        select(SavedView).order_by(
            SavedView.last_opened_at.desc().nullslast(), SavedView.created_at.desc()
        )
    )
    return SavedViewsRead(views=[SavedViewRead.model_validate(row) for row in rows])


# ---------------------------------------------------------------------------
# Annotations (task P6-05, spec §12.5)
# ---------------------------------------------------------------------------
#
# Reads only. Every write is `/api/admin/annotations`, so a shared instance
# (`P3-06`) can show the owner's notes without offering a way to add to them —
# and the read-only role is what enforces that rather than this comment.


@router.get("/annotations", response_model=AnnotationsRead)
async def list_annotations(
    sess: ReadSession,
    about: Annotated[
        int | None, Query(description="Narrow to the notes attached to one node.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnnotationsRead:
    """The reader's own notes, most recently written first (§12.5).

    `total` is how many match, not how many came back. A reader who has written
    four hundred notes is owed the number, and a page that only ever reports its
    own length cannot tell them.
    """
    return await annotations.listing(sess, about=about, limit=limit, offset=offset)


@router.get("/export/annotations", response_class=PlainTextResponse)
async def explore_export_annotations(
    sess: ReadSession,
    about: Annotated[int | None, Query(description="Narrow to one node.")] = None,
) -> str:
    """Notes as Markdown (`P6-15`, §12.5: "avoid trapping material in a bespoke store").

    The export that matters most of the four, because this is the only material
    in the corpus that is not recoverable by crawling again. Everything else
    here can be re-fetched from the web; a note cannot be re-derived from
    anything.
    """
    mine = await annotations.listing(sess, about=about, limit=MAX_EXPORT_ANNOTATIONS)
    return annotations.to_markdown(mine.annotations)


@router.get("/progress", response_model=CrawlProgressRead)
async def explore_progress(sess: ReadSession) -> CrawlProgressRead:
    """What the crawl is doing right now (task `B-09`, scaffold §1.7).

    Production starts empty by design, so for the first hour there is nothing
    to search and a landing page has to say something true anyway. The decision
    this takes: **make the first hour legible rather than ship a demo corpus.**

    Shipping one was the alternative, and it is worse twice over. A snapshot of
    a real crawl is third-party content, and whether it may be redistributed is
    the question §14.2 keeps carefully separate — the same reasoning that keeps
    `MERIDIAN_SERVE_RAW` off by default. Synthetic fixtures are worse still and
    the task rules them out: they do not resemble real extraction output, so
    the first impression would be of a system that works better than it does.

    A queue draining is a system working. These are the numbers that make that
    visible, and every one of them is true.
    """
    now = dt.datetime.now(dt.UTC)
    hour_ago = now - dt.timedelta(hours=1)

    queue = await queue_depth(sess)

    # Distinct domains, newest first. `max(attempted_at)` per domain rather
    # than the raw rows: a crawl hammering one slow site would otherwise fill
    # the list with a single name and hide that anything else is happening.
    recent = (
        await sess.execute(
            select(FetchAttempt.domain, func.max(FetchAttempt.attempted_at).label("last"))
            .group_by(FetchAttempt.domain)
            .order_by(sql_desc("last"))
            .limit(8)
        )
    ).all()

    attempts = int(
        await sess.scalar(
            select(func.count())
            .select_from(FetchAttempt)
            .where(FetchAttempt.attempted_at >= hour_ago)
        )
        or 0
    )
    successes = int(
        await sess.scalar(
            select(func.count())
            .select_from(FetchAttempt)
            .where(FetchAttempt.attempted_at >= hour_ago, FetchAttempt.outcome == "success")
        )
        or 0
    )

    return CrawlProgressRead(
        as_of=now,
        queue=dict(queue),
        recent_domains=[domain for domain, _ in recent],
        attempts_last_hour=attempts,
        successes_last_hour=successes,
    )
