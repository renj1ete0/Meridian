"""Age-aware ranking, by document kind (task `P2-20`, §9).

The thing this module exists to avoid is more important than the thing it does.
A single "newer is better" multiplier is easy, and for a corpus with an academic
spine it is the failure that matters: the 1987 paper everything cites ranks
below a blog post about it.

So the tests are mostly about what must *not* happen — a foundational paper
must not decay, an undated document must not be guessed at in either direction,
and decay must reorder rather than delete.

No database. The arithmetic is the part most likely to be subtly wrong and the
part a Postgres fixture tells you least about, which is the argument `fuse` in
`search.py` already makes for itself.
"""

from __future__ import annotations

import datetime as dt

import pytest

from meridian_core.ageing import (
    DEFAULT_HALF_LIFE_DAYS,
    HALF_LIFE_DAYS,
    MIN_FACTOR,
    age_in_days,
    decay_factor,
    half_life_for,
)

TODAY = dt.date(2026, 9, 20)


def days_ago(n: int) -> dt.date:
    return TODAY - dt.timedelta(days=n)


# --------------------------------------------------------------------------
# The failure this exists to avoid


def test_peer_reviewed_work_does_not_decay_at_all() -> None:
    """The foundational paper is the one a global multiplier buries, and for
    this corpus that is the failure that matters."""
    assert decay_factor(days_ago(20 * 365), "peer_reviewed", today=TODAY) == 1.0


def test_a_press_article_of_the_same_age_decays_hard() -> None:
    """The same date, a different kind of claim. If these two came out the same
    the table would not be doing anything."""
    old_press = decay_factor(days_ago(20 * 365), "press", today=TODAY)
    old_paper = decay_factor(days_ago(20 * 365), "peer_reviewed", today=TODAY)

    assert old_press < old_paper
    assert old_press == MIN_FACTOR


def test_decay_reorders_rather_than_deletes() -> None:
    """Floored, because without a floor a ten-year-old article scores within
    rounding of zero and drops out of the result set — a filter wearing a
    decay's clothes."""
    assert decay_factor(days_ago(100 * 365), "informal", today=TODAY) == MIN_FACTOR


# --------------------------------------------------------------------------
# An undated document is neither old nor new


def test_an_undated_document_is_not_adjusted() -> None:
    """Around a third of crawled pages have no extractable date, and whichever
    default you pick is wrong for the other kind: treating them as new floats
    every undated blog to the top, treating them as old buries every undated
    standards document."""
    assert decay_factor(None, "press", today=TODAY) == 1.0
    assert decay_factor(None, "peer_reviewed", today=TODAY) == 1.0


def test_an_undated_document_has_no_age_rather_than_age_zero() -> None:
    """Zero would be indistinguishable from "published today"."""
    assert age_in_days(None) is None
    assert age_in_days(TODAY, today=TODAY) == 0


def test_a_future_date_does_not_boost_a_document() -> None:
    """Embargoes, timezones and journal issues dated next month are all common.
    A negative age would let a scraped metadata field outrank the corpus."""
    assert age_in_days(TODAY + dt.timedelta(days=90), today=TODAY) == 0
    assert decay_factor(TODAY + dt.timedelta(days=90), "press", today=TODAY) == 1.0


# --------------------------------------------------------------------------
# The curve


@pytest.mark.parametrize("tier", ["press", "government", "informal", "industry", "academic"])
def test_one_half_life_halves_the_score(tier: str) -> None:
    half_life = HALF_LIFE_DAYS[tier]
    assert half_life is not None

    assert decay_factor(days_ago(half_life), tier, today=TODAY) == pytest.approx(0.5)


def test_decay_is_monotonic_in_age() -> None:
    factors = [decay_factor(days_ago(n), "press", today=TODAY) for n in (0, 100, 365, 900)]

    assert factors == sorted(factors, reverse=True)


def test_the_tiers_are_ordered_the_way_the_task_argues() -> None:
    """Press and informal rot fastest; a policy page supersedes rather than
    ages, so government sits well above press without being exempt."""
    at_five_years = {
        tier: decay_factor(days_ago(5 * 365), tier, today=TODAY)
        for tier in ("informal", "press", "industry", "government", "academic")
    }

    assert at_five_years["informal"] <= at_five_years["press"]
    assert at_five_years["press"] < at_five_years["government"]
    assert at_five_years["government"] <= at_five_years["academic"]


# --------------------------------------------------------------------------
# Overrides


def test_a_topic_override_beats_the_tier() -> None:
    """§9's own example is a topic one: the same tier ages differently
    depending on what the document is about."""
    fast = decay_factor(
        days_ago(365),
        "academic",
        topics=["av-acceptance"],
        overrides={"av-acceptance": 180},
        today=TODAY,
    )

    assert fast < decay_factor(days_ago(365), "academic", today=TODAY)


def test_a_topic_can_exempt_itself_from_ageing() -> None:
    assert (
        half_life_for("press", topics=["thermal-comfort"], overrides={"thermal-comfort": None})
        is None
    )


def test_the_longest_half_life_wins_across_topics() -> None:
    """A document partly about something slow-moving must not be aged as though
    it were only about the fast-moving half. The conservative direction is the
    one that does not bury things."""
    assert (
        half_life_for("press", topics=["fast", "slow"], overrides={"fast": 90, "slow": 3650})
        == 3650
    )


def test_an_exempt_topic_wins_outright_rather_than_being_compared() -> None:
    assert (
        half_life_for(
            "press", topics=["fast", "timeless"], overrides={"fast": 90, "timeless": None}
        )
        is None
    )


def test_an_unknown_tier_decays_rather_than_becoming_exempt() -> None:
    """Exemption is the valuable state and should be granted deliberately. A
    tier nobody added to the table must not inherit it by accident."""
    assert half_life_for("something-new") == DEFAULT_HALF_LIFE_DAYS
    assert decay_factor(days_ago(3650), "something-new", today=TODAY) < 1.0
