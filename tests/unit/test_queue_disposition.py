"""What a fetch outcome means for the task (task P1-15, spec §13.4).

The sibling of `test_fetch_signals.py`, which asks the same outcomes a different
question. That one decides whether the *domain* should keep being fetched from;
this one decides whether *this URL* should be asked for again. The two disagree
on purpose in both directions, and getting either wrong is silent: too eager to
retry and a robots-denied URL costs three requests and an hour of backoff to be
refused three times, too eager to abandon and one flaky minute drops a page out
of the corpus permanently.
"""

from __future__ import annotations

import logging

import pytest

from meridian_core.models.queue import FETCH_OUTCOME
from meridian_core.queueing import (
    TASK_ABANDON,
    TASK_FETCHED,
    TASK_RETRY,
    TASK_UNCHANGED,
    queue_disposition,
)


def test_every_fetch_outcome_has_a_disposition() -> None:
    """Completeness probe, derived from the model's CHECK constraint.

    A new outcome value fails here until someone has decided whether a task that
    ended that way is worth asking about again. Hardcoding the expected set
    would make this a test that gets edited into passing.
    """
    classified = TASK_FETCHED | TASK_UNCHANGED | TASK_RETRY | TASK_ABANDON | {"http_error"}
    unclassified = set(FETCH_OUTCOME.enums) - classified
    assert not unclassified, f"fetch outcomes with no queue disposition: {sorted(unclassified)}"


def test_the_four_sets_do_not_overlap() -> None:
    sets = {
        "fetched": TASK_FETCHED,
        "unchanged": TASK_UNCHANGED,
        "retry": TASK_RETRY,
        "abandon": TASK_ABANDON,
    }
    for name, values in sets.items():
        others = set().union(*(v for k, v in sets.items() if k != name))
        assert not values & others, f"{name} overlaps another disposition"
    assert "http_error" not in set().union(*sets.values())


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("success", "fetched"),
        # Nothing new to extract: the body that produced the validator is
        # already in the corpus, so the task is finished, not passed on.
        ("not_modified", "done"),
        ("timeout", "retry"),
        ("connection_error", "retry"),
        # Deterministic in the retry window — asking again in five seconds gets
        # the same answer from the same code path.
        ("robots_denied", "abandon"),
        ("blocked", "abandon"),
        ("unsafe_target", "abandon"),
        ("content_type_rejected", "abandon"),
        ("too_large", "abandon"),
        ("parse_error", "abandon"),
        ("decompression_bomb", "abandon"),
        ("too_many_redirects", "abandon"),
    ],
)
def test_outcomes_carry_the_disposition_they_should(outcome: str, expected: str) -> None:
    assert queue_disposition(outcome) == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (404, "abandon"),  # a correct answer about a URL that is not coming back
        (403, "abandon"),
        (410, "abandon"),
        (429, "retry"),  # the server asking for a minute, not refusing forever
        (500, "retry"),
        (503, "retry"),
        (None, "retry"),
    ],
)
def test_http_errors_are_split_by_status_code(status: int | None, expected: str) -> None:
    assert queue_disposition("http_error", status) == expected


@pytest.mark.parametrize(
    "outcome",
    ["too_many_redirects", "decompression_bomb", "unsafe_target"],
)
def test_domain_backoff_and_task_retry_are_different_questions(outcome: str) -> None:
    """These count against the domain and are still never retried for the URL.

    Recorded as a test rather than a comment because the overlap is the obvious
    "simplification" — reusing `DOMAIN_UNREACHABLE` here would silently start
    re-requesting decompression bombs three times each.
    """
    from meridian_core.policy import DOMAIN_UNREACHABLE, domain_signal

    assert outcome in DOMAIN_UNREACHABLE
    assert domain_signal(outcome) == "unreachable"
    assert queue_disposition(outcome) == "abandon"


def test_a_404_is_a_healthy_domain_and_a_dead_url() -> None:
    """The disagreement in the other direction."""
    from meridian_core.policy import domain_signal

    assert domain_signal("http_error", 404) == "alive"
    assert queue_disposition("http_error", 404) == "abandon"


def test_an_unknown_outcome_is_retried_and_says_so() -> None:
    """A string nobody classified must not silently drop a URL, or crash a lane.

    Retry is the forgiving default: it is bounded by `max_retries` either way,
    where abandoning would lose the task with no trace but a log line.

    `caplog` is unavailable in this suite — pytest's logging plugin is off in
    `pyproject.toml` so its handlers cannot interleave with the JSON ones the
    logging tests parse. A handler on the module's own logger does the job.
    """
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("meridian_core.queueing")
    logger.addHandler(handler)
    try:
        assert queue_disposition("something_new_entirely") == "retry"
    finally:
        logger.removeHandler(handler)

    assert [r for r in records if r.levelno == logging.WARNING and "unclassified" in r.getMessage()]
