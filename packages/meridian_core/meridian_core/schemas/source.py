"""DTOs for sources, chunks, and figures (mirrors ``meridian_core.models.source``,
§5.2, §5.3, §6.6).
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .common import CreateBase
from .enums import OcrTier, RetentionTier, SourceTier


class SourceCreate(CreateBase):
    url: str = Field(min_length=1)
    archive_url: str | None = None
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    publication_date: dt.date | None = None
    doi: str | None = None
    accessed_at: dt.datetime | None = None
    checksum: str | None = None
    source_tier: SourceTier = "informal"
    retention_tier: RetentionTier = "background"
    raw_file_path: str | None = None
    language: str | None = None
    text_available: bool = False
    ocr_applied: bool = False
    ocr_tier: OcrTier = "none"
    ocr_confidence: float | None = None
    extra: dict | None = None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: int
    url: str
    archive_url: str | None
    title: str | None
    author: str | None
    publisher: str | None
    publication_date: dt.date | None
    doi: str | None
    accessed_at: dt.datetime | None
    checksum: str | None
    source_tier: SourceTier
    retention_tier: RetentionTier
    raw_file_path: str | None
    language: str | None
    text_available: bool
    ocr_applied: bool
    ocr_tier: OcrTier
    ocr_confidence: float | None
    extra: dict | None
    created_at: dt.datetime


class ChunkCreate(CreateBase):
    """Written by the worker at extraction time (§5.3). ``embedding`` is
    optional because the vector is added one stage later, by the embedding
    step (queue: ``fetched`` -> ``extracted`` -> ``embedded``)."""

    source_id: int
    text: str = Field(min_length=1)
    embedding: list[float] | None = None
    page_or_offset: int | None = None
    chunk_index: int


class ChunkRead(BaseModel):
    """Deliberately has no ``embedding`` field: a 1024-float bge-m3 vector per
    chunk has no business riding along in an API response payload (task P0-10
    requirement 5). Callers that need the vector go through a dedicated
    similarity-search endpoint, not the chunk read model."""

    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    source_id: int
    text: str
    page_or_offset: int | None
    chunk_index: int
    created_at: dt.datetime


class FigureCreate(CreateBase):
    source_id: int
    page: int | None = None
    bbox: dict | None = None
    file_path: str | None = None
    thumbnail_path: str | None = None
    caption: str | None = None
    alt_text: str | None = None
    vlm_description: str | None = None
    ocr_text: str | None = None
    linked_entity_ids: list[int] | None = None


class FigureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    figure_id: int
    source_id: int
    page: int | None
    bbox: dict | None
    file_path: str | None
    thumbnail_path: str | None
    caption: str | None
    alt_text: str | None
    vlm_description: str | None
    ocr_text: str | None
    linked_entity_ids: list[int] | None
    created_at: dt.datetime
