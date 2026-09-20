"""How fast a document's relevance decays, by what kind of document it is
(task `P2-20`, §9).

§9 already says ageing is topic-dependent — "a 2014 finding on AV public
acceptance is near-worthless, while a 2014 finding on pedestrian thermal
comfort remains sound" — and the same is true across `source_tier`. Press and
informal material rots in months. Peer-reviewed work often does not rot at all.
A policy page supersedes rather than ages.

**A single "newer is better" multiplier is the wrong shape**, and avoiding it
is the point of this module rather than a caveat on it. Applied globally it
buries the foundational papers, which for a corpus with an academic spine is
precisely the failure that matters — the 1987 paper everything cites would rank
below a blog post about it.

So: **a half-life per source tier, overridable per topic**, applied as a decay
on the fused score rather than as a filter. A filter removes; a decay reorders,
and reordering is what "this is probably less current" actually means.

**An undated document is neither old nor new.** Around a third of crawled pages
have no extractable date, and whichever default you pick is wrong for the other
kind — treating them as new floats every undated blog to the top, treating them
as old buries every undated standards document. Decay applies only where a date
exists, and the result says so, exactly as the interface already prints "no
date" rather than a blank.

**The adjustment is shown, not applied silently.** A result quietly demoted is
a result the reader cannot audit, which is the opposite of what this corpus is
for. The hit carries its age and the factor applied, the way it already carries
its tier and its per-arm ranks.
"""

from __future__ import annotations

import datetime as dt
import math

from .logging import get_logger

log = get_logger(__name__)

#: Half-life in days, by source tier. The number of days after which a document
#: of that kind is worth half as much, all else equal.
#:
#: `None` means no decay at all, and `peer_reviewed` has it for the reason
#: above: the foundational paper is the one this would otherwise bury. The rest
#: are ordered by how quickly the *claim* stops being current, not by how
#: quickly the page changes — a government policy page supersedes rather than
#: ages, so it sits well above press without being exempt.
HALF_LIFE_DAYS: dict[str, int | None] = {
    "peer_reviewed": None,
    "academic": 3650,
    "government": 1825,
    "institutional": 1825,
    "industry": 730,
    "press": 365,
    "informal": 180,
}

#: The floor a decayed score cannot fall through. Decay reorders; it must not
#: effectively delete. Without a floor a ten-year-old press article scores
#: within rounding of zero and drops out of the result set entirely, which is a
#: filter wearing a decay's clothes.
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

    A topic override wins over the tier, because §9's example is a topic one:
    the same tier ages differently depending on what it is about. Where a
    document carries several topics the **longest** half-life wins — a document
    that is partly about something slow-moving should not be aged as though it
    were only about the fast-moving half, and the conservative direction here
    is the one that does not bury things.
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

    A future date returns 0 rather than a negative age. Publication dates that
    are slightly ahead are common — an embargo, a timezone, a journal issue
    dated next month — and a negative age would *boost* the document, which is
    a scraped metadata field being allowed to outrank the corpus.
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

    Returns 1.0 for an undated document — not a guess in either direction. That
    is the single most important line here: a default either way is wrong for
    a third of the corpus.
    """
    age = age_in_days(published, today=today)
    if age is None:
        return 1.0

    half_life = half_life_for(source_tier, topics=topics, overrides=overrides)
    if half_life is None or half_life <= 0:
        return 1.0

    return max(MIN_FACTOR, math.pow(0.5, age / half_life))
