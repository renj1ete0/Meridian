"""DTOs for `/api/admin/*` (task P6-13, spec §12.6, §5.6).

:class:`GazetteerRowRead` wraps a term rather than widening it, because whether it loads
is not a column. See docs/reference/data-model.md#admin-dtos.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import FetchPolicyRead, SteeringLogRead, TopicConfigRead
from .enums import DomainStatus, GazetteerEntityType, TaskType, TopicStatus
from .gazetteer import GazetteerTermRead
from .queue import QueueTaskRead


class GazetteerRowRead(BaseModel):
    """One row, plus what the matcher would do with it."""

    term: GazetteerTermRead

    #: Whether this term currently contributes patterns to the `EntityRuler`. False for
    #: everything unapproved, and for approved terms that are withheld.
    will_load: bool

    #: `ambiguous`, `collision`, `unapproved`, `rejected`, or None when the term loads.
    #: Named because each has a different fix.
    withheld_reason: str | None = None

    #: The other rows claiming a surface form this one claims. Empty unless
    #: `withheld_reason` is `collision`; a curator cannot resolve a collision
    #: without being told what it collided with.
    collides_with: list[int] = Field(default_factory=list)


class GazetteerQueueRead(BaseModel):
    """A page of the queue, and what the filter is not showing.

    Counts cover every state, for `P6-08`'s reason: a filtered list whose counts
    are also filtered cannot tell a reader that the thing they came for is one
    tab over, and they conclude it is not there.
    """

    rows: list[GazetteerRowRead]
    limit: int
    offset: int
    has_more: bool

    pending: int
    approved: int
    rejected: int
    # `B-209`: how many terms in this state the search matched; None when nothing is searched.
    matched: int | None = None


class GazetteerTermEdit(BaseModel):
    """Corrections a curator makes before deciding.

    Every field optional, and `None` means "leave it". Clear a field by sending the empty
    value its column takes.
    """

    model_config = ConfigDict(extra="forbid")

    canonical: str | None = Field(default=None, min_length=1)
    aliases: list[str] | None = None
    entity_type: GazetteerEntityType | None = None
    jurisdiction: str | None = None
    ambiguous: bool | None = None


# ---------------------------------------------------------------------------
# Topics (task P6-12, spec §10)
# ---------------------------------------------------------------------------


class TopicRowRead(BaseModel):
    """One topic, plus the two numbers that are not columns.

    ``weight`` is what is stored; ``share`` is what a draw uses after the boost, floors
    and ceilings.
    """

    topic: TopicConfigRead

    #: ``weight × boost_factor`` while a boost is running, otherwise ``weight``.
    #: The input to normalisation, not the output.
    effective_weight: float

    #: The fraction of seeds this topic draws; 0 for anything not `active`.
    share: float

    #: Whether a boost is running *now*. Derived from the expiry rather than
    #: from the factor being set, because an expired boost is left on the row on
    #: purpose — it is the audit trail — and simply stops counting.
    boost_active: bool


class TopicsRead(BaseModel):
    rows: list[TopicRowRead]

    #: What the active shares add up to. Always 1.0 when any topic is active,
    #: and published rather than assumed: it is one number, and it is the claim
    #: the whole screen rests on.
    sums_to: float


class TopicEdit(BaseModel):
    """A steering change. Every field optional; omitted means "leave it".

    Without a ``reason`` the server writes a factual one, since the log always has one
    (§10.1).
    """

    model_config = ConfigDict(extra="forbid")

    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    floor: float | None = Field(default=None, ge=0.0, le=1.0)
    ceiling: float | None = Field(default=None, ge=0.0, le=1.0)
    pinned: bool | None = None
    status: TopicStatus | None = None
    boost_factor: float | None = Field(default=None, gt=0.0)
    boost_expires_at: dt.datetime | None = None
    #: What the topic is about (`P2-21`). Not a steering change — it moves no
    #: share — but it moves every source's labels, so it is logged like one.
    #: An empty string clears it.
    description: str | None = Field(default=None, max_length=1000)
    reason: str | None = None


class TopicAdd(BaseModel):
    """§10.2's `add_topic`: insert and re-normalise so the set still sums to 1.0."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=120)
    floor: float = Field(default=0.05, ge=0.0, le=1.0)
    ceiling: float = Field(default=0.60, ge=0.0, le=1.0)
    description: str | None = Field(default=None, max_length=1000)
    reason: str | None = None


class SteeringLogPage(BaseModel):
    """Why the vector is where it is (§10.1)."""

    entries: list[SteeringLogRead]
    limit: int
    has_more: bool


# ---------------------------------------------------------------------------
# Fetch policy (task P6-22, spec §6.4, §13.2)
# ---------------------------------------------------------------------------


class FetchPolicyRowRead(BaseModel):
    """One domain's row, what it resolves to, and what the crawl learned.

    `settings` is what was set here; `resolved` is what a fetch gets after the global row
    and file defaults are merged under it; the learned fields are observations.
    """

    policy: FetchPolicyRead

    #: The effective policy, merged. Every key, including the ones this screen
    #: refuses to edit — showing only the editable ones would misrepresent what
    #: the crawler is actually doing.
    resolved: dict

    #: Keys set on this row rather than inherited. What a UI needs to show the
    #: difference between "this domain is slow" and "everything is slow".
    overridden: list[str]


class FetchPolicyPage(BaseModel):
    rows: list[FetchPolicyRowRead]
    limit: int
    offset: int
    has_more: bool

    #: Counts by status, unfiltered — `P6-08`'s rule: a filtered list whose
    #: counts are also filtered cannot tell an operator that the blocked domain
    #: they came for is one tab over.
    active: int
    paused: int
    blocked: int


class FetchPolicyEdit(BaseModel):
    """Changes to one domain's row.

    `settings` is merged into what is there rather than replacing it, so an edit
    to one field cannot silently drop the rest — a full-replacement PATCH from a
    form that rendered only some keys is how a delay somebody tuned disappears.
    """

    model_config = ConfigDict(extra="forbid")

    settings: dict | None = None
    status: DomainStatus | None = None
    note: str | None = None

    #: Required when editing the global `*` row, which changes every domain the
    #: crawl touches. Not a dialog — a dialog is a client's promise, and this is
    #: the one edit on this surface whose blast radius is the whole crawl.
    confirm: bool = False


# ---------------------------------------------------------------------------
# The budget (tasks `P4-10`, `P4-13`, §16)
# ---------------------------------------------------------------------------


class BudgetRead(BaseModel):
    """The caps, what the month has spent, and whether a run may start.

    `ready` (every cap set, and the month not at its ceiling) is the server's verdict; a
    client never derives it. See docs/reference/data-model.md#admin-dtos.
    """

    max_tokens_per_run: int | None = None
    max_seeds_per_run: int | None = None
    monthly_cost_ceiling_usd: float | None = None

    #: Spent so far this calendar month, in USD, across every run that started
    #: in it — including one still running.
    month_to_date_usd: float

    #: Whether `check_can_start_run` would allow a run right now.
    ready: bool

    #: Which caps are unset, so the screen can mark the fields rather than
    #: showing one message about the form as a whole. Empty when all are set.
    missing: list[str] = Field(default_factory=list)

    updated_at: dt.datetime | None = None
    updated_by: str | None = None


class BudgetEdit(BaseModel):
    """Set or change the caps. Every field optional; omitted means "leave it".

    `null` is not omitted: it clears a cap, which stops runs starting. The caller tells
    them apart with `exclude_unset`. Bounds mirror the table's CHECKs, for a 422.
    """

    max_tokens_per_run: int | None = Field(default=None, gt=0)
    max_seeds_per_run: int | None = Field(default=None, gt=0)
    monthly_cost_ceiling_usd: float | None = Field(default=None, gt=0)


# ---------------------------------------------------------------------------
# The first run (task `B-07`, scaffold §1.7, §15 phase 0)
# ---------------------------------------------------------------------------


class FirstRunRead(BaseModel):
    """Whether this install has started, and what it would start with.

    `is_first_run` means no sources yet (not no seeds: `make seed` queues those at first
    boot), computed by the server. See docs/reference/data-model.md#admin-dtos.
    """

    is_first_run: bool

    #: Documents in the corpus. Zero is the condition above.
    sources: int

    #: Cold-start seeds still waiting to be fetched, which a person can still change.
    pending_seeds: list[QueueTaskRead] = Field(default_factory=list)

    #: How many pending seeds are already claimed or attempted, and so past changing.
    seeds_in_flight: int = 0


class SeedCreate(BaseModel):
    """One cold-start seed, added by hand from the interface.

    `topic` is optional and usually wrong to omit: a seed with no topic is
    crawled and then belongs to whatever the topic matcher makes of it, which
    for an authority root is often nothing.
    """

    url_or_query: str = Field(min_length=1)
    task_type: TaskType = "url"
    topic: str | None = None
    #: Cold-start seeds run first, the same default `scripts/seed.py` uses.
    priority: int = 100
    #: Why, for the steering audit (`B-55`). A seed is steering — it changes
    #: what the crawl acquires — so it is logged beside weights and boosts.
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# The agent registry and run history (task `P6-23`, §11.3, §11.10, §11.12)
# ---------------------------------------------------------------------------


class AgentRowRead(BaseModel):
    """One registry row, as Admin shows it.

    No key, only the variable that holds it (§11.11). `key_present` is read from the
    environment the API can see.
    """

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    provider: str
    model: str | None
    task_types: list[str] | None
    quality_tier: int | None
    cost_tier: str | None
    availability: str | None
    enabled: bool
    fallback_agent_id: str | None
    route_order: int | None
    endpoint: str | None
    api_key_env_var: str | None
    #: Whether the variable this row names is set where the API runs. Not
    #: whether the key *works* — that costs a request, and a screen that made
    #: one per row on every load would be a bill for looking.
    key_present: bool = False
    #: Why this row cannot be routed to, in the words routing would use. Empty means
    #: it can.
    blocked_by: list[str] = Field(default_factory=list)


class AgentsRead(BaseModel):
    rows: list[AgentRowRead]
    #: Task types no enabled agent declares. §11.3 routes by task type, so an
    #: undeclared one is a stage that defers every run — and the registry looks
    #: fine, because the absence is between rows rather than in one.
    unserved_tasks: list[str] = Field(default_factory=list)


class AgentEdit(BaseModel):
    """What Admin may change on an agent: whether it runs, and which model (`P6-06`).

    A `${VARIABLE}` model is read from .env. Endpoints and task types stay in
    `config/agents.yaml`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^\S(.*\S)?$")


class RunRowRead(BaseModel):
    """One synthesis run (§11.10).

    Counters rather than a verdict: §11.9 compares cost and volume per run week
    on week, and a row that said "successful" would hide a run that finished
    having written nothing.
    """

    model_config = ConfigDict(from_attributes=True)

    run_id: int
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    stage: str | None
    status: str
    agent_id: str | None
    tokens_used: int
    cost_usd: float | None
    edges_added: int
    tags_added: int
    seeds_emitted: int
    last_chunk_id: int | None
    heartbeat_at: dt.datetime | None
    #: Kept: a deferred run's reason is the whole point of the row, and §13.4
    #: makes deferral ordinary rather than exceptional.
    error: str | None


class RunsRead(BaseModel):
    rows: list[RunRowRead]
    total: int
    #: The run in flight, if any. Separate from the list because "is something
    #: happening right now" is the first question this screen is opened to
    #: answer, and scanning a status column for it is a worse way to find out.
    active: RunRowRead | None = None


# ---------------------------------------------------------------------------
# Admin as designed (task P6-28): deciding the gazetteer queue in bulk
# ---------------------------------------------------------------------------

#: The most terms one bulk decision may carry — the queue's own page ceiling,
#: so a whole page can be decided at once and nothing larger than a page can.
GAZETTEER_BULK_MAX = 200


class GazetteerBulkDecision(BaseModel):
    """One verdict for several terms: the per-term routes' three verdicts, same meaning."""

    model_config = ConfigDict(extra="forbid")

    term_ids: list[int] = Field(min_length=1, max_length=GAZETTEER_BULK_MAX)
    decision: Literal["approve", "reject", "restore"]


class GazetteerBulkRead(BaseModel):
    """Every decided row, each with what the matcher will now do with it.

    Rows rather than a count, because approving forty terms at once is exactly
    how two of them come to claim the same wording, and the collision is only
    visible on the rows.
    """

    rows: list[GazetteerRowRead]
