"""The novelty gate's decision, with no database in it (task P2-03, spec §6.1).

``judge`` is deliberately pure — it takes nearest neighbours and returns
verdicts — because the three things that are easy to get wrong here are all
arithmetic and none of them need Postgres to demonstrate: which side of the
threshold is kept, what happens when there is nothing to compare against, and
what a verdict points at when its target is being marked in the same batch.

The database half — that the neighbour is genuinely the nearest, that it is
genuinely earlier, that the columns hold — is in
``tests/integration/test_novelty.py``, against real pgvector.
"""

from __future__ import annotations

import pytest

from meridian_core.novelty import (
    DEFAULT_THRESHOLD,
    Neighbour,
    NoveltyHealth,
    NoveltySettings,
    judge,
)


def near(chunk_id: int, similarity: float) -> Neighbour:
    return Neighbour(chunk_id=chunk_id, similarity=similarity)


# --------------------------------------------------------------------------
# The threshold
# --------------------------------------------------------------------------


def test_the_spec_threshold_is_the_default() -> None:
    """§6.1 writes the number down: drop above 0.95. It is not a taste setting."""
    assert DEFAULT_THRESHOLD == 0.95
    assert NoveltySettings().threshold == 0.95


def test_a_chunk_above_the_threshold_is_a_duplicate() -> None:
    verdicts = judge([7], {7: near(3, 0.97)}, threshold=0.95)

    assert verdicts[0].duplicate_of == 3
    assert verdicts[0].nearest_similarity == pytest.approx(0.97)
    assert not verdicts[0].novel


def test_a_chunk_exactly_on_the_threshold_is_kept() -> None:
    """Strictly greater, as §6.1 writes it.

    The asymmetry is on purpose and it is not pedantry: keeping a duplicate
    costs a row, and dropping a chunk that sat on the line costs the only copy
    of something. A gate tuned by moving the threshold will sit *on* values
    that were measured, so which way the boundary falls is a real decision.
    """
    verdicts = judge([7], {7: near(3, 0.95)}, threshold=0.95)

    assert verdicts[0].novel
    assert verdicts[0].nearest_similarity == pytest.approx(0.95)


def test_a_kept_chunk_still_records_how_close_it_came() -> None:
    """The score is written whether or not it cleared the bar.

    Without it the threshold can never be re-tuned against the corpus that was
    actually collected — only re-run, which means re-embedding nothing and
    re-scanning everything.
    """
    verdict = judge([7], {7: near(3, 0.4)}, threshold=0.95)[0]

    assert verdict.novel
    assert verdict.nearest_similarity == pytest.approx(0.4)


def test_nothing_to_compare_against_is_not_similarity_zero() -> None:
    """The first chunk in an empty corpus is unjudgeable, not maximally novel.

    Recorded as NULL rather than 0.0, because a real orthogonal neighbour is
    also 0.0 and the two mean different things — one says "nothing like this
    exists yet", the other says "nothing like this exists among the thousands
    of things checked".
    """
    verdict = judge([7], {}, threshold=0.95)[0]

    assert verdict.novel
    assert verdict.nearest_similarity is None


# --------------------------------------------------------------------------
# Chains
# --------------------------------------------------------------------------


def test_a_verdict_is_followed_through_a_target_marked_in_the_same_batch() -> None:
    """B duplicates A, A duplicates Z — so B must point at Z, not at A.

    The candidate filter excludes chunks already marked, but it cannot see a
    verdict that has not been written yet, so within one batch a chain is
    reachable. Resolving it here is what lets every consumer treat
    ``duplicate_of`` as "the surviving copy" rather than as a linked list.
    """
    verdicts = judge([50, 60], {50: near(9, 0.99), 60: near(50, 0.99)}, threshold=0.95)

    assert verdicts[0].duplicate_of == 9
    assert verdicts[1].duplicate_of == 9, "the chain was not followed to the survivor"


def test_a_long_chain_still_resolves_to_one_survivor() -> None:
    ids = [20, 30, 40, 50]
    neighbours = {20: near(9, 0.99), 30: near(20, 0.99), 40: near(30, 0.99), 50: near(40, 0.99)}

    verdicts = judge(ids, neighbours, threshold=0.95)

    assert [v.duplicate_of for v in verdicts] == [9, 9, 9, 9]


def test_a_chunk_pointing_at_a_kept_chunk_is_left_alone() -> None:
    """Only *marked* targets are followed. A near-duplicate of something that
    was itself kept points at that chunk, which is the survivor."""
    verdicts = judge([50, 60], {50: near(9, 0.10), 60: near(50, 0.99)}, threshold=0.95)

    assert verdicts[0].novel
    assert verdicts[1].duplicate_of == 50


# --------------------------------------------------------------------------
# Settings that would silently disable the gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", [1.5, -2.0, 42.0])
def test_a_threshold_outside_the_cosine_range_is_refused(threshold: float) -> None:
    """A threshold of 1.5 is not a strict gate, it is a gate that never fires —
    and the corpus it produces looks clean from every metric."""
    with pytest.raises(ValueError, match="cosine similarity"):
        NoveltySettings(threshold=threshold)


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
def test_an_impossible_source_fraction_is_refused(fraction: float) -> None:
    with pytest.raises(ValueError, match="source_fraction"):
        NoveltySettings(source_fraction=fraction)


def test_a_non_positive_batch_is_refused() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        NoveltySettings(batch_size=0)


def test_settings_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERIDIAN_NOVELTY_THRESHOLD", "0.88")
    monkeypatch.setenv("MERIDIAN_NOVELTY_SOURCE_FRACTION", "0.75")
    monkeypatch.setenv("MERIDIAN_NOVELTY_BATCH", "32")

    settings = NoveltySettings.from_env()

    assert (settings.threshold, settings.source_fraction, settings.batch_size) == (0.88, 0.75, 32)


def test_an_unparseable_threshold_fails_loudly(monkeypatch) -> None:
    """Rather than falling back to the default, which would run a differently
    configured gate than the operator asked for and say nothing."""
    monkeypatch.setenv("MERIDIAN_NOVELTY_THRESHOLD", "ninety-five percent")

    with pytest.raises(RuntimeError, match="MERIDIAN_NOVELTY_THRESHOLD"):
        NoveltySettings.from_env()


# --------------------------------------------------------------------------
# The health line (§12.5)
# --------------------------------------------------------------------------


def test_the_pass_rate_is_the_fraction_that_survived() -> None:
    health = NoveltyHealth(judged=200, duplicates=50, pending=7)

    assert health.pass_rate == pytest.approx(0.75)
    assert health.as_dict()["novelty_pending"] == 7


def test_an_unjudged_corpus_has_no_pass_rate_rather_than_a_perfect_one() -> None:
    """Zero over zero is not 1.0. A gate that has judged nothing reporting a
    100% pass rate is exactly the silent failure §12.5's line exists to catch."""
    assert NoveltyHealth(judged=0, duplicates=0, pending=900).pass_rate is None
