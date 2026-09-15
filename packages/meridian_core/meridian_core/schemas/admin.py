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

from pydantic import BaseModel, ConfigDict, Field

from .enums import GazetteerEntityType
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
