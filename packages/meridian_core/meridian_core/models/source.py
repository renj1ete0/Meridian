"""Sources, chunks, and figures (spec §5.2, §5.3, §6.6).

``Chunk.page_or_offset`` is captured at extraction time (§5.3), and
``Source.source_tier`` is assigned mechanically, never by a model (§5.2). See
docs/reference/data-model.md#sources for the reasons behind the columns.
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

# What a document *is* (task B-59), assigned mechanically at fetch. A `listing` is
# followed but not chunked.
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

    # HTTP cache validators from the last successful fetch, echoed back on the next
    # (§6.4). ``last_modified`` is opaque Text, never re-serialised; see
    # docs/reference/data-model.md#sources.
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)

    source_tier: Mapped[str] = mapped_column(
        SOURCE_TIER, nullable=False, default="informal", server_default="informal"
    )
    retention_tier: Mapped[str] = mapped_column(
        RETENTION_TIER, nullable=False, default="background", server_default="background"
    )
    raw_file_path: Mapped[str | None] = mapped_column(Text)

    #: The raw store ``raw_file_path`` is relative to (task P1-45). Provenance, not a
    #: lookup: resolution goes through ``MERIDIAN_RAW_ROOT``. NULL means written before
    #: this column existed.
    raw_root: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text, index=True)

    #: Which extractor produced this source's text (task P1-44, §6.6). Deliberately
    #: not `constrained()`: a diagnostic must never fail a write. See
    #: docs/reference/data-model.md#sources.
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

    #: Which topics this source's *content* is about, best first (tasks P2-14, P2-21).
    #: Written by `worker.retopic`, never by the fetch path. NULL is unexamined (the
    #: labeller's queue); `{}` is examined and about none.
    topic_labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    #: Which queue topics caused this source to be fetched (task P2-21): provenance
    #: only, never a content label. Accumulates across fetches.
    crawled_for: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    #: When the content labeller last examined this source (`P2-21`). NULL is
    #: the queue; a live chunk newer than this means a re-crawl rewrote the
    #: text and the labels describe a page that no longer exists.
    topics_examined_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: A fingerprint of the model, thresholds and topic prototypes the labels were
    #: computed under (`P2-21`); a source whose basis differs is re-examined. Compare,
    #: never parse.
    topic_basis: Mapped[str | None] = mapped_column(Text)

    #: Every labelling topic's similarity to this source, under `topic_basis`
    #: (`P2-21`), kept so a label can be explained.
    topic_scores: Mapped[dict | None] = mapped_column(JSONB)

    #: The best topic score when the labels were read from a *sample* of the passages
    #: (`B-89`); NULL when read from the whole text, or never read. Non-NULL also
    #: means "read again once whole".
    topic_sample_best: Mapped[float | None] = mapped_column()

    #: Which places this source's content is about (task P2-23, §7.2), most-evidenced
    #: first: ISO 3166-1 alpha-2 (``EU`` for the union) or UN/LOCODE without its space,
    #: a city always beside its country. NULL is unexamined; `{}` is about none.
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

    #: When §5.6's acronym harvest last read this document (task P5-02). NULL is the
    #: queue; a timestamp so a re-harvest can target a date range.
    acronyms_harvested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: What screening concluded about this page when stored (task P4-14, §2.5): the
    #: page's copy of the domain's verdict. Quarantined is stored, never deleted. See
    #: docs/reference/data-model.md#sources.
    trust_state: Mapped[str] = mapped_column(
        TRUST_STATE, nullable=False, default="unscreened", server_default="unscreened", index=True
    )

    extra: Mapped[dict | None] = mapped_column(JSONB)

    #: What kind of document this is (task B-59). NULL is unclassified, unlike
    #: `other`; the deciding rule is in ``extra["doc_kind"]``. Overwritten on every fetch.
    doc_kind: Mapped[str | None] = mapped_column(DOC_KIND, index=True)

    #: The earlier source this one is a copy of (`B-44`), set by `worker.docdupes`.
    #: Always the canonical (earliest) source, never another copy. Nothing is deleted.
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
        # The acronym harvest's queue (`P5-02`): partial, text and not yet read. The
        # array indexes are GIN because the queries are overlap (`&&`).
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
    #: Which `embedtext.VIEW_VERSION` the vector was computed from (`B-49`); NULL for
    #: vectors from the raw text. How `worker.reembed` finds stale vectors.
    embedding_view: Mapped[int | None] = mapped_column(SmallInteger)

    # Page number for paginated documents, character offset otherwise. Citations
    # need this to be accurate, so it is written when the text is extracted.
    page_or_offset: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- the novelty gate's verdict (§6.1, task P2-03) ---------------------
    # Recorded rather than acted on; see docs/reference/data-model.md#chunks.

    #: When the gate judged this chunk. NULL is the queue. One-shot: a chunk can only
    #: duplicate something older, which a second judgement would find again.
    novelty_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: Cosine similarity to the nearest chunk written before this one, judged or not.
    #: NULL means there was nothing to compare against, not similarity 0.
    nearest_similarity: Mapped[float | None] = mapped_column()

    #: The chunk this one duplicates, when the similarity cleared the threshold.
    #: ``SET NULL``, not CASCADE: if the survivor goes, this is the only copy left.
    duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id", ondelete="SET NULL"), index=True
    )

    # --- the lexical half of hybrid retrieval (§12.5, task P2-05) ---------
    # A STORED generated column with a literal regconfig, not a trigger; see
    # docs/reference/data-model.md#chunks.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
        nullable=False,
    )

    # --- superseded rather than deleted (§2.3, task P1-32) ----------------
    # Edges cite chunks by an array with no foreign key, so a changed page's old
    # chunks are stamped, not deleted. NULL is the live set, and every query serving
    # the corpus filters on it. See docs/reference/data-model.md#chunks.
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
        # The novelty gate's queue: partial, embedded and not yet judged, superseded
        # chunks excluded.
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
        # RUM alongside it, for ranking (`B-65`); GIN stays for unranked `@@`. See
        # docs/reference/data-model.md#chunks.
        Index(
            "ix_chunks_search_rum",
            "search_vector",
            postgresql_using="rum",
            postgresql_ops={"search_vector": "rum_tsvector_ops"},
        ),
        # --- the vector half of hybrid retrieval (§12.5, task P2-04) -------
        # HNSW, cosine ops, not partial. Half precision (`B-136`, ADR 0007): queries
        # must order by `vectorindex.indexed_distance`. See
        # docs/reference/data-model.md#chunks.
        Index(
            "ix_chunks_embedding_hnsw_half",
            sql_text("(embedding::halfvec(1024)) halfvec_cosine_ops"),
            postgresql_using="hnsw",
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

    #: Where the image is on the web (task P1-10), the only handle on it since nothing
    #: downloads figure images. NULL for a figure found in a PDF's text layer.
    image_url: Mapped[str | None] = mapped_column(Text)

    caption: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    vlm_description: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)

    #: Which graph nodes this figure illustrates (§6.6). `ARRAY(BigInteger)`, like every
    #: list of ids here (`B-10`); see docs/reference/data-model.md#conventions.
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
    """Which topics one passage is about (task `P2-24`), by :mod:`meridian_core.passagetopics`.

    A side table so a re-label does not rewrite ``chunks`` and its indexes. No row means
    not examined; ``{}`` means examined and about none. See
    docs/reference/data-model.md#chunk-topics.
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

    One row per distinct line hash per source, with the host beside it. Written from
    the text as extracted, never from cleaned text; see
    docs/reference/data-model.md#page-lines.
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
    #: Other hosts with an on-topic page linking here (`B-150`).
    vouched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: The examined and on-topic counts over pages reached by following a link or a sitemap
    #: (`B-155`): what predicts the next followed page, unlike pages a search picked.
    followed_examined: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    followed_on_topic: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class LinkVouch(Base):
    """A page linking to another host (task `B-150`).

    One row per (linked host, linking page). Written as each page's links are read, before
    any of them is dropped as already queued, so every page that links to a host counts, not
    only the first. `worker.hostscore` reads it as how many on-topic sites vouch for a host.
    """

    __tablename__ = "link_vouches"

    host: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sources.source_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class TranslationLookup(Base):
    """What one phrase is called in other languages, per Wikipedia (task `B-52`).

    From interlanguage links, since the worker may not call a model. A phrase with no
    article is recorded too (``article`` NULL), so it is not looked up again until stale.
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
