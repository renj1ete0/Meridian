"""Sources, chunks, and figures (spec §5.2, §5.3, §6.6).

Two things here are load-bearing and easy to get wrong later:

- ``Chunk.page_or_offset`` is captured **at extraction time**. Reconstructing it
  afterwards is painful and often impossible (§5.3).
- ``Source.source_tier`` is assigned mechanically from the domain and document
  structure, never by asking a model (§5.2). It is the first tiebreaker when
  sources conflict.
"""

from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import text as sql_text  # `Chunk.text` shadows the name in that class body
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meridian_core.db import Base

from .mixins import TRUST_STATE, TimestampMixin, constrained, pk

# bge-m3 dense vectors (§4). Changing this is a migration and a full re-embed.
EMBEDDING_DIM = 1024

SOURCE_TIER = constrained(
    "peer_reviewed", "government", "institutional", "press", "informal", name="source_tier"
)

# Drives raw-file retention (§5.4). Link rot is the binding reason to keep
# primary sources: government URLs reorganise constantly.
RETENTION_TIER = constrained("primary", "background", "junk", name="retention_tier")

OCR_TIER = constrained("none", "cheap", "quality", name="ocr_tier")

# What a document *is* (task B-59), beside `source_tier`'s who published it.
# Assigned mechanically at fetch from the document's own structure, never by a
# model. `listing` is a page whose value is its links — an index, a feed of new
# items, a search result page — and is followed but not chunked.
DOC_KIND = constrained(
    "paper", "report", "news", "legal", "profile", "listing", "other", name="doc_kind"
)


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    source_id: Mapped[int] = pk()

    url: Mapped[str] = mapped_column(Text, nullable=False)
    archive_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    doi: Mapped[str | None] = mapped_column(Text, index=True)
    accessed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str | None] = mapped_column(Text)

    # HTTP cache validators from the last successful fetch, echoed back on the
    # next one (§6.4 `conditional_requests`, which makes re-checks nearly free).
    #
    # ``last_modified`` is Text, not a timestamp, and that is not laziness. The
    # header is compared by the origin as an opaque string; parsing it to a
    # datetime and formatting it back would re-serialise a server's
    # "Sun, 30 Aug 2026 04:11:49 GMT" into whatever this codebase prefers, and a
    # strict origin would then stop returning 304 — silently turning the
    # cheapest request in the crawl back into the most expensive one.
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)

    source_tier: Mapped[str] = mapped_column(
        SOURCE_TIER, nullable=False, default="informal", server_default="informal"
    )
    retention_tier: Mapped[str] = mapped_column(
        RETENTION_TIER, nullable=False, default="background", server_default="background"
    )
    raw_file_path: Mapped[str | None] = mapped_column(Text)

    #: The raw store ``raw_file_path`` is relative to (task P1-45).
    #:
    #: Provenance, not a lookup. Resolution still goes through
    #: ``MERIDIAN_RAW_ROOT``, because an absolute path in this table would bake
    #: in a container's mount point and break the moment the store moved.
    #:
    #: It exists because without it a corpus that spans two roots — one worker
    #: run natively, one in a container against its bind mount — produces rows
    #: that dangle from either root's point of view, and nothing can tell that
    #: from a file that was genuinely lost. NULL means "written before this
    #: column existed", which is the truth and is not the same as "unknown root".
    raw_root: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text, index=True)

    #: Which extractor produced this source's text (task P1-44, §6.6).
    #:
    #: §6.6 routes each format to a different tool and HTML to two of them, so
    #: "how was this read" has a different answer per row and is not derivable
    #: from the media type. Without it the only way to tell a browser-extracted
    #: page from a locally-extracted one is to look for markdown link syntax in
    #: the text — which is how `P1-43` was found, and is not a diagnostic
    #: anyone should have to invent twice.
    #:
    #: Deliberately **not** `constrained()`. The value set grows whenever an
    #: extractor is added or a compound path is named, and a CHECK here would
    #: recreate `P1-28`'s trap exactly: a literal used in code and missing from
    #: the enum raises at the insert, after the fetch, the parse and the log
    #: line have all reported success. This column is a diagnostic, and a
    #: diagnostic that can fail a write is worse than no diagnostic.
    extractor: Mapped[str | None] = mapped_column(Text)

    # A source with no extractable text is still a citable graph participant and
    # still counts toward coverage — metadata-only is a valid resting state (§6.5).
    text_available: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )

    # Recorded explicitly so silently-skipped OCR is findable (§6.6).
    ocr_applied: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    ocr_tier: Mapped[str] = mapped_column(
        OCR_TIER, nullable=False, default="none", server_default="none"
    )
    ocr_confidence: Mapped[float | None] = mapped_column()

    #: Which topics this source's *content* is about (tasks P2-14, P2-21,
    #: §12.5, §12.3).
    #:
    #: An array, and named to match `entities.topic_labels` and
    #: `gazetteer.topic_labels`. Zero, one or several, best first: written by
    #: `python -m worker.retopic` from the source's chunk vectors, never by the
    #: fetch path — why a page was crawled is `crawled_for`, and conflating the
    #: two is the defect `P2-21` fixed.
    #:
    #: **NULL and `{}` are different.** NULL means nothing has examined this
    #: source's content; `{}` means it was examined and is about none of the
    #: topics. Only the first is the labeller's queue.
    topic_labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    #: Which queue topics caused this source to be fetched (task P2-21).
    #:
    #: Provenance, and nothing more: the reason the crawler went there. It used
    #: to *be* `topic_labels`, and that is how a crawl pursuing one topic
    #: stamped it onto every page a site's navigation led to, whatever those
    #: pages were about. Accumulates across fetches — a URL reached under two
    #: topics was crawled for both — and is written at fetch time, where the
    #: queue row is still in hand.
    crawled_for: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    #: When the content labeller last examined this source (`P2-21`). NULL is
    #: the queue; a live chunk newer than this means a re-crawl rewrote the
    #: text and the labels describe a page that no longer exists.
    topics_examined_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: The basis the labels were computed under — a fingerprint of the model,
    #: the thresholds and every topic's prototype text (`P2-21`). A topic added,
    #: archived or re-described changes the fingerprint, and every source whose
    #: basis differs is re-examined. Opaque on purpose: compare, never parse.
    topic_basis: Mapped[str | None] = mapped_column(Text)

    #: Every labelling topic's similarity to this source, under `topic_basis`
    #: (`P2-21`). What the labels were decided from, kept so a label can be
    #: explained — and so an off-topic demotion reads the same numbers the
    #: labelling did rather than recomputing them.
    topic_scores: Mapped[dict | None] = mapped_column(JSONB)

    #: Which places this source's content is about (task P2-23, §7.2).
    #:
    #: Normalised codes, most-evidenced first: ISO 3166-1 alpha-2 for a
    #: country (``EU`` for the union), UN/LOCODE without its space for a city
    #: — five characters whose first two are the country, so a city always
    #: implies its country and the country is tagged beside it. Written by
    #: ``python -m worker.places``; see `meridian_core.places` for the method.
    #:
    #: **NULL and `{}` are different**, exactly as for `topic_labels`: NULL is
    #: "nothing has examined this", `{}` is "examined, and it names no place
    #: often enough to be about one".
    places: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    #: When the place pass last examined this source. NULL is the queue; a
    #: live chunk newer than this means the text changed under the tags.
    places_examined_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: The basis the places were decided under — the method, the thresholds
    #: and the vocabulary, including gazetteer terms. Opaque: compare, never
    #: parse. A different basis puts the source back in the queue.
    place_basis: Mapped[str | None] = mapped_column(Text)

    #: What the places were decided from, and which signals decided each: the
    #: name and gazetteer counts, cited place entities, the domain's country
    #: and the language. Kept so a tag can be checked by reading it.
    place_evidence: Mapped[dict | None] = mapped_column(JSONB)

    #: When §5.6's acronym harvest last read this document (task P5-02).
    #:
    #: NULL is the whole queue, exactly like ``chunks.novelty_checked_at``. A
    #: timestamp rather than a boolean because the harvest's rules will change —
    #: a better initialism check, a wider window — and a re-harvest then needs to
    #: be targetable at everything read before a date. A boolean can only be
    #: reset for the entire corpus at once.
    acronyms_harvested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: What screening concluded about this page (task P4-14, §2.5).
    #:
    #: The *domain* holds the verdict (`fetch_policy.trust_state`) because
    #: screening is paid per domain; this column is the page's own copy of the
    #: state it was stored under, so a chunk query can filter without joining
    #: and so clearing a domain later does not rewrite what was true at the
    #: time. A page on an unscreened domain that the pre-screen flagged is
    #: `quarantined` here whatever the domain says.
    #:
    #: **Quarantined is stored, never deleted** (§2.5). The page keeps its raw
    #: file, its extraction and its chunks; what it loses is eligibility for
    #: the set the slow loop reads, until something clears it.
    trust_state: Mapped[str] = mapped_column(
        TRUST_STATE, nullable=False, default="unscreened", server_default="unscreened", index=True
    )

    extra: Mapped[dict | None] = mapped_column(JSONB)

    #: What kind of document this is (task B-59): a paper, a report, a news
    #: article, legal text, an organisation's own page, a listing, or other.
    #:
    #: **NULL means nothing has classified it** — the backfill's queue — and is
    #: not `other`, which means the rules looked and none applied. The rule
    #: that decided, and the link measurements behind a listing verdict, are in
    #: ``extra["doc_kind"]`` so a verdict can be audited without re-deriving it.
    #: Overwritten on every fetch: it describes the page as it is now.
    doc_kind: Mapped[str | None] = mapped_column(DOC_KIND, index=True)

    #: The earlier source this one is a copy of (`B-44`) — the same document
    #: under another URL, or its PDF and its HTML page. Document-level, where
    #: `chunks.duplicate_of` is passage-level: the novelty gate judges passages
    #: one at a time and never hides a primary source, so a document fetched
    #: twice was searched twice and would be synthesised twice. Set by
    #: `worker.docdupes`; the earliest source is the canonical one and a copy
    #: always points at it directly, never at another copy. Nothing is deleted.
    duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sources.source_id", ondelete="SET NULL"), index=True
    )
    #: Which rule found it: ``exact`` (its passages are the other's) or
    #: ``near`` (same title, near-identical meaning, comparable length).
    duplicate_reason: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    figures: Mapped[list[Figure]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("url", name="uq_sources_url"),
        CheckConstraint(
            "duplicate_of IS NULL OR duplicate_of <> source_id",
            name="duplicate_is_another_source",
        ),
        CheckConstraint(
            "(duplicate_of IS NULL) = (duplicate_reason IS NULL)",
            name="duplicate_has_a_reason",
        ),
        Index("ix_sources_tier_date", "source_tier", "publication_date"),
        # The acronym harvest's queue (`P5-02`). Partial: everything with text
        # and not yet read. A metadata-only source has nothing to harvest, and
        # leaving it in the queue means re-skipping it on every pass forever.
        # GIN, because the query is array overlap (`&&`) rather than equality —
        # a btree cannot answer it at all, and without this the topic filter is
        # a sequential scan over every source in the corpus.
        Index("ix_sources_topic_labels", "topic_labels", postgresql_using="gin"),
        # The place filter is array overlap too (`P2-23`).
        Index("ix_sources_places", "places", postgresql_using="gin"),
        Index(
            "ix_sources_harvest_pending",
            "source_id",
            postgresql_where=text("acronyms_harvested_at IS NULL AND text_available"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Source {self.source_id} {self.source_tier} {self.url[:60]!r}>"


class Chunk(Base, TimestampMixin):
    __tablename__ = "chunks"

    chunk_id: Mapped[int] = pk()
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    #: Which `embedtext.VIEW_VERSION` the vector was computed from (`B-49`).
    #: NULL for vectors computed from the raw text, before the view existed.
    #: What lets `worker.reembed` find the vectors a view change made stale
    #: without taking any of them out of search while it works.
    embedding_view: Mapped[int | None] = mapped_column(SmallInteger)

    # Page number for paginated documents, character offset otherwise. Citations
    # need this to be accurate, so it is written when the text is extracted.
    page_or_offset: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- the novelty gate's verdict (§6.1, task P2-03) ---------------------
    #
    # Recorded rather than acted on. §6.1 says "drop if >0.95" and §5.4 says a
    # near-duplicate loses its raw file, but a gate that deletes leaves nothing
    # to audit, nothing to re-judge when the threshold moves, and no way to
    # compute §12.5's novelty pass rate. So the gate writes a verdict and the
    # retention sweep (`P1-31`) is what spends it.

    #: When the gate judged this chunk. NULL is the whole queue, the same way
    #: NULL ``embedding`` is the embedder's. One-shot on purpose: a chunk can
    #: only become a duplicate of something *older*, and the older one is
    #: already here, so a second judgement would find the same answer.
    novelty_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: Cosine similarity to the nearest chunk written before this one, judged or
    #: not. NULL means there was nothing to compare against — the first chunk in
    #: an empty corpus is not "similarity 0", it is unjudgeable, and a sentinel
    #: would be indistinguishable from a real orthogonal neighbour.
    nearest_similarity: Mapped[float | None] = mapped_column()

    #: The chunk this one duplicates, when the similarity cleared the threshold.
    #: Self-referential, and ``ON DELETE SET NULL`` rather than CASCADE: if the
    #: survivor is deleted by a re-crawl, this chunk is now the only copy of
    #: that text and deleting it too would lose the content entirely.
    duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id", ondelete="SET NULL"), index=True
    )

    # --- the lexical half of hybrid retrieval (§12.5, task P2-05) ---------
    #
    # A STORED generated column, not the trigger the task named. Postgres 12
    # made the trigger unnecessary, and a generated column is strictly stronger
    # than one: it cannot be bypassed by a write path that forgot to fire it,
    # cannot drift from ``text`` after a bulk UPDATE, and needs no ordering
    # agreement with any other BEFORE trigger on the table. The failure mode a
    # trigger has here is silent — a chunk that exists, is embedded, and is
    # unfindable lexically — and the corpus gives no signal that it happened.
    #
    # The regconfig is a literal on purpose. ``to_tsvector(text)`` resolves the
    # configuration through ``default_text_search_config``, which is a session
    # GUC and therefore not IMMUTABLE, and Postgres refuses it in a generated
    # column. Naming it also pins the stemming: the same text indexed under a
    # different session setting would otherwise produce a different vector.
    #
    # NOT NULL because ``text`` is: ``to_tsvector`` of a stopword-only string is
    # the empty tsvector, not NULL, so a NULL here would mean the column was
    # added without being generated — which is precisely the migration mistake
    # worth failing on.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
        nullable=False,
    )

    # --- superseded rather than deleted (§2.3, task P1-32) ----------------
    #
    # A re-crawl of a changed page used to DELETE this source's chunks and write
    # new ones. `edges.supporting_chunk_ids` is an array of ids with no foreign
    # key behind it — Postgres cannot enforce one on array elements — so a
    # deleted chunk left every edge citing it pointing at nothing. §2.3 makes
    # provenance mandatory on every edge, and an edge whose evidence row is gone
    # does not fail any check: it reads as an edge with provenance, and the
    # citation simply does not resolve.
    #
    # So nothing is deleted. The old chunks are stamped here, which keeps every
    # citation resolvable, keeps the *text an edge was actually derived from*
    # (§2.4 re-derives from source chunks, and the page has since changed), and
    # leaves the sweep free to reclaim the ones nothing cites.
    #
    # NULL is the live set, and every query that serves the corpus filters on
    # it: a superseded chunk is text that is no longer on the page, and serving
    # it would make the corpus quote a document as saying something it no longer
    # says.
    superseded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="chunks")

    __table_args__ = (
        # Unique among the *live* chunks only. The old set keeps its indices, so
        # a plain constraint over (source_id, chunk_index) would refuse the
        # replacement it exists to make possible.
        Index(
            "uq_chunks_live_index",
            "source_id",
            "chunk_index",
            unique=True,
            postgresql_where=sql_text("superseded_at IS NULL"),
        ),
        # The high-water mark scan: everything after the last consumed chunk (§6.3).
        Index("ix_chunks_id_created", "chunk_id", "created_at"),
        # The novelty gate's queue. Partial, because the rows it wants are the
        # shrinking minority: everything embedded and not yet judged. Superseded
        # chunks are excluded — judging text that is no longer on the page
        # spends the gate's budget on a verdict nothing will ever read.
        Index(
            "ix_chunks_novelty_pending",
            "chunk_id",
            postgresql_where=sql_text(
                "embedding IS NOT NULL AND novelty_checked_at IS NULL AND superseded_at IS NULL"
            ),
        ),
        # What the sweep reclaims: superseded, and cited by nothing. Kept
        # partial so it stays small — the live corpus is not in it at all.
        Index(
            "ix_chunks_superseded",
            "superseded_at",
            postgresql_where=sql_text("superseded_at IS NOT NULL"),
        ),
        # GIN rather than GiST: this index is read constantly and written once
        # per chunk, which is the tradeoff GIN is built for. GiST would be the
        # choice only if the corpus churned.
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        # --- the vector half of hybrid retrieval (§12.5, task P2-04) -------
        #
        # `vector_cosine_ops` because that is the operator everything here
        # already uses: `embeddings.py` normalises, so `<=>` is the cheap one,
        # and `novelty.py` compares with `cosine_distance`. An index built for
        # a different operator class is not a slower index, it is an unused
        # one — the planner silently declines it and every search becomes a
        # sequential scan over every vector in the corpus.
        #
        # Not partial on `duplicate_of IS NULL`. A near-duplicate is a verdict
        # that can be re-judged when the threshold moves (§6.1), and an index
        # that excluded them would have to be rebuilt to follow.
        #
        # HNSW rather than IVFFlat: IVFFlat needs a representative sample to
        # build its lists and is therefore wrong to create on an empty table,
        # which is exactly when a migration runs.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Chunk {self.chunk_id} src={self.source_id} #{self.chunk_index}>"


class Figure(Base, TimestampMixin):
    """Figures often carry findings more compactly than the text (§6.6).

    Captions are extracted at ingestion and indexed like any other text, which
    delivers most of the value at no cost. ``vlm_description`` is deferred
    enrichment and only ever populated by an explicit, user-triggered batch.
    """

    __tablename__ = "figures"

    figure_id: Mapped[int] = pk()
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False
    )

    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSONB)
    file_path: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)

    #: Where the image is on the web (task P1-10).
    #:
    #: `file_path` is a local raw path and nothing downloads figure images, so
    #: without this a row describes a picture nobody can ever look at — and the
    #: enrichment §6.6 defers (`P7-07`) would have nothing to fetch. It is the
    #: only handle on the image until something stores one.
    #:
    #: NULL for a figure found in a PDF's text layer: there the caption is
    #: extractable and the image is not addressable at all.
    image_url: Mapped[str | None] = mapped_column(Text)

    caption: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    vlm_description: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)

    #: Which graph nodes this figure illustrates (§6.6's lookup affordance).
    #:
    #: `ARRAY(BigInteger)`, matching `entities.merged_from` and the four
    #: `supporting_chunk_ids` columns — every other list of ids in this schema.
    #: It was `json` until `B-10`, which nothing chose: `json` keeps the literal
    #: document text, so `'[1, 2]'` and `'[1,2]'` are unequal values, there is
    #: nothing to index against, and reading one back means parsing JSON to
    #: recover integers Postgres could return directly.
    linked_entity_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))

    source: Mapped[Source] = relationship(back_populates="figures")

    __table_args__ = (
        # A source's figures are always read together — the figures panel
        # (`P6-14`) and any enrichment batch both start from "which figures does
        # this source have".
        Index("ix_figures_source", "source_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Figure {self.figure_id} src={self.source_id} p{self.page}>"


class ChunkTopics(Base):
    """Which topics one passage is about (task `P2-24`).

    `P2-21` labels a *source* from its mean chunk vector, which is right for a
    page and coarse for a long report: a book mostly about one topic with a
    chapter on another carries only the first, and the chapter is invisible to
    a topic filter. This is the same method applied to each chunk's own vector
    (:mod:`meridian_core.passagetopics`).

    **A side table, not columns on ``chunks``.** ``chunks`` carries an HNSW and
    a GIN index, and an UPDATE that cannot be HOT writes a new entry into every
    one of them — so re-labelling the corpus after a topic change would re-index
    every vector for the sake of a few bytes of labels, and bloat the table the
    embedder and search both live on. Here a re-label rewrites only these
    narrow rows. The cost is one join, by primary key, where labels are read.

    **No row is NULL; an empty array is ``{}``.** The same distinction
    ``sources.topic_labels`` keeps: a chunk with no row has not been examined
    (usually because it has no vector yet), and one whose row carries ``{}``
    was examined and is about none of the topics.
    """

    __tablename__ = "chunk_topics"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id", ondelete="CASCADE"), primary_key=True
    )
    #: Best first, like ``sources.topic_labels``. Never NULL: absence is the row.
    topic_labels: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    #: Every topic's score, so thresholds can be re-measured without vectors.
    topic_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: The passage basis fingerprint the labels were decided under.
    topic_basis: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which embedding view the scored vector came from (``chunks.embedding_view``
    #: at the time). A re-embed under a new view makes the row stale.
    embedding_view: Mapped[int | None] = mapped_column(SmallInteger)
    topics_examined_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Gaps counts passages per topic; overlap (`&&`) is what search asks.
        Index("ix_chunk_topics_labels", "topic_labels", postgresql_using="gin"),
    )


class PageLine(Base):
    """Which candidate lines one page's *uncleaned* text holds (task `B-43`).

    One row per distinct line hash per source, with the page's host beside it
    so the per-host counts are a single GROUP BY. Written at fetch from the text
    as extracted — never from cleaned text: counted after cleaning, a banner
    would vanish from the pages it was removed from, fall below the threshold,
    stop being removed and come back, a rule that switches itself off by
    working. Replaced whenever the page is re-chunked from a fresh fetch.
    """

    __tablename__ = "page_lines"

    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.source_id", ondelete="CASCADE"), primary_key=True
    )
    line_hash: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: Lower-cased, ``www.`` folded — the unit "every page of this site" means.
    host: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_page_lines_host_hash", "host", "line_hash"),)


class BoilerplateLine(Base):
    """A line a host repeats on enough of its pages to be furniture (task `B-43`).

    Derived, wholesale, from `page_lines` by `worker.boilerplate`; nothing else
    writes it and nothing is lost by truncating it. The counts are kept so a
    person can see why a line was judged repeated.
    """

    __tablename__ = "boilerplate_lines"

    host: Mapped[str] = mapped_column(Text, primary_key=True)
    line_hash: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: Pages of this host carrying the line, and the host's pages in all.
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    host_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class HostScore(Base):
    """How much of what a host serves is about the corpus's topics (task `B-48`).

    Derived, wholesale, by `worker.hostscore` from the content labels `P2-21`
    writes and from the pending queue. Read by the fetch loop to decide whether
    a link to the host is worth queueing — the loop never computes it, so no
    model or vector is anywhere near the crawl.
    """

    __tablename__ = "host_scores"

    host: Mapped[str] = mapped_column(Text, primary_key=True)
    #: Sources from this host whose content has been examined, and how many of
    #: those carry at least one topic.
    examined: Mapped[int] = mapped_column(Integer, nullable=False)
    on_topic: Mapped[int] = mapped_column(Integer, nullable=False)
    #: URL tasks waiting for this host when the score was computed.
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TranslationLookup(Base):
    """What one phrase is called in other languages, per Wikipedia (task `B-52`).

    §7.4 makes non-English seeds mandatory, and a language prefix on English
    words was measured to return English pages: the query needs the words. The
    worker may not call a model, so the words come from Wikipedia's
    interlanguage links — the title of the same article in another language,
    written by people who speak it. A phrase with no article is recorded too
    (``article`` NULL), so it is not looked up again until it is stale.
    """

    __tablename__ = "translation_lookups"

    #: The phrase as the vocabulary holds it, case-folded.
    phrase: Mapped[str] = mapped_column(Text, primary_key=True)
    #: The English article it resolved to, after redirects; NULL if none.
    article: Mapped[str | None] = mapped_column(Text)
    #: Language code → that language's title for the article.
    translations: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    looked_up_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
