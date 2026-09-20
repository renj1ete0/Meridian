"""The synthesis cycle's own bookkeeping (task `P4-09`, §11.10).

What can be decided without a database: whether every stage is accounted for,
and whether the journal says something a person can read. The parts that matter
— that a dry run writes nothing, that a stopped run stays resumable — need a
transaction, and are in `tests/integration/test_orchestrate.py`.
"""

from __future__ import annotations

import pytest

from meridian_core.runs import FINAL_STAGE, STAGES
from worker.orchestrate import BUILT_BY, Journal, cycle


def test_every_stage_says_who_builds_it() -> None:
    """A stage missing from the table would log as "needs nothing".

    Which reads as a stage that ran and found nothing to do — the opposite of
    the truth, and indistinguishable from it in a log nobody is watching.
    """
    unexplained = set(STAGES) - set(BUILT_BY) - {FINAL_STAGE}

    assert not unexplained, f"stages with no task named: {sorted(unexplained)}"


def test_the_final_stage_is_not_work() -> None:
    # `done` is a state, not a step. Listing it would make the cycle try to
    # perform it and then look for something after it.
    assert FINAL_STAGE not in BUILT_BY


def test_a_dry_journal_says_nothing_was_written() -> None:
    journal = Journal(dry_run=True)
    journal.note("pull", "read 12 chunks")

    rendered = journal.render()

    assert "nothing was written" in rendered
    assert "pull" in rendered


def test_a_real_journal_does_not_claim_a_dry_run() -> None:
    journal = Journal(dry_run=False)
    journal.note("pull", "read 12 chunks")

    assert "nothing was written" not in journal.render()


def test_an_empty_journal_says_so_rather_than_printing_a_bare_heading() -> None:
    # A heading with nothing under it reads as truncated output.
    assert "(nothing)" in Journal().render()


def test_a_recorded_call_shows_its_arguments() -> None:
    """The point of `--dry-run` is §11.10's "print tool calls" — a tool name
    with no arguments does not tell you what it would have done."""
    journal = Journal(dry_run=True)

    journal.call("add_edge", subject=1, predicate="located_in", object=2)

    rendered = journal.render()
    assert "add_edge" in rendered
    assert "predicate='located_in'" in rendered


def test_entries_keep_the_order_they_happened_in() -> None:
    journal = Journal()
    journal.note("pull", "first")
    journal.call("add_edge", subject=1)
    journal.note("tag", "last")

    assert [line.split()[0] for line in journal.render().splitlines()[1:]] == [
        "pull",
        "call",
        "tag",
    ]


async def test_an_unknown_stop_stage_is_refused_before_anything_starts() -> None:
    """Refused up front, not after a run has been created.

    A typo in `--stop-after` that was noticed halfway through would leave a
    started run behind, and the unique index would then refuse the next one.
    """
    with pytest.raises(ValueError, match="not a stage"):
        await cycle(None, journal=Journal(), now=None, stop_after="taging")
