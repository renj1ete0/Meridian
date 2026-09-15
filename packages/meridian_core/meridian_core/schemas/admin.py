"""DTOs for `/api/admin/*` (task P6-13, spec §12.6, §5.6).

Here rather than in `services/api` for the reason every other DTO is: services
import schemas, they do not define them (AGENTS.md layout).

The shape worth explaining is :class:`GazetteerRowRead`, which wraps the row
rather than widening it. Whether a term will actually *load* is not a column —
it depends on every other approved row, because a surface form two rows share is
withheld from the matcher whichever row you are looking at. Flattening it into
:class:`GazetteerTermRead` would put a derived field beside stored ones with no
way to tell them apart, and the drift test that checks every DTO field has a
column behind it would have to be weakened to allow it.

It is here at all because approving a term that then silently never matches is
the failure this whole surface exists to prevent. A curator who approves "BCA"
and never sees it in an extraction has no way to discover that a second row
claims the same string.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from .config import FetchPolicyRead, SteeringLogRead, TopicConfigRead
from .enums import DomainStatus, GazetteerEntityType, TopicStatus
from .gazetteer import GazetteerTermRead


class GazetteerRowRead(BaseModel):
    """One row, plus what the matcher would do with it."""

    term: GazetteerTermRead

    #: Whether this term currently contributes patterns to the `EntityRuler`.
    #: False for everything unapproved, and also for approved terms that are
    #: withheld — which is the case worth surfacing, because nothing else shows
    #: it and the symptom is a curated term that never matches anything.
    will_load: bool

    #: `ambiguous`, `collision`, `unapproved`, `rejected` — or None when the term
    #: loads. Named rather than boolean because the four have different fixes: a
    #: collision needs one of the two rows changed, ambiguity is a decision to
    #: leave the mention to the resolver, and the other two are just the queue.
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


class GazetteerTermEdit(BaseModel):
    """Corrections a curator makes before deciding.

    Every field optional, and `None` means "leave it" rather than "clear it".
    The two are different edits and a PATCH that cannot express the difference
    would make clearing a jurisdiction impossible or make every edit clear it —
    so clearing is done by sending the empty value the column takes, and
    omitting the key is what leaves it alone.
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

    ``weight`` is what is stored. ``share`` is what a draw actually uses once a
    boost and the floors and ceilings have been applied, and the two differ
    exactly when something interesting is happening — which is why both are
    shown rather than one being computed away.
    """

    topic: TopicConfigRead

    #: ``weight × boost_factor`` while a boost is running, otherwise ``weight``.
    #: The input to normalisation, not the output.
    effective_weight: float

    #: The fraction of seeds this topic draws. 0 for anything not `active`,
    #: which is the honest answer rather than an absent field: a paused topic
    #: keeps a weight and draws nothing, and showing only the weight would read
    #: as it still competing.
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

    ``reason`` is optional here and never optional in the log — the server
    writes a factual description when none is given. §10.1 requires a reason on
    every change, and requiring a person to type one before moving a slider
    produces a column full of the word "update".
    """

    model_config = ConfigDict(extra="forbid")

    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    floor: float | None = Field(default=None, ge=0.0, le=1.0)
    ceiling: float | None = Field(default=None, ge=0.0, le=1.0)
    pinned: bool | None = None
    status: TopicStatus | None = None
    boost_factor: float | None = Field(default=None, gt=0.0)
    boost_expires_at: dt.datetime | None = None
    reason: str | None = None


class TopicAdd(BaseModel):
    """§10.2's `add_topic`: insert and re-normalise so the set still sums to 1.0."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=120)
    floor: float = Field(default=0.05, ge=0.0, le=1.0)
    ceiling: float = Field(default=0.60, ge=0.0, le=1.0)
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

    Three layers shown separately on purpose. `settings` is what somebody set
    *here*; `resolved` is what a fetch actually gets after the global row and the
    file defaults are merged under it; and the learned fields are neither — they
    are observations. An operator looking at a domain going through a browser
    needs to know which of the three put it there, because only one of them is
    something they can change on this screen.
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
