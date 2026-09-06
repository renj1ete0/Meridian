"""What a fetch outcome means for the domain (task P1-05, spec §6.4).

Consecutive-failure blocking is the mechanism that stops one dead site eating
the crawl budget, and it is only as good as the question *which outcomes are
failures*. Getting that wrong is silent in both directions: too broad and a site
with a strict robots.txt gets auto-blocked, too narrow and a domain that has
been timing out for a week keeps being asked.
"""

from __future__ import annotations

import logging

import pytest

from meridian_core.models.queue import FETCH_OUTCOME
from meridian_core.policy import (
    DOMAIN_ALIVE,
    DOMAIN_NO_SIGNAL,
    DOMAIN_UNREACHABLE,
    domain_signal,
)


def test_every_fetch_outcome_is_classified() -> None:
    """Completeness probe: a new outcome must be given a meaning, not defaulted.

    Derived from ``FETCH_OUTCOME`` rather than a hardcoded list, so adding a
    value to the model's CHECK constraint fails here until someone decides
    whether it is evidence a domain is dead.
    """
    classified = DOMAIN_ALIVE | DOMAIN_UNREACHABLE | DOMAIN_NO_SIGNAL | {"http_error"}
    unclassified = set(FETCH_OUTCOME.enums) - classified
    assert not unclassified, f"unclassified fetch outcomes: {sorted(unclassified)}"


def test_the_three_sets_do_not_overlap() -> None:
    assert not DOMAIN_ALIVE & DOMAIN_UNREACHABLE
    assert not DOMAIN_ALIVE & DOMAIN_NO_SIGNAL
    assert not DOMAIN_UNREACHABLE & DOMAIN_NO_SIGNAL
    assert "http_error" not in DOMAIN_ALIVE | DOMAIN_UNREACHABLE | DOMAIN_NO_SIGNAL


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("success", "alive"),
        ("not_modified", "alive"),
        # The domain answered; this URL was simply not usable. Counting these
        # would auto-block a live site over five oversized PDFs in a row.
        ("too_large", "alive"),
        ("content_type_rejected", "alive"),
        ("parse_error", "alive"),
        ("timeout", "unreachable"),
        ("connection_error", "unreachable"),
        ("too_many_redirects", "unreachable"),
        ("decompression_bomb", "unreachable"),
        ("unsafe_target", "unreachable"),
        # No request went out, so there is nothing to conclude either way.
        ("robots_denied", "none"),
        ("blocked", "none"),
    ],
)
def test_outcomes_carry_the_meaning_they_should(outcome: str, expected: str) -> None:
    assert domain_signal(outcome) == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (404, "alive"),  # a missing page is the domain working correctly
        (403, "alive"),
        (410, "alive"),
        (429, "unreachable"),  # the server asking to be left alone
        (500, "unreachable"),
        (502, "unreachable"),
        (503, "unreachable"),
        (None, "unreachable"),  # an HTTP error with no status is not a healthy answer
    ],
)
def test_http_errors_are_split_by_status_code(status: int | None, expected: str) -> None:
    """A 404 and a 503 are the same outcome and opposite evidence."""
    assert domain_signal("http_error", status) == expected


def test_an_unknown_outcome_carries_no_consequence() -> None:
    """A string nobody classified must not block a domain, or crash the worker.

    The completeness probe above is what stops this shipping; this is about what
    happens at 3am if it ever does — no consequence, but not silent either.

    ``caplog`` is unavailable here: pytest's logging plugin is switched off in
    ``pyproject.toml`` so its handlers cannot interleave with the JSON ones the
    logging tests parse. A handler on the module's own logger does the job.
    """
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("meridian_core.policy")
    logger.addHandler(handler)
    try:
        assert domain_signal("something_new_entirely") == "none"
    finally:
        logger.removeHandler(handler)

    assert [r for r in records if r.levelno == logging.WARNING and "unclassified" in r.getMessage()]
