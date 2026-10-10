"""`/api/explore/*` — the read surface (task P2-07, spec §12.5, §12.6).

Every route reads through `meridian_ro`; `deps` offers nothing writable here. A hit
carries its source inline, and there is a route for the chunks around a hit.
See docs/features/api-and-access.md#explore-routes.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import desc as sql_desc
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core import annotations, chat, watch
from meridian_core.answer import DEFAULT_ANSWER_CANDIDATES, DEFAULT_TOP, MAX_TOP
from meridian_core.areaview import AreaNotFound, area_detail, areas_level, jump
from meridian_core.bridgeview import bridge
from meridian_core.corpusmap import DEFAULT_SAMPLE, MAX_SAMPLE, corpus_map
from meridian_core.crawlhealth import crawl_health
from meridian_core.crawlhealth import liveness as crawl_liveness
from meridian_core.db import session_ro
from meridian_core.export import to_bibtex, to_markdown
from meridian_core.figures import is_furniture, reader_caption
from meridian_core.mapsteer import area_steering
from meridian_core.models import (
    AttributeDefinition,
    AttributeValue,
    ChatThread,
    Chunk,
    Edge,
    Entity,
    FetchAttempt,
    Figure,
    Notification,
    SavedView,
    Source,
    SteeringProposal,
)
from meridian_core.passagetopics import passage_topics_for
from meridian_core.provider import answerable
from meridian_core.queueing import queue_depth
from meridian_core.schemas.annotations import AnnotationsRead
from meridian_core.schemas.answer import AnswerRead
from meridian_core.schemas.areas import (
    AreaDetailRead,
    AreaJumpRead,
    AreasRead,
    AreaSteeringRead,
    BridgeRead,
)
from meridian_core.schemas.chat import (
    ChatMessageRead,
    ChatStatusRead,
    ChatThreadDetailRead,
    ChatThreadRead,
    ChatThreadsRead,
)
from meridian_core.schemas.corpusmap import CorpusMapRead
from meridian_core.schemas.enums import SourceTier
from meridian_core.schemas.graph import EntityRead
from meridian_core.schemas.runs import NotificationRead
from meridian_core.schemas.search import (
    CorpusStatsRead,
    CrawlHealthRead,
    CrawlProgressRead,
    FigureRefRead,
    LivenessRead,
    NodeAttributeRead,
    NodeDetailRead,
    NotificationsRead,
    SearchHitRead,
    SearchResponse,
    SourceChunksRead,
    SourceFiguresRead,
    TopicOverlapsRead,
)
from meridian_core.schemas.settings import DisplaySettingsRead
from meridian_core.schemas.source import ChunkRead, SourcePageRead
from meridian_core.schemas.views import SavedViewRead, SavedViewsRead
from meridian_core.search import DEFAULT_CANDIDATES, SearchFilters, page_unit_for
from meridian_core.stats import corpus_stats
from meridian_core.timefmt import display_zone, zone_label
from meridian_core.topicoverlaps import topic_overlaps

from ..cache import KeptByKey
from ..deps import ReadSession
from ..search_service import WindowTooDeep, answer_search, paged_search

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
#: Passages shown above a linked one, so it opens in its context rather than at the top.
CONTEXT_BEFORE = 3

#: How many supporting chunks one node panel carries. An entity with a dozen
#: attributes can cite a hundred chunks, and a panel that returned all of them
#: would be a page of prose where §12.5 asked for evidence a reader can follow.
MAX_SUPPORTING_CHUNKS = 40

#: How many of the reader's own notes the node panel carries: the latest few, below
#: the chunk cap; the notes list shows the rest.
MAX_PANEL_ANNOTATIONS = 10

#: How many notes one export may carry: a page cap, so all of them stay reachable.
MAX_EXPORT_ANNOTATIONS = 500


# `Annotated[...]` rather than `= Query(...)`, so a handler stays callable from a test.
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
    topic_match: Annotated[
        Literal["any", "all"],
        Query(
            description="`all` keeps only passages whose source and own labels, together, "
            "carry every `topic` named: where topics meet."
        ),
    ] = "any",
    place: Annotated[
        list[str] | None,
        Query(
            description="Repeat to allow several; a source about any of them is kept. Codes as "
            "`sources.places` stores them: ISO 3166-1 alpha-2 for a country, UN/LOCODE without "
            "its space for a city. Sources never examined for places are excluded.",
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

    An empty `q` returns an empty result rather than a 422. Check `degraded`: without an
    embedding service, the vector arm does not run and results match on words only.
    See docs/features/search.md.
    """
    filters = SearchFilters(
        source_tiers=source_tier,
        languages=language,
        topics=topic,
        topics_all=topic_match == "all",
        places=place,
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


@router.get("/answer", response_model=AnswerRead)
async def explore_answer(
    sess: ReadSession,
    q: Annotated[str, Query(description="The question. Empty returns no groups.")] = "",
    topic: Annotated[list[str] | None, Query(description="As on `/search`.")] = None,
    topic_match: Annotated[Literal["any", "all"], Query()] = "any",
    place: Annotated[list[str] | None, Query(description="As on `/search`.")] = None,
    source_tier: Annotated[list[SourceTier] | None, Query()] = None,
    language: Annotated[list[str] | None, Query()] = None,
    published_after: Annotated[dt.date | None, Query(description="As on `/search`.")] = None,
    published_before: Annotated[dt.date | None, Query(description="As on `/search`.")] = None,
    candidates: Annotated[
        int,
        Query(ge=1, le=1000, description="How deep the one search goes before grouping."),
    ] = DEFAULT_ANSWER_CANDIDATES,
    top: Annotated[int, Query(ge=1, le=MAX_TOP, description="Sources shown per place.")] = (
        DEFAULT_TOP
    ),
) -> AnswerRead:
    """A question answered as evidence grouped by country, with coverage per country.

    The same search `/search` runs, over the whole candidate pool rather than a
    page, grouped by the places its sources are about. Nothing is generated:
    each item is a source's best-matching passage. See `meridian_core.answer`
    for the grouping and the coverage rule.
    """
    filters = SearchFilters(
        source_tiers=source_tier,
        languages=language,
        topics=topic,
        topics_all=topic_match == "all",
        places=place,
        published_after=published_after,
        published_before=published_before,
    )
    return await answer_search(sess, q, filters=filters, candidates=candidates, top=top)


@router.get("/topic-overlaps", response_model=TopicOverlapsRead)
async def explore_topic_overlaps(sess: ReadSession) -> TopicOverlapsRead:
    """Labelled sources by exact topic combination (`B-72`): the web of topics.

    A client sums the sets containing a selection to show how many sources lie
    in all of it, then searches with ``topic_match=all``.
    """
    return await topic_overlaps(sess)


@router.get("/settings", response_model=DisplaySettingsRead)
async def explore_settings(sess: ReadSession) -> DisplaySettingsRead:
    """Deployment-wide display settings: the zone times are shown in (`B-145`, ADR 0009)."""
    zone = await display_zone(sess)
    return DisplaySettingsRead(display_timezone=zone, label=zone_label(zone))


@router.get("/stats", response_model=CorpusStatsRead)
async def explore_stats(
    sess: ReadSession,
    since: Annotated[
        dt.datetime | None,
        Query(description="Count what arrived after this instant (P6-11). ISO 8601."),
    ] = None,
) -> CorpusStatsRead:
    """What the corpus holds.

    Behind the Explore landing state's counts. `searchable_chunks` is deliberately separate from
    `chunks`: §12.5 turns on a reader being able to tell "we never collected this" from "we
    collected it and filtered it", and one number for both erases exactly that.
    """
    return CorpusStatsRead.model_validate(await corpus_stats(sess, since=since))


#: Seconds a projection is served before a background refresh (`B-156`).
MAP_TTL_S = 600.0

KEPT_MAP: KeptByKey[tuple[int, tuple[str, ...], tuple[str, ...]], CorpusMapRead] = KeptByKey(
    MAP_TTL_S
)


async def compute_map(
    sample: int, topics: tuple[str, ...], places: tuple[str, ...]
) -> CorpusMapRead:
    """One projection, on its own session: a background refresh outlives the request."""
    async with session_ro() as sess:
        return CorpusMapRead.model_validate(
            await corpus_map(sess, sample=sample, topics=list(topics), places=list(places))
        )


@router.get("/map", response_model=CorpusMapRead)
async def explore_map(
    sample: Annotated[int, Query(ge=1, le=MAX_SAMPLE)] = DEFAULT_SAMPLE,
    topic: Annotated[list[str] | None, Query()] = None,
    place: Annotated[list[str] | None, Query()] = None,
) -> CorpusMapRead:
    """The embedding space, projected to three dimensions (`P6-26`, `P6-29`).

    Kept per sample and filter for ten minutes and refreshed behind the reader (`B-156`):
    on a real corpus a projection takes over a second, and the corpus moves over hours.
    `as_of` says when it was computed. See docs/features/map.md#the-3d-projection.
    """
    topics = tuple(sorted(set(topic or ())))
    places = tuple(sorted(set(place or ())))
    return await KEPT_MAP.get((sample, topics, places), lambda: compute_map(sample, topics, places))


@router.get("/areas", response_model=AreasRead)
async def explore_areas(
    sess: ReadSession, parent: Annotated[int | None, Query(ge=1)] = None
) -> AreasRead:
    """One level of the map (`P6-30`): the regions, or the children of ``parent``.

    From the newest build of `worker.areas`; an empty build list is an answer
    (``build`` is null), not an error, so the map can say what it waits for.
    """
    try:
        return await areas_level(sess, parent_id=parent)
    except AreaNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/areas/jump", response_model=AreaJumpRead)
async def explore_area_jump(
    sess: ReadSession, q: Annotated[str, Query(min_length=1, max_length=200)]
) -> AreaJumpRead:
    """Areas named by a term, then sub-areas whose passages mention it."""
    return await jump(sess, q)


@router.get("/areas/{area_id}", response_model=AreaDetailRead)
async def explore_area(area_id: int, sess: ReadSession) -> AreaDetailRead:
    """One area: its stats, where it sits, and its most typical passages."""
    try:
        return await area_detail(sess, area_id)
    except AreaNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/areas/{area_id}/steering", response_model=AreaSteeringRead)
async def explore_area_steering(area_id: int, sess: ReadSession) -> AreaSteeringRead:
    """Which topic steering this area would move (`P6-35`), read before acting.

    Lets the map's menu say what "more" and "less" will do, or why "less" cannot do anything
    for an area about no topic.
    """
    try:
        return await area_steering(sess, area_id)
    except AreaNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bridges/{area_a}/{area_b}", response_model=BridgeRead)
async def explore_bridge(area_a: int, area_b: int, sess: ReadSession) -> BridgeRead:
    """What connects two areas (`P6-31`).

    Cited claims, similar passages and shared terms, as three separate lists. Nothing recorded
    is an empty answer.
    """
    try:
        return await bridge(sess, area_a, area_b)
    except AreaNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sources/{source_id}", response_model=SourcePageRead)
async def explore_source(source_id: int, sess: ReadSession) -> SourcePageRead:
    """One source, with everything recorded about how it was acquired.

    Including `extractor` (`P1-44`) and the OCR columns.
    """
    source = await sess.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"no source {source_id}")
    read = SourcePageRead.model_validate(source)
    read.page_unit = page_unit_for((source.extra or {}).get("media_type"))
    return read


@router.get("/sources/{source_id}/chunks", response_model=SourceChunksRead)
async def explore_source_chunks(
    source_id: int,
    sess: ReadSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    around: Annotated[
        int | None,
        Query(
            description="A chunk id: start the window a few passages before it, so a hit opens "
            "in its context. Replaces `offset`; ignored when it is not a live chunk of this source."
        ),
    ] = None,
) -> SourceChunksRead:
    """A source's chunks in document order — the context around a hit.

    Ordered by `chunk_index`. A source with no chunks returns an empty list, not a 404.
    `offset` in the response is where the window starts, which `around` decides (`B-178`).
    """
    if await sess.get(Source, source_id) is None:
        raise HTTPException(status_code=404, detail=f"no source {source_id}")

    if around is not None:
        target = await sess.scalar(
            select(Chunk.chunk_index).where(
                Chunk.chunk_id == around,
                Chunk.source_id == source_id,
                Chunk.superseded_at.is_(None),
            )
        )
        if target is not None:
            before = await sess.scalar(
                select(func.count()).where(
                    Chunk.source_id == source_id,
                    Chunk.superseded_at.is_(None),
                    Chunk.chunk_index < target,
                )
            )
            offset = max(0, int(before or 0) - CONTEXT_BEFORE)

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

    `duplicate_of` and `nearest_similarity` tell a filtered near-duplicate from a page
    never crawled (§12.5).
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

    Explicit ids, not a query; plain text, as a `.bib` file is.
    See docs/features/api-and-access.md#explore-routes.
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

    Off unless `MERIDIAN_SERVE_RAW` is set; `grants.raw_files` is the per-guest switch
    beneath it.
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
    furniture = 0
    for figure in rows:
        # `B-156`: a page's logos, icons and controls are stored as figures; a reader is not
        # shown them. See docs/features/web-app.md#figures-furniture.
        if is_furniture(figure.image_url, figure.caption, figure.alt_text):
            furniture += 1
            continue
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
                reader_caption=reader_caption(figure.caption, figure.alt_text, figure.image_url),
            )
        )

    return SourceFiguresRead(
        source_id=source_id,
        figures=figures,
        raw_available=_raw_is_served(),
        furniture_hidden=furniture,
    )


@router.get("/sources/{source_id}/raw")
async def explore_source_raw(source_id: int, sess: ReadSession) -> FileResponse:
    """The stored raw file for one source (`P6-14`, §5.4).

    The path comes from the row, never the request. Off unless `MERIDIAN_SERVE_RAW`.
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


async def _settled(sess: AsyncSession, rows: list[Notification]) -> dict[int, str]:
    """What became of what each notification asks (`B-182`), by notification id.

    The body is written when the notification is, so a superseded proposal still read "applies
    by itself unless rejected". Read now: a proposal no longer pending, a duplicate merged.
    """
    proposals: dict[int, int] = {}
    created: dict[int, int] = {}
    for row in rows:
        payload = row.payload or {}
        if row.notification_type == "steering_proposal" and isinstance(
            payload.get("proposal_id"), int
        ):
            proposals[row.notification_id] = payload["proposal_id"]
        elif row.notification_type == "merge_adjudication" and isinstance(
            payload.get("created"), int
        ):
            created[row.notification_id] = payload["created"]
    out: dict[int, str] = {}
    if proposals:
        status = dict(
            (
                await sess.execute(
                    select(SteeringProposal.proposal_id, SteeringProposal.status).where(
                        SteeringProposal.proposal_id.in_(set(proposals.values()))
                    )
                )
            ).all()
        )
        for nid, pid in proposals.items():
            if status.get(pid) not in (None, "pending"):
                out[nid] = status[pid]
    if created:
        merged = set(
            (
                await sess.execute(
                    select(Entity.entity_id).where(
                        Entity.entity_id.in_(set(created.values())),
                        Entity.redirects_to.is_not(None),
                    )
                )
            ).scalars()
        )
        for nid, eid in created.items():
            if eid in merged:
                out[nid] = "merged"
    return out


@router.get("/notifications", response_model=NotificationsRead)
async def explore_notifications(
    sess: ReadSession,
    notification_type: Annotated[
        list[str] | None, Query(description="Filter by type. Repeat the key.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationsRead:
    """What happened while nobody was looking (`P6-08`, §12.5, §13.3).

    The same rows as the Telegram digest (`P5-07`), filterable by type.
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

    settled = await _settled(sess, rows)
    return NotificationsRead(
        notifications=[
            NotificationRead.model_validate(row).model_copy(
                update={"settled": settled.get(row.notification_id)}
            )
            for row in rows
        ],
        counts_by_type=counts,
        unread=sum(1 for row in rows if row.read_at is None),
    )


@router.get("/nodes/{entity_id}", response_model=NodeDetailRead)
async def explore_node(entity_id: int, sess: ReadSession) -> NodeDetailRead:
    """One node, with what is claimed about it and what justified each claim.

    §12.5's node panel in one request: description, tags with confidence, supporting
    chunks (superseded ones included, uniquely here) and the contested edge count.
    See docs/features/api-and-access.md#explore-routes.
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
    passages = await passage_topics_for(sess, chunk_ids)

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
            passage_topics=passages.get(chunk.chunk_id),
            places=source.places,
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

    Writes are under `/api/admin`. A view never opened sorts last rather than being
    hidden.
    """
    rows = await sess.scalars(
        select(SavedView).order_by(
            SavedView.last_opened_at.desc().nullslast(), SavedView.created_at.desc()
        )
    )
    views = []
    for row in rows:
        view = SavedViewRead.model_validate(row)
        # A question to watch (`P6-43`): what is new for it since it was last opened.
        view.new_since = await watch.new_for_view(sess, row)
        views.append(view)
    return SavedViewsRead(views=views)


# ---------------------------------------------------------------------------
# Annotations (task P6-05, spec §12.5)
# ---------------------------------------------------------------------------
#
# Reads only; every write is `/api/admin/annotations`.


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

    The one export of material crawling cannot recover.
    """
    mine = await annotations.listing(sess, about=about, limit=MAX_EXPORT_ANNOTATIONS)
    return annotations.to_markdown(mine.annotations)


@router.get("/progress", response_model=CrawlProgressRead)
async def explore_progress(sess: ReadSession) -> CrawlProgressRead:
    """What the crawl is doing right now (task `B-09`, scaffold §1.7).

    Makes an empty instance's first hour legible instead of shipping a demo corpus.
    See docs/features/api-and-access.md#explore-routes.
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
        liveness=LivenessRead.model_validate(await crawl_liveness(sess, now=now)),
    )


@router.get("/crawl-health", response_model=CrawlHealthRead)
async def explore_crawl_health(sess: ReadSession) -> CrawlHealthRead:
    """A day of fetching and a verdict on whether it has stopped (task `P6-25`).

    Behind Admin's crawl-health panel, but a read, so here rather than behind the admin
    gate. See docs/features/api-and-access.md#explore-routes.
    """
    return CrawlHealthRead.model_validate(await crawl_health(sess))


# ---------------------------------------------------------------------------
# Ask the graph: the conversations (tasks P6-06, P6-07)
# ---------------------------------------------------------------------------


@router.get("/chat/status", response_model=ChatStatusRead)
async def chat_status(sess: ReadSession) -> ChatStatusRead:
    """Whether any agent could be asked a question (`B-183`); nothing is called.

    So the panel can say no model is set up before a question is written, not after.
    """
    return ChatStatusRead(available=await answerable(sess, chat.TASK_TYPE))


@router.get("/chat/threads", response_model=ChatThreadsRead)
async def list_chat_threads(
    sess: ReadSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> ChatThreadsRead:
    """Earlier questions, most recent first — the panel's history (`P6-07`)."""
    rows, total = await chat.recent_threads(sess, limit=limit)
    return ChatThreadsRead(threads=[ChatThreadRead.model_validate(r) for r in rows], total=total)


@router.get("/chat/threads/{thread_id}", response_model=ChatThreadDetailRead)
async def read_chat_thread(thread_id: int, sess: ReadSession) -> ChatThreadDetailRead:
    messages = await chat.thread_messages(sess, thread_id)
    if messages is None:
        raise HTTPException(status_code=404, detail=f"No conversation {thread_id}.")
    thread = await sess.get(ChatThread, thread_id)
    return ChatThreadDetailRead(
        thread=ChatThreadRead.model_validate(thread),
        messages=[ChatMessageRead.model_validate(m) for m in messages],
    )
