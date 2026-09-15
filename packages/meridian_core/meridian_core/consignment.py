"""Which failures may be handed to someone else (task P1-38).

Specified in [docs/spec/external-acquisition.md](../../../docs/spec/external-acquisition.md)
§3. This module is that section's §3, and deliberately nothing else: no table,
no lease, no API. It is the part with a security argument behind it, it needs
none of the rest to be correct, and it is worth having tested before anything
can call it.

**The question is not "did this fail".** `queue_disposition` already abandons
eight outcomes, and they are not interchangeable. Three classes of thing sit
behind the ones worth consigning — a bot wall on a page that is public, a login
the operator legitimately holds, a format or size this crawler refuses — and
what they share is that the content exists and this fetcher cannot have it.
Everything else abandoned is a correct answer about a URL, and asking a third
party to try harder produces nothing.

**Two outcomes are never eligible, and the line is absolute rather than a
default.**

``robots_denied``
    The site asked not to be crawled. Routing that request through a third party
    so the refusal does not apply is not a technical workaround; it is the same
    crawl with the conduct removed. §14.2 commits this system to identifying
    itself and honouring robots, and a consignment path that can launder a
    robots refusal makes that commitment decorative. It is refused before any
    other check runs and no argument to this function can reach past it.

``unsafe_target``
    `netguard` refused the address because it resolved to a private range, a
    cloud metadata endpoint, or a scheme this crawler does not speak. Publishing
    it in a feed asks an external service to fetch the operator's own network
    and post the result back — an SSRF with a cooperating victim. `P1-20` and
    `P1-24` exist to make that impossible and consignment must not reopen it.

**Unknown outcomes are refused.** The opposite of `queue_disposition`, which
retries what it cannot classify because retrying is bounded and dropping a URL
is not. Here the forgiving default is the dangerous one: a new outcome nobody
has thought about should not become publishable by being new.
"""

from __future__ import annotations

import dataclasses

#: Refused before anything else, and not reachable by configuration.
#: See the module docstring — both have an argument behind them that no
#: deployment preference outranks.
NEVER_ELIGIBLE = frozenset({"robots_denied", "unsafe_target"})

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

#: HTTP statuses that mean "not for you" rather than "not here" or "not now".
#: 402 and 451 are included because both describe content that exists and is
#: being withheld — payment required, and withheld for legal reasons in this
#: jurisdiction — which is exactly the case a different fetcher may resolve.
ELIGIBLE_STATUSES = frozenset({401, 402, 403, 451})


@dataclasses.dataclass(frozen=True)
class Eligibility:
    """Whether a failed task may be consigned, and why.

    The reason is not decoration. An operator looking at a feed that is emptier
    than expected needs to know whether the URLs were refused on conduct
    grounds, on policy grounds, or because nobody has opted into their size —
    and those want three different responses.
    """

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

    ``allow_oversize`` is the operator's opt-in for `too_large`. It is the only
    knob, and it deliberately cannot widen anything else — a single boolean that
    turned into "consign more" generally is how the two absolute refusals would
    eventually be reachable.
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
        # 404 and 410 are the origin answering correctly about a URL that is
        # gone; 5xx and 429 are transient and were retried rather than
        # abandoned, so they do not reach here in the normal path and must not
        # become eligible if they ever do.
        return Eligibility(False, f"http {status_code} is an answer, not a refusal")

    # `blocked` lands here too, and should: the operator's own policy refused
    # this domain, and consigning it is a way around a decision they made.
    return Eligibility(False, f"{outcome} is not a refusal to serve this client")
