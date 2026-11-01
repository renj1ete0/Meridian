"""Corpus counts for the Explore landing state (task P2-07, spec §12.5, §8 design).

Counted exactly, not estimated. Each named quantity has one definition here, which every page,
the digest and the assistant's overview count with (`B-240`). See
docs/features/search.md#corpus-counts.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, Edge, Entity, Source, TopicConfig
from .places import Place, comparison_set
from .search import SearchFilters, passage_conditions, readable_passage_conditions


@dataclasses.dataclass(frozen=True)
class CorpusStats:
    """What the corpus holds, at one moment.

    ``searchable_chunks`` is not ``chunks``: it leaves out copies and junk, so "not
    collected" and "collected and filtered" stay distinct (§12.5).
    """

    #: When these numbers were counted (`P2-18`).
    as_of: dt.datetime

    #: Every source row, junk and copies included: what the crawl has fetched. Named for that,
    #: so nobody quotes it as the corpus (`B-240`).
    fetched_sources: int
    #: The corpus's sources (:func:`kept_sources`): what every page calls "sources".
    #: See docs/features/search.md#corpus-counts.
    kept_sources: int
    #: Live passages, copies and junk included: what is stored.
    chunks: int
    #: Live passages with a vector, copies and junk included.
    embedded_chunks: int
    #: Live passages marked a copy of another passage.
    duplicate_chunks: int
    #: What Find can return with no filter (:func:`searchable_passages`): what every page calls
    #: "passages".
    searchable_chunks: int
    #: Searchable passages from screened-and-cleared sources: what an assistant's search
    #: reaches (`P4-14`), a subset of ``searchable_chunks``.
    readable_chunks: int
    entities: int
    edges: int
    contested_edges: int

    #: Counted only when a caller asked "what is new since X" (`P6-11`), in kept sources and
    #: searchable passages. None means nobody asked, deliberately not 0.
    new_sources: int | None = None
    new_chunks: int | None = None

    #: Every configured topic from `topic_config`, most-attended first (`P6-24`), not
    #: the labels present on sources. See docs/features/search.md#corpus-counts.
    topics: list[str] = dataclasses.field(default_factory=list)

    #: Kept sources nothing has examined for topics, `topic_labels IS NULL` (`P6-24`), which a
    #: topic filter excludes invisibly. An unindexed COUNT; the first to feel scale.
    sources_without_topics: int = 0

    #: The comparison set's places, for a place filter to offer (`P2-23`).
    #: From configuration — the government suffixes and the gazetteer's
    #: jurisdictions — not from a scan, for the reason `topics` is.
    places: list[Place] = dataclasses.field(default_factory=list)

    #: Kept sources nothing has examined for places — `places IS NULL` (`P2-23`).
    sources_without_places: int = 0


def kept_sources() -> tuple:
    """What counts as a source: not junk, and not a copy of another.

    The one definition of "sources" (`B-156`, `B-240`); search's defaults narrow to the same
    rows, which a test holds.
    """
    return (Source.retention_tier != "junk", Source.duplicate_of.is_(None))


def searchable_passages() -> list[ColumnElement[bool]]:
    """What counts as a passage: what Find can return with no filter set (`B-240`).

    Search's own predicate, so the count and the search cannot drift: live, not a copy, from a
    kept source. Needs ``chunks`` joined to ``sources``.
    """
    return passage_conditions(SearchFilters())


def live_entities() -> tuple:
    """What counts as a concept: not a note, and not merged into another node.

    The landing, the health line and Growth all count with it, so they agree.
    """
    return (Entity.node_type != "annotation", Entity.redirects_to.is_(None))


async def corpus_stats(sess: AsyncSession, *, since: dt.datetime | None = None) -> CorpusStats:
    """Count what is in the corpus. Reads only.

    ``since`` adds the delta a returning reader wants: §12.5 asks the landing
    state to carry it, because "what arrived while I was away" is the question
    somebody opens this with, and a total answers a different one.
    """

    async def count(stmt) -> int:
        return int(await sess.scalar(stmt) or 0)

    new_sources = new_chunks = None
    if since is not None:
        new_sources = await count(
            select(func.count())
            .select_from(Source)
            .where(Source.created_at > since, *kept_sources())
        )
        new_chunks = await count(
            select(func.count())
            .select_from(Chunk)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.created_at > since, *searchable_passages())
        )

    # One joined scan for both passage subsets, not two.
    searchable, readable = (
        await sess.execute(
            select(
                func.count().filter(and_(*searchable_passages())),
                func.count().filter(and_(*readable_passage_conditions())),
            )
            .select_from(Chunk)
            .join(Source, Source.source_id == Chunk.source_id)
            .where(Chunk.superseded_at.is_(None))
        )
    ).one()

    topics = list(
        await sess.scalars(
            select(TopicConfig.topic).order_by(TopicConfig.weight.desc(), TopicConfig.topic)
        )
    )

    return CorpusStats(
        # `P2-18`: a client cannot otherwise tell a cached count from a fresh
        # one, and these are exactly the numbers someone quotes as "the corpus
        # has N documents" months later.
        as_of=dt.datetime.now(dt.UTC),
        new_sources=new_sources,
        new_chunks=new_chunks,
        topics=topics,
        sources_without_topics=await count(
            select(func.count())
            .select_from(Source)
            .where(Source.topic_labels.is_(None), *kept_sources())
        ),
        places=await comparison_set(sess),
        sources_without_places=await count(
            select(func.count()).select_from(Source).where(Source.places.is_(None), *kept_sources())
        ),
        fetched_sources=await count(select(func.count()).select_from(Source)),
        kept_sources=await count(select(func.count()).select_from(Source).where(*kept_sources())),
        # Live chunks only (`P1-32`). "How much is in here" means the text on the
        # pages now; counting retired generations would make the corpus appear
        # to grow every time a page changed.
        chunks=await count(
            select(func.count()).select_from(Chunk).where(Chunk.superseded_at.is_(None))
        ),
        embedded_chunks=await count(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.embedding.is_not(None), Chunk.superseded_at.is_(None))
        ),
        duplicate_chunks=await count(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.duplicate_of.is_not(None), Chunk.superseded_at.is_(None))
        ),
        searchable_chunks=int(searchable or 0),
        readable_chunks=int(readable or 0),
        entities=await count(select(func.count()).select_from(Entity).where(*live_entities())),
        edges=await count(select(func.count()).select_from(Edge)),
        # §9: contradiction is a result, not an error, and contested nodes are
        # the highest-value ones in the graph — which is why this is a headline
        # count rather than something you filter your way to.
        contested_edges=await count(
            select(func.count()).select_from(Edge).where(Edge.contested_with.is_not(None))
        ),
    )
