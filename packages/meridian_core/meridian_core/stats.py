"""Corpus counts for the Explore landing state (task P2-07, spec §12.5, §8 design).

The design system's Explore default state carries "four counts in the mono
numeral style — documents, nodes, edges, contested†", and §12.5 makes the three
entry points search, coverage and contested. These are the numbers behind that
screen.

Here rather than in the API because `meridian_core` owns anything that touches
the database (AGENTS.md layout), and because the same counts are what §12.5's
daily health line wants — a surface and a log line asking the same question
should not be two queries that can disagree.

**Counted, not estimated.** `count(*)` over a corpus this size is milliseconds,
and a landing page that rounded or cached would make "nothing has been crawled
since Tuesday" indistinguishable from "the count is stale". If the corpus ever
grows past the point where these are cheap, the fix is a materialised view with
a refresh time *displayed beside it*, not a silent approximation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chunk, Edge, Entity, Source


@dataclasses.dataclass(frozen=True)
class CorpusStats:
    """What the corpus holds, at one moment.

    ``searchable`` is not ``chunks``. A chunk is searchable lexically as soon as
    it exists, but the vector half needs an embedding and the novelty gate may
    have marked it a duplicate — and §12.5's point is that a reader must be able
    to tell "we have not collected this" from "we collected it and filtered it".
    Reporting one number for both would erase exactly that distinction.
    """

    #: When these numbers were counted (`P2-18`).
    as_of: dt.datetime

    sources: int
    chunks: int
    embedded_chunks: int
    duplicate_chunks: int
    entities: int
    edges: int
    contested_edges: int

    #: Counted only when a caller asked "what is new since X" (`P6-11`). None
    #: means nobody asked — deliberately not 0, which is the answer to a
    #: question about a moment when nothing had changed, and a returning reader
    #: shown "0 new" would believe that.
    new_sources: int | None = None
    new_chunks: int | None = None

    @property
    def searchable_chunks(self) -> int:
        """Chunks a default search can return: everything not marked duplicate."""
        return self.chunks - self.duplicate_chunks


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
            select(func.count()).select_from(Source).where(Source.created_at > since)
        )
        new_chunks = await count(
            select(func.count()).select_from(Chunk).where(Chunk.created_at > since)
        )

    return CorpusStats(
        # `P2-18`: a client cannot otherwise tell a cached count from a fresh
        # one, and these are exactly the numbers someone quotes as "the corpus
        # has N documents" months later.
        as_of=dt.datetime.now(dt.UTC),
        new_sources=new_sources,
        new_chunks=new_chunks,
        sources=await count(select(func.count()).select_from(Source)),
        chunks=await count(select(func.count()).select_from(Chunk)),
        embedded_chunks=await count(
            select(func.count()).select_from(Chunk).where(Chunk.embedding.is_not(None))
        ),
        duplicate_chunks=await count(
            select(func.count()).select_from(Chunk).where(Chunk.duplicate_of.is_not(None))
        ),
        entities=await count(select(func.count()).select_from(Entity)),
        edges=await count(select(func.count()).select_from(Edge)),
        # §9: contradiction is a result, not an error, and contested nodes are
        # the highest-value ones in the graph — which is why this is a headline
        # count rather than something you filter your way to.
        contested_edges=await count(
            select(func.count()).select_from(Edge).where(Edge.contested_with.is_not(None))
        ),
    )
