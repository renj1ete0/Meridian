"""DTOs for the retrieval boundary (task P2-07; mirrors ``meridian_core.search``).

``tests/unit/test_api_schemas.py`` compares these against the dataclasses field by
field. See docs/features/search.md#response-shapes.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .annotations import AnnotationRead
from .enums import FetchOutcome, LivenessState, PageUnit, SearchArm, SourceTier, TaskStatus
from .graph import EntityRead
from .runs import NotificationRead
from .source import ChunkRead


class SearchHitRead(BaseModel):
    """One chunk, with the provenance that makes it citable (§2 principle 3).

    Mirrors ``meridian_core.search.SearchHit``.
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
    #: Which topics this passage itself is about (`P2-24`), best first. None when no
    #: pass has examined the passage; `[]` when one has and found none.
    passage_topics: list[str] | None = None
    #: Which places the source is about (`P2-23`): ISO 3166-1 alpha-2 codes for
    #: countries, UN/LOCODE without its space for cities. None means never
    #: examined, `[]` examined and about no place it could name.
    places: list[str] | None = None

    #: What `page_or_offset` counts (`P2-18`, §5.3), and what it was derived from.
    #: None means the media type was never recorded.
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

    #: How old the document is, and what that did to its score (`P2-20`). `age_days`
    #: is None for an undated document, and `decay` is then 1.0.
    age_days: int | None = None
    decay: float = 1.0
    score_before_decay: float = 0.0


class SearchResponse(BaseModel):
    """A page of hits, and an account of how they were found.

    ``arms`` and ``degraded`` are part of the contract: a lexical-only search says so.
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
    #: Whether another page exists, from fetching one more hit than asked for.
    has_more: bool

    #: How deep each arm went before fusion; a page past it is refused. See
    #: docs/features/search.md#paging.
    candidate_pool: int
    lexical_candidates: int
    vector_candidates: int


class PlaceRead(BaseModel):
    """A place as a filter offers it: the stored code and a name to show (`P2-23`)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class CorpusStatsRead(BaseModel):
    """Mirrors ``meridian_core.stats.CorpusStats``, plus its derived count."""

    model_config = ConfigDict(from_attributes=True)

    #: When these numbers were counted (`P2-18`). A client cannot
    #: otherwise tell a cached count from a fresh one.
    as_of: dt.datetime
    #: Every source row, junk and copies included.
    sources: int
    #: Documents a reader can find: not junk, not a copy (`B-156`). What the landing shows.
    kept_sources: int
    chunks: int
    embedded_chunks: int
    duplicate_chunks: int
    searchable_chunks: int
    entities: int
    edges: int
    contested_edges: int

    #: The delta a returning reader asked for (`P6-11`), in kept documents (`B-156`). None
    #: means they did not ask; 0 means nothing arrived, and a landing page must not show the
    #: first as the second.
    new_sources: int | None = None
    new_chunks: int | None = None

    #: Every configured topic, most-attended first (`P6-24`) — what a filter
    #: control offers. From `topic_config`, so a topic with no sources yet is
    #: offered and filters to nothing, which is the true answer.
    topics: list[str] = Field(default_factory=list)

    #: Sources nothing has examined for topics (`P6-24`). What lets a topic
    #: filter tell a reader that narrowing may be hiding unexamined material.
    sources_without_topics: int = 0

    #: The comparison set's places, for a place filter to offer (`P2-23`), from
    #: configuration rather than a scan of the corpus.
    places: list[PlaceRead] = Field(default_factory=list)

    #: Sources nothing has examined for places (`P2-23`) — excluded by a place
    #: filter, as unexamined sources are by a topic filter.
    sources_without_places: int = 0


class SourceChunksRead(BaseModel):
    """A page of one source's chunks, in document order (``chunk_index``)."""

    source_id: int
    chunks: list[ChunkRead]
    limit: int
    offset: int
    has_more: bool


class FigureRefRead(BaseModel):
    """One figure, with what makes it openable (task P6-14, spec §6.6, §12.5).

    `raw_url` is None unless this deployment serves raw files.
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

    #: The caption or alt text a reader is shown: None when both are only the image's file
    #: name (`B-156`). `caption` and `alt_text` stay as extracted.
    reader_caption: str | None = None


class SourceFiguresRead(BaseModel):
    source_id: int
    figures: list[FigureRefRead]
    #: Whether this deployment serves raw files at all, so a client can explain
    #: an absent link rather than showing a broken one.
    raw_available: bool
    #: Logos, icons and controls left out of `figures` (`B-156`): counted, so their absence
    #: is said rather than silent.
    furniture_hidden: int = 0


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
    """Everything §12.5 asks the node panel to show, in one request."""

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

    #: The reader's own annotations (§12.5), newest first and capped. Defaulted, so a
    #: missing notes query is an empty section rather than a 500.
    annotations: list[AnnotationRead] = Field(default_factory=list)


class CrawlProgressRead(BaseModel):
    """What the crawl is doing, for a corpus too small to search (task `B-09`).

    Shown instead of a demo corpus; see docs/features/search.md#response-shapes.
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

    #: Whether the crawl is still fetching (`B-156`): the status pill's crawl half. None
    #: when it was not computed.
    liveness: LivenessRead | None = None


class HourBucketRead(BaseModel):
    """Mirrors ``meridian_core.crawlhealth.HourBucket``."""

    model_config = ConfigDict(from_attributes=True)

    start: dt.datetime
    succeeded: int
    failed: int


class OutcomeCountRead(BaseModel):
    """Mirrors ``meridian_core.crawlhealth.OutcomeCount``."""

    model_config = ConfigDict(from_attributes=True)

    outcome: FetchOutcome
    count: int


class DomainCountRead(BaseModel):
    """Mirrors ``meridian_core.crawlhealth.DomainCount``."""

    model_config = ConfigDict(from_attributes=True)

    domain: str
    attempts: int
    succeeded: int


class LivenessRead(BaseModel):
    """Mirrors ``meridian_core.crawlhealth.Liveness``.

    The state and the numbers behind it, not a sentence. The words belong to
    the screen, and a server that sent prose would leave every other consumer
    parsing it back into the numbers it started from.
    """

    model_config = ConfigDict(from_attributes=True)

    state: LivenessState
    last_attempt_at: dt.datetime | None
    quiet_seconds: int | None
    ready: int
    pending: int


class CrawlHealthRead(BaseModel):
    """What a long crawl has been doing, and whether it still is (task `P6-25`).

    Mirrors ``meridian_core.crawlhealth.CrawlHealth``. `CrawlProgressRead` is
    the first hour; this is the second week — a day of history, the outcome mix,
    and a verdict on whether anything is still fetching.
    """

    model_config = ConfigDict(from_attributes=True)

    as_of: dt.datetime
    #: The verdict's threshold, so the screen can say what "stalled" means
    #: rather than repeating a number that lives in Python.
    stall_after_seconds: int
    #: Oldest first, one per hour, empty hours included.
    hours: list[HourBucketRead]
    #: Every fetch outcome, zeros included, most frequent first.
    outcomes: list[OutcomeCountRead]
    #: Every queue status, zeros included.
    queue: dict[TaskStatus, int]
    #: Live chunks with no vector yet — the embedder's queue.
    embedding_backlog: int
    #: The busiest domains in the last hour.
    top_domains: list[DomainCountRead]
    liveness: LivenessRead


class TopicOverlapRead(BaseModel):
    """Sources carrying exactly this set of topics (`B-72`)."""

    topics: list[str]
    sources: int


class TopicOverlapsRead(BaseModel):
    """How labelled sources fall across topic combinations.

    Exact sets, so they add up: the sources carrying *at least* a selection are
    the sum over every set that contains it, which a client computes for any
    selection without another request.
    """

    overlaps: list[TopicOverlapRead]
    labelled_sources: int
