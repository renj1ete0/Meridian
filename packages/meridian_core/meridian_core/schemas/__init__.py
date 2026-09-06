"""Pydantic v2 DTOs for every service boundary (AGENTS.md: "pydantic for all
boundaries"; spec §2.6, §11.8: "All writes validate server-side. Never trust
model output for structure").

Every SQLAlchemy table in ``meridian_core.models`` gets the DTO variants that
are actually useful at its boundary — usually a ``*Create`` (what a caller may
submit) and a ``*Read`` (what an API returns, built from an ORM instance via
``model_validate`` with ``model_config = ConfigDict(from_attributes=True)``).
Where a table's write and read shapes coincide closely enough that a second
class would just retype the first, we said so in that module rather than
generating one anyway (task P0-10 requirement 2).

Two structural rules keep this package from drifting away from the tables it
describes:

- Enum-valued columns are ``constrained(...)`` CHECK constraints, not native
  Postgres enums (see ``models/mixins.py``). ``schemas/enums.py`` builds a
  ``Literal`` from each constraint's own ``.enums`` tuple instead of retyping
  the value set, so a status added on the model side is valid here too without
  anyone remembering to update a second list.
- Embedding vectors (``Chunk.embedding``, ``Entity.embedding``) never appear in
  a ``*Read`` model. A 1024-float bge-m3 array per row has no business riding
  along in an API response payload; a caller that needs it goes through a
  dedicated similarity-search endpoint instead.

Services import DTOs from here; they never define their own models (AGENTS.md
layout rule).
"""

from __future__ import annotations

from .common import Confidence, ProvenanceFields, QualityTier, SupportingChunkIds
from .config import (
    AgentCreate,
    AgentRead,
    FetchPolicyCreate,
    FetchPolicyRead,
    SteeringLogCreate,
    SteeringLogRead,
    TopicConfigCreate,
    TopicConfigRead,
)
from .enums import (
    AgentAvailability,
    AttributeScope,
    AttributeStatus,
    Certainty,
    DomainStatus,
    EnrichmentType,
    GazetteerEntityType,
    GazetteerSource,
    JobStatus,
    NodeType,
    NotificationType,
    OcrTier,
    RetentionTier,
    RunStage,
    RunStatus,
    SeedSource,
    SourceTier,
    Stance,
    TaskStatus,
    TaskType,
    TokenScope,
    TopicStatus,
)
from .gazetteer import GazetteerTermCreate, GazetteerTermRead
from .graph import (
    AttributeDefinitionCreate,
    AttributeDefinitionRead,
    AttributeValueCreate,
    AttributeValueRead,
    EdgeCreate,
    EdgeRead,
    EntityCreate,
    EntityRead,
    ObservationCreate,
    ObservationRead,
)
from .queue import QueueTaskCreate, QueueTaskRead
from .runs import (
    EnrichmentItemCreate,
    EnrichmentItemRead,
    NotificationCreate,
    NotificationRead,
    ReportCreate,
    ReportRead,
    RunCreate,
    RunRead,
)
from .source import ChunkCreate, ChunkRead, FigureCreate, FigureRead, SourceCreate, SourceRead

__all__ = [
    # common
    "Confidence",
    "ProvenanceFields",
    "QualityTier",
    "SupportingChunkIds",
    # enums (Literal aliases)
    "AgentAvailability",
    "AttributeScope",
    "AttributeStatus",
    "Certainty",
    "DomainStatus",
    "EnrichmentType",
    "GazetteerEntityType",
    "GazetteerSource",
    "JobStatus",
    "NodeType",
    "NotificationType",
    "OcrTier",
    "RetentionTier",
    "RunStage",
    "RunStatus",
    "SeedSource",
    "SourceTier",
    "Stance",
    "TaskStatus",
    "TaskType",
    "TokenScope",
    "TopicStatus",
    # queue
    "QueueTaskCreate",
    "QueueTaskRead",
    # source / chunk / figure
    "SourceCreate",
    "SourceRead",
    "ChunkCreate",
    "ChunkRead",
    "FigureCreate",
    "FigureRead",
    # graph
    "EntityCreate",
    "EntityRead",
    "EdgeCreate",
    "EdgeRead",
    "AttributeDefinitionCreate",
    "AttributeDefinitionRead",
    "AttributeValueCreate",
    "AttributeValueRead",
    # gazetteer
    "GazetteerTermCreate",
    "GazetteerTermRead",
    # config
    "TopicConfigCreate",
    "TopicConfigRead",
    "SteeringLogCreate",
    "SteeringLogRead",
    "FetchPolicyCreate",
    "FetchPolicyRead",
    "AgentCreate",
    "AgentRead",
    # runs / enrichment / reports / notifications
    "RunCreate",
    "RunRead",
    "EnrichmentItemCreate",
    "EnrichmentItemRead",
    "ReportCreate",
    "ReportRead",
    "NotificationCreate",
    "NotificationRead",
    "ObservationCreate",
    "ObservationRead",
]
