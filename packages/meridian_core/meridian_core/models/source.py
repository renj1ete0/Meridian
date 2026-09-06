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

    source: Mapped[Source] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", name="uq_chunks_source_id_chunk_index"),
        # The high-water mark scan: everything after the last consumed chunk (§6.3).
        Index("ix_chunks_id_created", "chunk_id", "created_at"),
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
