"""Literal aliases mirrored from the models' CHECK-constrained columns (task P0-10).

Each ``Literal`` is built from the constraint's own ``.enums`` tuple, never retyped. See
docs/reference/data-model.md#boundary-schemas.
"""

from __future__ import annotations

from typing import Literal

from meridian_core.models.chat import CHAT_ROLE
from meridian_core.models.config import (
    AVAILABILITY,
    DOMAIN_STATUS,
    GRANT_PROFILE,
    PROPOSAL_KIND,
    PROPOSAL_STATUS,
    SUBJECT_KIND,
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
from meridian_core.models.mixins import TRUST_STATE
from meridian_core.models.queue import (
    FETCH_OUTCOME,
    SEED_MECHANISM,
    SEED_SOURCE,
    TASK_STATUS,
    TASK_TYPE,
)
from meridian_core.models.robots import ROBOTS_OUTCOME
from meridian_core.models.runs import (
    ENRICHMENT_TYPE,
    JOB_STATUS,
    NOTIFICATION_TYPE,
    RUN_STAGE,
    RUN_STATUS,
)
from meridian_core.models.source import DOC_KIND, OCR_TIER, RETENTION_TIER, SOURCE_TIER

# queue.py
TaskStatus = Literal[*TASK_STATUS.enums]
TaskType = Literal[*TASK_TYPE.enums]
SeedSource = Literal[*SEED_SOURCE.enums]
SeedMechanism = Literal[*SEED_MECHANISM.enums]
FetchOutcome = Literal[*FETCH_OUTCOME.enums]

# source.py
SourceTier = Literal[*SOURCE_TIER.enums]
RetentionTier = Literal[*RETENTION_TIER.enums]
OcrTier = Literal[*OCR_TIER.enums]
DocKind = Literal[*DOC_KIND.enums]

# graph.py
NodeType = Literal[*NODE_TYPE.enums]
Stance = Literal[*STANCE.enums]
Certainty = Literal[*CERTAINTY.enums]
AttributeScope = Literal[*ATTRIBUTE_SCOPE.enums]
AttributeStatus = Literal[*ATTRIBUTE_STATUS.enums]

# gazetteer.py
GazetteerEntityType = Literal[*GAZETTEER_ENTITY_TYPE.enums]
GazetteerSource = Literal[*GAZETTEER_SOURCE.enums]

#: How the last read of an origin's robots.txt ended (`P1-29`).
RobotsOutcome = Literal[*ROBOTS_OUTCOME.enums]

# config.py
TopicStatus = Literal[*TOPIC_STATUS.enums]
DomainStatus = Literal[*DOMAIN_STATUS.enums]
TokenScope = Literal[*TOKEN_SCOPE.enums]

#: Who is on the other end of a grant (`P3-06`). A person arrives through SSO
#: and a machine through a service token, and they are audited differently.
SubjectKind = Literal[*SUBJECT_KIND.enums]

#: A named set of tools, never a free-form list — a per-person tool list is how
#: somebody ends up holding a write tool nobody remembers granting (§3).
GrantProfile = Literal[*GRANT_PROFILE.enums]
AgentAvailability = Literal[*AVAILABILITY.enums]

#: What a steering proposal changes, and where it is in its life (`P6-38`).
ProposalKind = Literal[*PROPOSAL_KIND.enums]
ProposalStatus = Literal[*PROPOSAL_STATUS.enums]

#: Who wrote a chat message (`P6-06`).
ChatRole = Literal[*CHAT_ROLE.enums]

# mixins.py — shared by `sources` and `fetch_policy` (`P4-14`)
TrustState = Literal[*TRUST_STATE.enums]

# runs.py
RunStage = Literal[*RUN_STAGE.enums]
RunStatus = Literal[*RUN_STATUS.enums]
JobStatus = Literal[*JOB_STATUS.enums]
EnrichmentType = Literal[*ENRICHMENT_TYPE.enums]
NotificationType = Literal[*NOTIFICATION_TYPE.enums]


#: How a scheduled job's last run ended (`P5-06`). Distinct from `JobStatus`,
#: which is an enrichment/report job's lifecycle — this one only ever describes
#: a run that has already finished, so there is no `queued` or `running`.
JobRunStatus = Literal["ok", "failed", "timeout"]

#: Which retrieval arm produced a hit (`P2-06`, `P2-18`). A Literal so the value set
#: crosses into the web package's types.
SearchArm = Literal["lexical", "vector"]

#: What a hit's `page_or_offset` counts (§5.3, `P2-18`).
PageUnit = Literal["page", "offset"]

#: Whether the crawl is alive (`P6-25`), judged at read time by `crawlhealth.judge`.
#: ``waiting`` (all pending work in backoff) is not ``stalled``; see
#: docs/reference/data-model.md#crawl-health.
LivenessState = Literal["crawling", "stalled", "waiting", "idle"]
