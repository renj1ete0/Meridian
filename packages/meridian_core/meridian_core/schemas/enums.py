"""Literal aliases mirrored from the models' CHECK-constrained columns.

Each ``constrained(...)`` call in ``meridian_core.models`` (see ``mixins.py``)
builds a SQLAlchemy ``Enum`` whose ``.enums`` tuple is the single source of
truth for a value set. Importing that tuple into a ``Literal`` here — rather
than retyping the string literals in every DTO — means a status added to a
model's value set is automatically valid in the API schema too. Retyping them
would let the two drift, and an API accepting a status the database rejects
is exactly the boundary bug this package exists to prevent (task P0-10
requirement 4).
"""

from __future__ import annotations

from typing import Literal

from meridian_core.models.config import (
    AVAILABILITY,
    DOMAIN_STATUS,
    TOKEN_SCOPE,
    TOPIC_STATUS,
)
from meridian_core.models.gazetteer import GAZETTEER_ENTITY_TYPE, GAZETTEER_SOURCE
from meridian_core.models.graph import (
    ATTRIBUTE_SCOPE,
    ATTRIBUTE_STATUS,
    CERTAINTY,
    NODE_TYPE,
    STANCE,
)
from meridian_core.models.queue import (
    FETCH_OUTCOME,
    SEED_SOURCE,
    TASK_STATUS,
    TASK_TYPE,
)
from meridian_core.models.runs import (
    ENRICHMENT_TYPE,
    JOB_STATUS,
    NOTIFICATION_TYPE,
    RUN_STAGE,
    RUN_STATUS,
)
from meridian_core.models.source import OCR_TIER, RETENTION_TIER, SOURCE_TIER

# queue.py
TaskStatus = Literal[*TASK_STATUS.enums]
TaskType = Literal[*TASK_TYPE.enums]
SeedSource = Literal[*SEED_SOURCE.enums]
FetchOutcome = Literal[*FETCH_OUTCOME.enums]

# source.py
SourceTier = Literal[*SOURCE_TIER.enums]
RetentionTier = Literal[*RETENTION_TIER.enums]
OcrTier = Literal[*OCR_TIER.enums]

# graph.py
NodeType = Literal[*NODE_TYPE.enums]
Stance = Literal[*STANCE.enums]
Certainty = Literal[*CERTAINTY.enums]
AttributeScope = Literal[*ATTRIBUTE_SCOPE.enums]
AttributeStatus = Literal[*ATTRIBUTE_STATUS.enums]

# gazetteer.py
GazetteerEntityType = Literal[*GAZETTEER_ENTITY_TYPE.enums]
GazetteerSource = Literal[*GAZETTEER_SOURCE.enums]

# config.py
TopicStatus = Literal[*TOPIC_STATUS.enums]
DomainStatus = Literal[*DOMAIN_STATUS.enums]
TokenScope = Literal[*TOKEN_SCOPE.enums]
AgentAvailability = Literal[*AVAILABILITY.enums]

# runs.py
RunStage = Literal[*RUN_STAGE.enums]
RunStatus = Literal[*RUN_STATUS.enums]
JobStatus = Literal[*JOB_STATUS.enums]
EnrichmentType = Literal[*ENRICHMENT_TYPE.enums]
NotificationType = Literal[*NOTIFICATION_TYPE.enums]
