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
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import text as sql_text  # `Chunk.text` shadows the name in that class body
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meridian_core.db import Base

from .mixins import TimestampMixin, constrained, pk

# bge-m3 dense vectors (§4). Changing this is a migration and a full re-embed.
EMBEDDING_DIM = 1024

SOURCE_TIER = constrained(
    "peer_reviewed", "government", "institutional", "press", "informal", name="source_tier"
)

# Drives raw-file retention (§5.4). Link rot is the binding reason to keep
# primary sources: government URLs reorganise constantly.
RETENTION_TIER = constrained("primary", "background", "junk", name="retention_tier")

OCR_TIER = constrained("none", "cheap", "quality", name="ocr_tier")


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

    #: Which topics this source belongs to (task P2-14, §12.5, §12.3).
    #:
    #: An array, and named to match `entities.topic_labels` and
    #: `gazetteer.topic_labels`, which already carry exactly this. A source
    #: genuinely belongs to more than one: a URL can be enqueued under several
    #: topics and its path can match several more, and picking one would make
    #: the label depend on whichever crawl ran last.
    #:
    #: **NULL and `{}` are different.** NULL means nothing has examined this
    #: source — every row written before the column existed — and `{}` means it
    #: was examined and matched nothing. Only the first is worth a backfill,
    #: which is what `python -m worker.retopic` reads.
    topic_labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    #: When §5.6's acronym harvest last read this document (task P5-02).
    #:
    #: NULL is the whole queue, exactly like ``chunks.novelty_checked_at``. A
    #: timestamp rather than a boolean because the harvest's rules will change —
    #: a better initialism check, a wider window — and a re-harvest then needs to
    #: be targetable at everything read before a date. A boolean can only be
    #: reset for the entire corpus at once.
    acronyms_harvested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    extra: Mapped[dict | None] = mapped_column(JSONB)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    figures: Mapped[list[Figure]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("url", name="uq_sources_url"),
        Index("ix_sources_tier_date", "source_tier", "publication_date"),
        # The acronym harvest's queue (`P5-02`). Partial: everything with text
        # and not yet read. A metadata-only source has nothing to harvest, and
        # leaving it in the queue means re-skipping it on every pass forever.
        # GIN, because the query is array overlap (`&&`) rather than equality —
        # a btree cannot answer it at all, and without this the topic filter is
        # a sequential scan over every source in the corpus.
        Index("ix_sources_topic_labels", "topic_labels", postgresql_using="gin"),
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
