"""Pydantic v2 DTOs for every service boundary (spec §2.6, §11.8).

Usually a ``*Create`` and a ``*Read`` per table. Enum literals come from the models'
CHECK constraints, and no ``*Read`` carries an embedding vector. Services import DTOs
from here and never define their own. See docs/reference/data-model.md#boundary-schemas.
"""

from __future__ import annotations

from .annotations import (
    AnnotationCreate,
    AnnotationEdit,
    AnnotationRead,
    AnnotationsRead,
    AnnotationTarget,
)
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
from .corpusmap import CorpusMapRead, MapPointRead
from .enums import (
    AgentAvailability,
    AttributeScope,
    AttributeStatus,
    Certainty,
    DocKind,
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
    SeedMechanism,
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
from .queue import FetchAttemptRead, FetchHealth, QueueTaskCreate, QueueTaskRead
from .runs import (
    EnrichmentItemCreate,
    EnrichmentItemRead,
    NotificationCreate,
    NotificationItemRead,
    NotificationRead,
    ReportCreate,
    ReportRead,
    RunCreate,
    RunRead,
)
from .search import (
    CorpusStatsRead,
    SearchHitRead,
    SearchResponse,
    SourceChunksRead,
)
from .source import ChunkCreate, ChunkRead, FigureCreate, FigureRead, SourceCreate, SourceRead
from .steering_proposals import (
    SteeringProposalRead,
    SteeringProposalReject,
    SteeringProposalsRead,
)
from .views import SavedViewCreate, SavedViewEdit, SavedViewRead, SavedViewsRead

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
    "DocKind",
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
    "SeedMechanism",
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
    # retrieval (P2-07)
    "SearchHitRead",
    "SearchResponse",
    "CorpusStatsRead",
    "CorpusMapRead",
    "MapPointRead",
    "SourceChunksRead",
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
    # annotations
    "AnnotationCreate",
    "AnnotationEdit",
    "AnnotationRead",
    "AnnotationTarget",
    "AnnotationsRead",
    # saved views
    "SavedViewCreate",
    "SavedViewEdit",
    "SavedViewRead",
    "SavedViewsRead",
    # config
    "TopicConfigCreate",
    "TopicConfigRead",
    "SteeringLogCreate",
    "SteeringLogRead",
    "FetchAttemptRead",
    "FetchHealth",
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
    "NotificationItemRead",
    "NotificationRead",
    "ObservationCreate",
    "ObservationRead",
    # steering proposals (P6-38)
    "SteeringProposalRead",
    "SteeringProposalReject",
    "SteeringProposalsRead",
]
