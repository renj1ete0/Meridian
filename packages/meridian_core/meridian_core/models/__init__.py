"""SQLAlchemy models. Services import from here; they never define their own.

Importing this package registers every table on ``Base.metadata``, which is what
Alembic autogenerate reflects against.
"""

from .config import (
    Agent,
    AgentToken,
    BudgetConfig,
    FetchPolicy,
    Grant,
    ScheduledJob,
    SteeringLog,
    TopicConfig,
)
from .gazetteer import GazetteerTerm
from .graph import AttributeDefinition, AttributeValue, Edge, Entity, Observation
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
from .source import EMBEDDING_DIM, Chunk, Figure, Source
from .views import SavedView

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EMBEDDING_DIM",
    "QUALITY_TIER_MAX",
    "QUALITY_TIER_MIN",
    "Agent",
    "AgentToken",
    "AttributeDefinition",
    "AttributeValue",
    "Chunk",
    "Edge",
    "EnrichmentItem",
    "Entity",
    "FetchAttempt",
    "FetchPolicy",
    "Figure",
    "GazetteerTerm",
    "Notification",
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
    "ScheduledJob",
    "SteeringLog",
    "TimestampMixin",
    "TopicConfig",
]
