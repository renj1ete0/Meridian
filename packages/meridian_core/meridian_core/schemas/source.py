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
    etag: str | None = None
    last_modified: str | None = None
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
    etag: str | None
    last_modified: str | None
    source_tier: SourceTier
    retention_tier: RetentionTier
    raw_file_path: str | None
    #: Which raw store the file was written into (`P1-45`). Provenance — it is
    #: not what resolves the path, and NULL means "written before the column".
    raw_root: str | None = None
    language: str | None
    #: Which tool read this source's text (`P1-44`). NULL on rows extracted
    #: before the column existed — which is the truth, and distinguishable from
    #: every real extractor name.
    extractor: str | None = None
    text_available: bool
    ocr_applied: bool
    ocr_tier: OcrTier
    ocr_confidence: float | None
    #: When §5.6's acronym harvest last read this document (`P5-02`). NULL is the
    #: queue, and the default keeps rows written before the column readable.
    acronyms_harvested_at: dt.datetime | None = None
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
    # The novelty gate's verdict (§6.1). Exposed because Explore has to be able
    # to say *why* a chunk is missing from a result set — a near-duplicate that
    # was silently filtered is indistinguishable from one that was never
    # crawled, and only one of those is worth investigating.
    novelty_checked_at: dt.datetime | None = None
    nearest_similarity: float | None = None
    duplicate_of: int | None = None
    # When a re-crawl retired this chunk (`P1-32`). NULL is the live set, and
    # every route that serves the corpus filters on it — exposed so a caller
    # holding a chunk id from an edge's provenance can tell "this is the text
    # the edge was derived from, and the page has since changed" from "this is
    # what the page says now".
    superseded_at: dt.datetime | None = None
    created_at: dt.datetime


class FigureCreate(CreateBase):
    source_id: int
    page: int | None = None
    bbox: dict | None = None
    file_path: str | None = None
    thumbnail_path: str | None = None
    image_url: str | None = None
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
    #: The image on the web (`P1-10`). `file_path` is a *local* path and nothing
    #: downloads figure images, so this is the only handle a reader — or
    #: `P7-07`'s enrichment — has on the picture itself.
    image_url: str | None = None
    caption: str | None
    alt_text: str | None
    vlm_description: str | None
    ocr_text: str | None
    linked_entity_ids: list[int] | None
    created_at: dt.datetime
