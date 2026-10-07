"""How fast a document's relevance decays, by what kind of document it is (task `P2-20`, §9).

A half-life per source tier, overridable per topic, applied as a decay on the fused
score, never as a filter. An undated document is not decayed, and the factor applied
is shown on the hit. See docs/features/source-quality.md#ageing.
"""

from __future__ import annotations

import datetime as dt
import math

from .logging import get_logger

log = get_logger(__name__)

#: Half-life in days, by source tier. The number of days after which a document
#: of that kind is worth half as much, all else equal.
#: `None` means no decay at all. See docs/features/source-quality.md#ageing.
HALF_LIFE_DAYS: dict[str, int | None] = {
    "peer_reviewed": None,
    "academic": 3650,
    "government": 1825,
    "institutional": 1825,
    "industry": 730,
    "press": 365,
    "informal": 180,
}

#: The floor a decayed score cannot fall through: decay reorders, it must not
#: effectively delete.
MIN_FACTOR = 0.25

#: Used when a tier is not in the table. Not `None` — an unrecognised tier
#: should decay like ordinary material rather than become exempt, because the
#: exemption is the valuable state and it should be granted deliberately.
DEFAULT_HALF_LIFE_DAYS = 730

__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "HALF_LIFE_DAYS",
    "MIN_FACTOR",
    "age_in_days",
    "decay_factor",
    "half_life_for",
]


def half_life_for(
    source_tier: str | None,
    *,
    topics: list[str] | None = None,
    overrides: dict[str, int | None] | None = None,
) -> int | None:
    """Half-life in days for this kind of document, or None for no decay.

    A topic override wins over the tier. Across several topics the **longest**
    half-life wins, the direction that does not bury things.
    """
    overrides = overrides or {}
    candidates = [overrides[topic] for topic in (topics or ()) if topic in overrides]
    if candidates:
        # `None` is "no decay", which is the longest of all — so it wins
        # outright rather than being compared as a number.
        if any(value is None for value in candidates):
            return None
        return max(value for value in candidates if value is not None)

    if source_tier in HALF_LIFE_DAYS:
        return HALF_LIFE_DAYS[source_tier]
    return DEFAULT_HALF_LIFE_DAYS


def age_in_days(published: dt.date | None, *, today: dt.date | None = None) -> int | None:
    """How old a document is, or None when it has no date.

    A future date returns 0: a negative age would *boost* the document.
    """
    if published is None:
        return None
    return max(0, ((today or dt.date.today()) - published).days)


def decay_factor(
    published: dt.date | None,
    source_tier: str | None,
    *,
    topics: list[str] | None = None,
    overrides: dict[str, int | None] | None = None,
    today: dt.date | None = None,
) -> float:
    """The multiplier to apply to a fused score. 1.0 means no adjustment.

    Exponential with the configured half-life, floored at `MIN_FACTOR`:

        factor = max(MIN_FACTOR, 0.5 ** (age_days / half_life_days))

    Returns 1.0 for an undated document: not a guess in either direction.
    """
    age = age_in_days(published, today=today)
    if age is None:
        return 1.0

    half_life = half_life_for(source_tier, topics=topics, overrides=overrides)
    if half_life is None or half_life <= 0:
        return 1.0

    return max(MIN_FACTOR, math.pow(0.5, age / half_life))
