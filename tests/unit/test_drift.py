"""Drift tests: two sources of truth must not disagree.

Every test here compares one definition against another rather than asserting a
literal. A test that hardcodes the expected value set would need editing every
time the schema legitimately grows, and would then pass while the two halves of
the system disagreed — which is the failure mode this file exists to prevent.
"""

from __future__ import annotations

import typing

import pytest
from sqlalchemy import Enum as SAEnum

from meridian_core import models, schemas
from meridian_core.db import Base
from meridian_core.models import mixins

# --------------------------------------------------------------------------
# Enum drift: DTO Literal aliases vs the models' CHECK-constrained columns
# --------------------------------------------------------------------------

# (Literal alias in schemas.enums, SQLAlchemy Enum in models)
ENUM_PAIRS = [
    ("TaskStatus", models.queue.TASK_STATUS),
    ("TaskType", models.queue.TASK_TYPE),
    ("SeedSource", models.queue.SEED_SOURCE),
    ("FetchOutcome", models.queue.FETCH_OUTCOME),
    ("SourceTier", models.source.SOURCE_TIER),
    ("RetentionTier", models.source.RETENTION_TIER),
    ("OcrTier", models.source.OCR_TIER),
    ("NodeType", models.graph.NODE_TYPE),
    ("Stance", models.graph.STANCE),
    ("Certainty", models.graph.CERTAINTY),
    ("AttributeScope", models.graph.ATTRIBUTE_SCOPE),
    ("AttributeStatus", models.graph.ATTRIBUTE_STATUS),
    ("GazetteerEntityType", models.gazetteer.GAZETTEER_ENTITY_TYPE),
    ("GazetteerSource", models.gazetteer.GAZETTEER_SOURCE),
    ("TopicStatus", models.config.TOPIC_STATUS),
    ("DomainStatus", models.config.DOMAIN_STATUS),
    ("TokenScope", models.config.TOKEN_SCOPE),
    ("AgentAvailability", models.config.AVAILABILITY),
    ("RunStage", models.runs.RUN_STAGE),
    ("RunStatus", models.runs.RUN_STATUS),
    ("JobStatus", models.runs.JOB_STATUS),
    ("EnrichmentType", models.runs.ENRICHMENT_TYPE),
    ("NotificationType", models.runs.NOTIFICATION_TYPE),
]


@pytest.mark.parametrize("alias_name,sa_enum", ENUM_PAIRS)
def test_dto_literal_matches_model_enum(alias_name: str, sa_enum: SAEnum) -> None:
    """An API that accepts a value the database rejects is a boundary bug."""
    from meridian_core.schemas import enums as dto_enums

    alias = getattr(dto_enums, alias_name)
    assert set(typing.get_args(alias)) == set(sa_enum.enums), (
        f"{alias_name} has drifted from the model's value set"
    )


def test_every_model_enum_has_a_dto_alias() -> None:
    """A new constrained column must gain a Literal, or DTOs silently accept anything.

    Without this, adding a column and forgetting the alias types it as bare `str`
    at the boundary, and the CHECK constraint becomes the only guard — a 500
    instead of a 422.
    """
    covered = {id(sa_enum) for _, sa_enum in ENUM_PAIRS}
    missing = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            type_ = column.type
            if isinstance(type_, SAEnum) and id(type_) not in covered:
                missing.append(f"{table.name}.{column.name}")
    assert not missing, f"constrained columns with no DTO Literal: {missing}"


# --------------------------------------------------------------------------
# Model/DTO parity: a new column must not be invisible at the API boundary
# --------------------------------------------------------------------------

# model class -> Read DTO. Fields deliberately withheld are listed with a reason.
READ_PAIRS = [
    (models.QueueTask, schemas.QueueTaskRead, set()),
    (models.FetchAttempt, schemas.FetchAttemptRead, set()),
    # Embeddings are 1024 floats — never in a response payload.
    (models.Source, schemas.SourceRead, set()),
    (models.Chunk, schemas.ChunkRead, {"embedding"}),
    (models.Figure, schemas.FigureRead, set()),
    (models.Entity, schemas.EntityRead, {"embedding"}),
    (models.Edge, schemas.EdgeRead, set()),
    (models.AttributeDefinition, schemas.AttributeDefinitionRead, set()),
    (models.AttributeValue, schemas.AttributeValueRead, set()),
    (models.Observation, schemas.ObservationRead, set()),
    (models.GazetteerTerm, schemas.GazetteerTermRead, set()),
    (models.TopicConfig, schemas.TopicConfigRead, set()),
    (models.FetchPolicy, schemas.FetchPolicyRead, set()),
    (models.Run, schemas.RunRead, set()),
    (models.EnrichmentItem, schemas.EnrichmentItemRead, set()),
    (models.Report, schemas.ReportRead, set()),
    (models.Notification, schemas.NotificationRead, set()),
]


@pytest.mark.parametrize("model,dto,exempt", READ_PAIRS, ids=lambda v: getattr(v, "__name__", ""))
def test_read_dto_covers_every_model_column(model, dto, exempt: set[str]) -> None:
    """Adding a column without exposing it leaves the API quietly incomplete."""
    columns = {c.name for c in model.__table__.columns}
    fields = set(dto.model_fields)
    unexposed = columns - fields - exempt
    assert not unexposed, (
        f"{model.__name__} columns absent from {dto.__name__}: {sorted(unexposed)}. "
        "Expose them, or add to the exempt set with a reason."
    )


@pytest.mark.parametrize("model,dto,exempt", READ_PAIRS, ids=lambda v: getattr(v, "__name__", ""))
def test_read_dto_invents_no_fields(model, dto, exempt: set[str]) -> None:
    """The reverse direction: a DTO field with no column behind it never populates."""
    columns = {c.name for c in model.__table__.columns}
    invented = set(dto.model_fields) - columns
    assert not invented, f"{dto.__name__} fields with no backing column: {sorted(invented)}"


# --------------------------------------------------------------------------
# Provenance completeness (spec §2.3, §11.12)
# --------------------------------------------------------------------------

PROVENANCE_COLUMNS = {"produced_by", "model", "quality_tier", "produced_at", "schema_version"}

# Tables whose rows are model-produced artifacts and must therefore be auditable.
PROVENANCE_BEARING = ["entities", "edges", "attribute_values", "observations"]


@pytest.mark.parametrize("table_name", PROVENANCE_BEARING)
def test_artifact_tables_carry_full_provenance(table_name: str) -> None:
    """Quality tier can only move up automatically if every row records its tier."""
    table = Base.metadata.tables[table_name]
    present = {c.name for c in table.columns}
    missing = PROVENANCE_COLUMNS - present
    assert not missing, f"{table_name} is missing provenance columns: {sorted(missing)}"


@pytest.mark.parametrize("table_name", ["edges", "attribute_values", "observations"])
def test_assertable_rows_require_supporting_chunks(table_name: str) -> None:
    """An edge, tag, or observation without a justifying chunk is not assertable
    (§2 principle 3)."""
    column = Base.metadata.tables[table_name].c.supporting_chunk_ids
    assert not column.nullable, (
        f"{table_name}.supporting_chunk_ids is nullable — provenance would be optional"
    )


def _bounds(annotated) -> tuple[object, object]:
    """Pull (ge, le) out of an Annotated[..., Field(...)] alias.

    Pydantic v2 keeps constraints in ``FieldInfo.metadata`` as annotated-types
    objects rather than as attributes on FieldInfo itself.
    """
    ge = le = None
    for meta in typing.get_args(annotated)[1:]:
        for constraint in getattr(meta, "metadata", []):
            ge = getattr(constraint, "ge", ge)
            le = getattr(constraint, "le", le)
    return ge, le


def test_quality_tier_bounds_are_shared() -> None:
    """The DTO bound and the registry ordinal must come from one definition."""
    from meridian_core.schemas.common import QualityTier

    assert _bounds(QualityTier) == (mixins.QUALITY_TIER_MIN, mixins.QUALITY_TIER_MAX)


def test_confidence_is_a_probability() -> None:
    """Confidence is 0..1 everywhere — not a percentage, not an unbounded score."""
    from meridian_core.schemas.common import Confidence

    assert _bounds(Confidence) == (0.0, 1.0)
