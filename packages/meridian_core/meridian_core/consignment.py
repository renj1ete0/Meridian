"""Which failures may be handed to someone else (task P1-38).

Implements docs/spec/external-acquisition.md §3 and nothing else. `robots_denied`,
`robots_unreachable` and `unsafe_target` are never eligible, whatever the arguments, and
an unknown outcome is refused. The reasons are in the spec, §3.1 and §3.3.
"""

from __future__ import annotations

import dataclasses

#: Refused before anything else, and not reachable by configuration (spec §3.1).
NEVER_ELIGIBLE = frozenset({"robots_denied", "robots_unreachable", "unsafe_target"})

#: A refusal to serve *this client*, for content that exists.
ELIGIBLE_OUTCOMES = frozenset(
    {
        "content_type_rejected",  # we will not read it; something else may
        "parse_error",  # the bytes arrived and made no sense
        "too_many_redirects",  # often a session or consent wall a browser clears
    }
)

#: Eligible only when the operator has accepted the size. The consigned copy is
#: just as large as the one refused, so this is a decision rather than a default.
OPT_IN_OUTCOMES = frozenset({"too_large"})

#: HTTP statuses that mean "not for you" rather than "not here" or "not now" (spec §3.3).
ELIGIBLE_STATUSES = frozenset({401, 402, 403, 451})


@dataclasses.dataclass(frozen=True)
class Eligibility:
    """Whether a failed task may be consigned, and the reason an operator acts on."""

    eligible: bool
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.eligible


def consignment_eligible(
    outcome: str,
    status_code: int | None = None,
    *,
    allow_oversize: bool = False,
) -> Eligibility:
    """Whether a URL that ended in ``outcome`` may be handed to an external actor.

    ``allow_oversize`` is the operator's opt-in for `too_large`, and widens nothing else.
    """
    # First, and before every other branch, so that no later condition and no
    # future argument can reach past it.
    if outcome in NEVER_ELIGIBLE:
        return Eligibility(False, f"{outcome} is never eligible for consignment")

    if outcome in ELIGIBLE_OUTCOMES:
        return Eligibility(True, outcome)

    if outcome in OPT_IN_OUTCOMES:
        if allow_oversize:
            return Eligibility(True, f"{outcome}, accepted by the operator")
        return Eligibility(False, f"{outcome} needs an explicit size decision")

    if outcome == "http_error":
        if status_code in ELIGIBLE_STATUSES:
            return Eligibility(True, f"http {status_code}")
        # 404/410 are correct answers; 5xx/429 were retried, not abandoned.
        return Eligibility(False, f"http {status_code} is an answer, not a refusal")

    # `blocked` lands here too, and should: the operator's own policy refused
    # this domain, and consigning it is a way around a decision they made.
    return Eligibility(False, f"{outcome} is not a refusal to serve this client")
