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
    JSON,
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB
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
    language: Mapped[str | None] = mapped_column(Text, index=True)

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

    source: Mapped[Source] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", name="uq_chunks_source_id_chunk_index"),
        # The high-water mark scan: everything after the last consumed chunk (§6.3).
        Index("ix_chunks_id_created", "chunk_id", "created_at"),
        # The novelty gate's queue. Partial, because the rows it wants are the
        # shrinking minority: everything embedded and not yet judged.
        Index(
            "ix_chunks_novelty_pending",
            "chunk_id",
            postgresql_where=sql_text("embedding IS NOT NULL AND novelty_checked_at IS NULL"),
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

    caption: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    vlm_description: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)

    linked_entity_ids: Mapped[list[int] | None] = mapped_column(JSON)

    source: Mapped[Source] = relationship(back_populates="figures")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Figure {self.figure_id} src={self.source_id} p{self.page}>"
