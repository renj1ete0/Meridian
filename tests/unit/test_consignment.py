"""Consignment eligibility (task P1-38, docs/spec/external-acquisition.md §3).

No database. The rule is a pure function on purpose — it is the part of that
spec with a security argument behind it, it needs neither the table nor the API
to be correct, and it is worth having tested before anything can call it.

Most of these are rejection tests, because the failure mode is not "a URL that
should have been consigned was not". It is a URL published to a third party that
should never have left this system, and nothing downstream of here would notice.
"""

from __future__ import annotations

import pytest

from meridian_core.consignment import (
    ELIGIBLE_STATUSES,
    NEVER_ELIGIBLE,
    consignment_eligible,
)
from meridian_core.models.queue import FETCH_OUTCOME
from meridian_core.queueing import TASK_ABANDON

# --------------------------------------------------------------------------
# The two that are never eligible
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", sorted(NEVER_ELIGIBLE))
@pytest.mark.parametrize("status", [None, 200, 403, 451, 500])
@pytest.mark.parametrize("allow_oversize", [False, True])
def test_the_absolute_refusals_hold_under_every_argument(
    outcome: str, status: int | None, allow_oversize: bool
) -> None:
    """Not a default. Every combination of every argument, because "absolute"
    means there is no way to configure past it — and the only way to show that
    is to try.

    `robots_denied`: routing a refused request through a third party is the same
    crawl with the conduct removed (§14.2). `unsafe_target`: publishing an
    address `netguard` rejected asks an external service to fetch the operator's
    own network and post the result back.
    """
    verdict = consignment_eligible(outcome, status, allow_oversize=allow_oversize)

    assert verdict.eligible is False
    assert "never eligible" in verdict.reason


def test_the_refusal_is_checked_before_anything_else() -> None:
    """Ordering, stated as a test.

    A `robots_denied` carrying a 403 must be refused *as robots*, not accepted
    as a 403. Reordering the branches would make it eligible and would look like
    a tidy-up.
    """
    assert consignment_eligible("robots_denied", 403).eligible is False


# --------------------------------------------------------------------------
# Completeness: nothing falls through unclassified
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", sorted(FETCH_OUTCOME.enums))
def test_every_fetch_outcome_gets_a_verdict(outcome: str) -> None:
    """Drift, over the database enum rather than a list written here.

    A new outcome must be classified deliberately. It will be added for a
    reason, and "should this be publishable to a third party" is not a question
    whose answer should be inherited from whichever branch happens to catch it.
    """
    verdict = consignment_eligible(outcome, 403 if outcome == "http_error" else None)
    assert isinstance(verdict.eligible, bool)
    assert verdict.reason


def test_an_unknown_outcome_is_refused() -> None:
    """Fail closed, and deliberately the opposite of `queue_disposition`, which
    retries what it cannot classify. There, the forgiving default is bounded by
    `max_retries`; here it publishes a URL to somebody outside this system."""
    assert consignment_eligible("something_nobody_has_written_yet").eligible is False


def test_only_abandoned_outcomes_can_be_eligible() -> None:
    """A URL that is still being retried has not finished failing.

    Consigning one would mean two fetchers chasing the same page, and the
    external copy arriving would race the retry that was already going to
    succeed.
    """
    retried = {"timeout", "connection_error"}
    for outcome in retried:
        assert consignment_eligible(outcome).eligible is False

    eligible = {
        outcome
        for outcome in FETCH_OUTCOME.enums
        if outcome != "http_error" and consignment_eligible(outcome, allow_oversize=True).eligible
    }
    assert eligible <= TASK_ABANDON, (
        f"eligible but not abandoned: {sorted(eligible - TASK_ABANDON)}"
    )


# --------------------------------------------------------------------------
# What is eligible
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(ELIGIBLE_STATUSES))
def test_a_refusal_to_serve_this_client_is_eligible(status: int) -> None:
    """401, 402, 403, 451 all describe content that exists and is being withheld
    — from this client, for payment, or in this jurisdiction. Each is a case a
    different fetcher may legitimately resolve."""
    assert consignment_eligible("http_error", status).eligible is True


@pytest.mark.parametrize("status", [404, 410])
def test_a_page_that_is_gone_is_not_eligible(status: int) -> None:
    """The origin answering correctly. Nobody else can fetch what is not there,
    and a feed full of 404s wastes whatever the external actor costs."""
    assert consignment_eligible("http_error", status).eligible is False


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_statuses_are_not_eligible(status: int) -> None:
    """These retry rather than abandon, so they should not reach here at all —
    and must not become eligible if a future disposition change lets them."""
    assert consignment_eligible("http_error", status).eligible is False


def test_a_missing_status_is_not_eligible() -> None:
    """`http_error` with no code is a failure that never got far enough to be a
    refusal."""
    assert consignment_eligible("http_error", None).eligible is False


# --------------------------------------------------------------------------
# The one knob
# --------------------------------------------------------------------------


def test_oversize_needs_an_explicit_decision() -> None:
    """The consigned copy is exactly as large as the one that was refused, so
    the size limit does not stop applying because somebody else did the
    fetching."""
    assert consignment_eligible("too_large").eligible is False
    assert consignment_eligible("too_large", allow_oversize=True).eligible is True


def test_the_opt_in_widens_nothing_else() -> None:
    """A single boolean that grew into "consign more, generally" is precisely
    how the two absolute refusals would eventually become reachable."""
    widened = {
        outcome
        for outcome in FETCH_OUTCOME.enums
        if consignment_eligible(outcome, allow_oversize=True).eligible
        and not consignment_eligible(outcome).eligible
    }

    assert widened == {"too_large"}


def test_a_blocked_domain_is_not_eligible() -> None:
    """The operator's own policy refused this domain. Consigning it is a way
    around a decision they made, which is the same shape of problem as the
    robots case with a different party being overridden."""
    assert consignment_eligible("blocked").eligible is False
