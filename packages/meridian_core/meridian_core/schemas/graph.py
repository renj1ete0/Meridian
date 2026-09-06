"""DTOs for entities, edges, and the attribute system (mirrors
``meridian_core.models.graph``, §5.4, §5.5, §7).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from meridian_core.models.graph import COMPARISON_RELATION

from .common import Confidence, CreateBase, ProvenanceFields, QualityTier, SupportingChunkIds
from .enums import AttributeScope, AttributeStatus, Certainty, NodeType, Stance


class EntityCreate(ProvenanceFields):
    """``embedding`` is accepted on write — entity resolution needs it for
    similarity search at write time (§5.5) — but is never returned; see
    ``EntityRead``. ``merged_from``/``redirects_to`` are excluded: merges are a
    separate, reversible admin operation, not something a caller sets when
    first proposing an entity.
    """

    canonical_name: str = Field(min_length=1)
    node_type: NodeType
    jurisdiction: str | None = None
    aliases: list[str] | None = None
    embedding: list[float] | None = None
    topic_labels: list[str] | None = None
    description: str | None = None
    confidence: Confidence | None = None
    is_annotation: bool = False


class EntityRead(BaseModel):
    """No ``embedding`` field: a 1024-float vector has no business in an API
    response payload (task P0-10 requirement 5)."""

    model_config = ConfigDict(from_attributes=True)

    entity_id: int
    canonical_name: str
    node_type: NodeType
    jurisdiction: str | None
    aliases: list[str] | None
    topic_labels: list[str] | None
    description: str | None
    confidence: Confidence | None
    merged_from: list[int] | None
    redirects_to: int | None
    is_annotation: bool
    produced_by: str | None
    model: str | None
    quality_tier: QualityTier | None
    produced_at: dt.datetime | None
    schema_version: int
    created_at: dt.datetime


class EdgeCreate(ProvenanceFields):
    """``supporting_chunk_ids`` must be non-empty: an edge without provenance
    is not assertable and cannot be re-derived from source (spec §2 principle
    3; task P0-10 requirement 6). ``relation_type`` is free text on the model
    (no CHECK constraint), so it stays a plain validated string here too."""

    from_node: int
    to_node: int
    relation_type: str = Field(min_length=1)
    topic_labels: list[str] | None = None
    supporting_chunk_ids: SupportingChunkIds
    confidence: Confidence | None = None
    stance: Stance | None = None
    certainty: Certainty | None = None
    contested_with: list[int] | None = None
    similarity_dimension: str | None = None
    disanalogy: str | None = None
    # When the fact held — not when we learned it (§9 temporal decay).
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    created_by: str | None = None

    @model_validator(mode="after")
    def _comparison_states_its_limits(self) -> EdgeCreate:
        """§7.2: a comparison edge must name its axis and its limits.

        Mirrors the database CHECK so the caller gets a 422 naming the missing
        field rather than an opaque integrity error.
        """
        if self.relation_type == COMPARISON_RELATION and not (
            self.similarity_dimension and self.disanalogy
        ):
            raise ValueError(
                "a comparison edge requires similarity_dimension and disanalogy (§7.2)"
            )
        return self


class EdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    edge_id: int
    from_node: int
    to_node: int
    relation_type: str
    topic_labels: list[str] | None
    supporting_chunk_ids: list[int]
    confidence: Confidence | None
    stance: Stance | None
    certainty: Certainty | None
    contested_with: list[int] | None
    similarity_dimension: str | None
    disanalogy: str | None
    valid_from: dt.date | None
    valid_to: dt.date | None
    created_by: str | None
    produced_by: str | None
    model: str | None
    quality_tier: QualityTier | None
    produced_at: dt.datetime | None
    schema_version: int
    created_at: dt.datetime


class AttributeDefinitionCreate(CreateBase):
    """Audit signals (discrimination, usage_count, consecutive_audit_failures,
    last_audited_at) are computed by the audit process, not caller input —
    that is the whole reason ``AttributeDefinitionRead`` carries more fields
    than this class (§7.3)."""

    name: str = Field(min_length=1)
    description: str | None = None
    scope: AttributeScope = "global"
    topic: str | None = None
    status: AttributeStatus = "active"
    schema_version: int = 1


class AttributeDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attribute_id: int
    name: str
    description: str | None
    scope: AttributeScope
    topic: str | None
    status: AttributeStatus
    schema_version: int
    discrimination: float | None
    usage_count: int
    consecutive_audit_failures: int
    last_audited_at: dt.datetime | None
    created_at: dt.datetime


class AttributeValueCreate(ProvenanceFields):
    """``supporting_chunk_ids`` non-empty for the same reason as on
    ``EdgeCreate`` (§2 principle 3)."""

    entity_id: int
    attribute_id: int
    value: str | None = None
    value_numeric: float | None = None
    value_json: dict | None = None
    confidence: Confidence | None = None
    supporting_chunk_ids: SupportingChunkIds
    tagged_at: dt.datetime | None = None


class AttributeValueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value_id: int
    entity_id: int
    attribute_id: int
    value: str | None
    value_numeric: float | None
    value_json: dict | None
    confidence: Confidence | None
    supporting_chunk_ids: list[int]
    tagged_at: dt.datetime | None
    produced_by: str | None
    model: str | None
    quality_tier: QualityTier | None
    produced_at: dt.datetime | None
    schema_version: int
    created_at: dt.datetime


class ObservationCreate(ProvenanceFields):
    """A measured quantity attached to an entity.

    ``metric`` plus ``unit`` plus ``denominator`` are what make a number mean
    anything — "3.2 percent" is unusable without knowing percent *of what*
    (§8: extract the structure rather than letting it be summarised away).
    """

    subject_entity_id: int
    metric: str = Field(min_length=1)
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    denominator: str | None = None
    geography_entity_id: int | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    method: str | None = None
    qualifiers: dict[str, Any] | None = None
    confidence: Confidence | None = None
    supporting_chunk_ids: SupportingChunkIds
    contested_with: list[int] | None = None

    @model_validator(mode="after")
    def _needs_a_value(self) -> ObservationCreate:
        """Mirrors the database CHECK; an observation with no value is not one."""
        if self.value_numeric is None and self.value_text is None:
            raise ValueError("observation needs value_numeric or value_text")
        return self

    @model_validator(mode="after")
    def _period_ordered(self) -> ObservationCreate:
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end precedes period_start")
        return self


class ObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    observation_id: int
    subject_entity_id: int
    metric: str
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    denominator: str | None
    geography_entity_id: int | None
    period_start: dt.date | None
    period_end: dt.date | None
    method: str | None
    qualifiers: dict[str, Any] | None
    confidence: Confidence | None
    supporting_chunk_ids: list[int]
    contested_with: list[int] | None
    produced_by: str | None
    model: str | None
    quality_tier: QualityTier | None
    produced_at: dt.datetime | None
    schema_version: int
    created_at: dt.datetime
