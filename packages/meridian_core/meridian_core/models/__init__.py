"""SQLAlchemy models. Services import from here; they never define their own.

Importing this package registers every table on ``Base.metadata``, which is what
Alembic autogenerate reflects against.
"""

from .areas import Area, AreaBridge, AreaBuild, AreaMember
from .config import (
    Agent,
    AgentToken,
    BudgetConfig,
    FetchPolicy,
    Grant,
    GrantAudit,
    ScheduledJob,
    SteeringLog,
    SteeringProposal,
    TopicConfig,
)
from .gazetteer import GazetteerTerm
from .graph import AttributeDefinition, AttributeValue, Edge, Entity, MergeLog, Observation
from .mixins import (
    CURRENT_SCHEMA_VERSION,
    QUALITY_TIER_MAX,
    QUALITY_TIER_MIN,
    ProvenanceMixin,
    TimestampMixin,
)
from .queue import FetchAttempt, QueueTask
from .robots import RobotsCacheEntry
from .runs import EnrichmentItem, Notification, Report, Run
from .source import (
    EMBEDDING_DIM,
    BoilerplateLine,
    Chunk,
    ChunkTopics,
    Figure,
    HostScore,
    PageLine,
    Source,
    TranslationLookup,
)
from .views import SavedView

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EMBEDDING_DIM",
    "QUALITY_TIER_MAX",
    "QUALITY_TIER_MIN",
    "Agent",
    "Area",
    "AreaBridge",
    "AreaBuild",
    "AreaMember",
    "AgentToken",
    "AttributeDefinition",
    "AttributeValue",
    "BoilerplateLine",
    "Chunk",
    "ChunkTopics",
    "Edge",
    "EnrichmentItem",
    "Entity",
    "FetchAttempt",
    "FetchPolicy",
    "Figure",
    "GazetteerTerm",
    "HostScore",
    "Notification",
    "PageLine",
    "Observation",
    "ProvenanceMixin",
    "QueueTask",
    "Report",
    "RobotsCacheEntry",
    "SavedView",
    "Run",
    "Source",
    "BudgetConfig",
    "Grant",
    "MergeLog",
    "GrantAudit",
    "ScheduledJob",
    "SteeringLog",
    "SteeringProposal",
    "TimestampMixin",
    "TopicConfig",
    "TranslationLookup",
]
