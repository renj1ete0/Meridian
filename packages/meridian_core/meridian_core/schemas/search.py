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

from pydantic import BaseModel, ConfigDict, Field

from .annotations import AnnotationRead
from .enums import PageUnit, SearchArm, SourceTier
from .graph import EntityRead
from .runs import NotificationRead
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
    #: Which topics the source belongs to (`P2-14`). On the hit so a result list
    #: can show why a document is in a filtered set — a hit whose topic a reader
    #: cannot see is a filter they have to trust rather than check.
    topic_labels: list[str] | None = None

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

    #: The delta a returning reader asked for (`P6-11`). None means they did not
    #: ask; 0 means nothing arrived, and a landing page must not show the first
    #: as the second.
    new_sources: int | None = None
    new_chunks: int | None = None

    #: Every configured topic, most-attended first (`P6-24`) — what a filter
    #: control offers. From `topic_config`, so a topic with no sources yet is
    #: offered and filters to nothing, which is the true answer.
    topics: list[str] = Field(default_factory=list)

    #: Sources nothing has examined for topics (`P6-24`). What lets a topic
    #: filter tell a reader that narrowing may be hiding unexamined material.
    sources_without_topics: int = 0


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


class FigureRefRead(BaseModel):
    """One figure, with what makes it openable (task P6-14, spec §6.6, §12.5).

    `raw_url` is None unless this deployment serves raw files. §12.5 asks for
    "page-accurate links to raw files", and a link is only page-accurate if
    there is a file to point at — a caption with a dead link is worse than a
    caption alone, because a reader spends a click finding out.
    """

    model_config = ConfigDict(from_attributes=True)

    figure_id: int
    source_id: int
    caption: str | None
    alt_text: str | None
    image_url: str | None
    page: int | None

    #: The source's own title and url, so the panel needs no second request to
    #: say which document a figure came from.
    source_title: str | None = None
    source_url: str | None = None

    #: Deep link into the stored raw file, `#page=N` when the page is known.
    #: None when raw files are not served — see the class docstring.
    raw_url: str | None = None


class SourceFiguresRead(BaseModel):
    source_id: int
    figures: list[FigureRefRead]
    #: Whether this deployment serves raw files at all, so a client can explain
    #: an absent link rather than showing a broken one.
    raw_available: bool


class NotificationsRead(BaseModel):
    """The notifications panel's payload (task P6-08, spec §12.5)."""

    notifications: list[NotificationRead]
    #: Across every type, not only the filtered ones — a panel reading
    #: "alerts (0)" while three seed proposals wait is the filter hiding the
    #: thing the reader came for.
    counts_by_type: dict[str, int]
    unread: int


# ---------------------------------------------------------------------------
# The node detail panel (task P6-04, spec §12.5)
# ---------------------------------------------------------------------------


class NodeAttributeRead(BaseModel):
    """One attribute assigned to one entity, with what justified it.

    Flattened across `attribute_values` and `attribute_definitions`, because a
    panel showing `attribute_id: 7` would be asking the reader to resolve a
    foreign key. The definition's `name` and `scope` are what the tag says.
    """

    value_id: int
    name: str
    #: `global` or `topic_local` (§7.1). What groups the tags: a comparison
    #: dimension that applies everywhere and one that applies inside one topic
    #: mean different things, and mixing them in one row implies they do not.
    scope: str
    topic: str | None

    value: str | None
    value_numeric: float | None

    #: §7 makes confidence first-class, so it rides with the tag rather than
    #: behind a hover: a tag whose confidence a reader cannot see is a claim
    #: presented as a fact.
    confidence: float | None
    quality_tier: int | None
    supporting_chunk_ids: list[int]


class NodeDetailRead(BaseModel):
    """Everything §12.5 asks the node panel to show, in one request.

    One request rather than four, because every part of this panel is about the
    same node and a reader opening it wants all of it — and because four
    requests is four chances for a partly-rendered panel that looks like a node
    with no attributes.
    """

    entity: EntityRead

    #: Sorted by confidence, highest first, then by name. An attribute list in
    #: insertion order puts whatever was tagged first at the top, which is a
    #: fact about the crawl rather than about the entity.
    attributes: list[NodeAttributeRead]

    #: The chunks those attributes cite, hydrated with their source (§12.5:
    #: "supporting chunks with source and tier"). §2 principle 3 — a tag whose
    #: evidence cannot be followed is an assertion.
    supporting: list[SearchHitRead]

    #: Edges from or to this node that §9 marked contested. Counted rather than
    #: listed: the list needs the *other* node's name to be worth reading, and
    #: that is the canvas's job (`P6-02`).
    contested_edges: int

    #: §12.5's node panel ends with "own annotations", and this is the only part
    #: of the panel a person wrote themselves. Most recently written first, and
    #: capped — the panel shows the recent few, the notes list shows the rest.
    #:
    #: Defaulted rather than required, because a panel assembled before `P6-05`
    #: existed is still a valid panel, and making it required would turn a
    #: missing notes query into a 500 rather than an empty section.
    annotations: list[AnnotationRead] = Field(default_factory=list)


class CrawlProgressRead(BaseModel):
    """What the crawl is doing, for a corpus too small to search (task `B-09`).

    Production starts empty by design (scaffold §1.7), so a fresh install has
    nothing to look at for the first hour. The choice §12.3 implies and this
    takes: **make the first hour legible rather than shipping a demo corpus.**

    Shipping one was the alternative and it is worse on two counts. A snapshot
    of a real crawl is third-party content, and redistributing it is the
    question §14.2 keeps separate from everything else (`B-11` reaches the same
    conclusion about `MERIDIAN_SERVE_RAW`). Synthetic fixtures are worse still
    — the task rules them out directly, because they do not resemble real
    extraction output and the first impression would be of a system that works
    better than it does.

    So: the numbers that are true right now. A queue draining is a system
    working, and it is the only honest thing an empty corpus has to show.
    """

    model_config = ConfigDict(from_attributes=True)

    as_of: dt.datetime

    #: Queue rows by status. Not a single depth: 4,000 `pending` and 4,000
    #: `failed` are the same number and opposite situations (§12.5).
    queue: dict[str, int] = Field(default_factory=dict)

    #: Domains fetched most recently, newest first. The concrete answer to
    #: "is it doing anything" — a count that moves says less than a name.
    recent_domains: list[str] = Field(default_factory=list)

    #: Fetch attempts in the last hour, and how many succeeded. Both, because
    #: a crawl failing steadily and a crawl succeeding steadily produce the
    #: same attempt count and want opposite reactions from the reader.
    attempts_last_hour: int = 0
    successes_last_hour: int = 0
