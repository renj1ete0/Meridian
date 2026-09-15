"""DTOs for the retrieval boundary (task P2-07; mirrors ``meridian_core.search``).

`search.py` returns frozen dataclasses because it is a library and its callers
are Python. These are the same shapes at an HTTP boundary, and they live here
rather than in `services/api` for the reason every other DTO does: services
import schemas, they do not define them (AGENTS.md layout). The orchestrator and
the MCP read tools (`P3-02`) will want the same shape, and two definitions of it
would drift.

``tests/unit/test_api_schemas.py`` compares these against the dataclasses field
by field, so a field added to a ``SearchHit`` and forgotten here fails rather
than being silently dropped on the way out.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict

from .enums import PageUnit, SearchArm, SourceTier
from .source import ChunkRead


class SearchHitRead(BaseModel):
    """One chunk, with the provenance that makes it citable.

    Mirrors ``meridian_core.search.SearchHit``. The source fields ride along
    rather than being an id the caller must resolve: §2 principle 3 is that
    nothing is assertable without a citation you can follow back to a file, and
    a hit that returned text plus a foreign key would make the citation
    optional in practice.
    """

    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    source_id: int
    text: str
    page_or_offset: int | None
    chunk_index: int

    url: str
    title: str | None
    source_tier: SourceTier
    publication_date: dt.date | None
    language: str | None

    #: What `page_or_offset` counts, and what it was derived from. §5.3's rule
    #: is "page for paginated documents, offset otherwise", and before `P2-18`
    #: a hit carried nothing that said which — so a consumer either re-derived
    #: it from a media type it did not have, or labelled every citation
    #: "page/offset". None means the media type was never recorded.
    page_unit: PageUnit | None = None
    media_type: str | None = None

    #: The novelty gate's verdict (§6.1). Present so a surface can say *why*
    #: something is missing — a filtered near-duplicate and a never-crawled page
    #: are otherwise indistinguishable, and only one is worth investigating.
    duplicate_of: int | None

    score: float
    #: 1-based position within each arm, None where that arm did not find it.
    #: "Found by both" and "found by one" are different qualities of hit and the
    #: fused score alone cannot tell them apart.
    lexical_rank: int | None
    vector_rank: int | None


class SearchResponse(BaseModel):
    """A page of hits, and an honest account of how they were found.

    ``arms`` and ``degraded`` are not diagnostics bolted on — they are the
    contract. Retrieval with no query vector is lexical-only, which is a
    legitimate mode, but a caller that believes it ran a hybrid search and ran
    half of one will draw the wrong conclusion about the corpus. §12.5 asks for
    hybrid search; a response that cannot say whether it delivered one is not
    answering the question.
    """

    hits: list[SearchHitRead]

    #: Which arms ran: ``lexical``, ``vector``, or both. Sorted for a stable
    #: payload — a set's iteration order is not, and a field that reshuffles
    #: between identical requests breaks response caching and diffing.
    arms: list[SearchArm]
    degraded: bool
    #: Why, when degraded. Empty when it is not.
    degraded_reason: str | None = None

    limit: int
    offset: int
    #: Whether another page exists. Determined by fetching one more hit than
    #: asked for, not by a second COUNT — the fused ranking has no cheap total,
    #: and a total computed a different way than the page would eventually
    #: disagree with it.
    has_more: bool

    #: How deep each arm went before fusion. Paging past this is not meaningful:
    #: RRF can only order what the arms handed it, so a hit beyond the pool was
    #: never a candidate. Exposed so a caller can tell "no more results" from
    #: "no more results *within the pool*".
    candidate_pool: int
    lexical_candidates: int
    vector_candidates: int


class CorpusStatsRead(BaseModel):
    """Mirrors ``meridian_core.stats.CorpusStats``, plus its derived count."""

    model_config = ConfigDict(from_attributes=True)

    #: When these numbers were counted (`P2-18`). A client cannot
    #: otherwise tell a cached count from a fresh one.
    as_of: dt.datetime
    sources: int
    chunks: int
    embedded_chunks: int
    duplicate_chunks: int
    searchable_chunks: int
    entities: int
    edges: int
    contested_edges: int


class SourceChunksRead(BaseModel):
    """A page of one source's chunks, in document order.

    The endpoint behind a search hit: having found a passage, a reader needs
    what surrounds it. ``chunk_index`` is document order, so paging here is a
    genuine offset rather than a ranking position — unlike search, where the
    ordering is a fused rank and an offset into it means something weaker.
    """

    source_id: int
    chunks: list[ChunkRead]
    limit: int
    offset: int
    has_more: bool
