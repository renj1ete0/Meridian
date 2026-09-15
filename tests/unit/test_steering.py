"""The weight vector (task P6-12, spec §10).

§10's model is one sentence — "attention is a weight vector over topics; seeds
are drawn proportionally" — and two things in it are load-bearing in a way that
divide-by-the-total does not deliver.

**The floor is a guarantee.** "Every active topic retains 5–10% minimum so
nothing fully stalls." A floor violated by 0.01 is not a rounding problem, it is
the guarantee not existing: the topic stalls, which is precisely what the floor
was written to prevent, and nothing reports it because a weight vector that sums
to 1.0 looks correct from every angle.

**A boost removes itself.** "Steer back later without needing to remember." The
way that promise breaks is a boost written into the stored weight and a cleanup
job that does not run — so the boost is applied at read time and an expired one
is simply not applied.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from meridian_core.steering import (
    EPS,
    InfeasibleWeights,
    TopicShare,
    boost_is_active,
    check_feasible,
    draw_shares,
    effective_weight,
    normalise,
)

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)


@dataclasses.dataclass
class Row:
    topic: str
    weight: float
    floor: float = 0.05
    ceiling: float = 0.60
    status: str = "active"
    boost_factor: float | None = None
    boost_expires_at: dt.datetime | None = None


def share(topic: str, weight: float, floor: float = 0.05, ceiling: float = 0.60) -> TopicShare:
    return TopicShare(topic, weight, floor, ceiling)


# --------------------------------------------------------------------------
# The pool sums to one
# --------------------------------------------------------------------------


def test_weights_are_scaled_to_sum_to_one() -> None:
    result = normalise([share("a", 2), share("b", 1), share("c", 1)])

    assert sum(result.values()) == pytest.approx(1.0)


def test_proportions_are_kept_when_nothing_is_clamped() -> None:
    result = normalise([share("a", 2, 0.0, 1.0), share("b", 1, 0.0, 1.0)])

    assert result["a"] == pytest.approx(2 * result["b"])


def test_an_empty_set_is_not_a_division_by_zero() -> None:
    assert normalise([]) == {}


def test_all_zero_weights_are_spread_evenly() -> None:
    # A set of topics nobody has weighted yet — the state a fresh install is in
    # before the first steering action. Dividing by the total would be 0/0.
    result = normalise([share("a", 0), share("b", 0), share("c", 0)])

    assert result == {
        "a": pytest.approx(1 / 3),
        "b": pytest.approx(1 / 3),
        "c": pytest.approx(1 / 3),
    }


def test_a_negative_weight_is_read_as_zero_rather_than_subtracting() -> None:
    # A negative weight scaled proportionally would take share *away* from the
    # pool and could push another topic above 1.0.
    result = normalise([share("a", -5, 0.0, 1.0), share("b", 1, 0.0, 1.0)])

    assert sum(result.values()) == pytest.approx(1.0)
    assert result["a"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# The floor is a guarantee, not a preference
# --------------------------------------------------------------------------


def test_a_dominant_topic_does_not_starve_the_others_below_their_floor() -> None:
    # The case that makes divide-by-total wrong. Proportionally, b and c would
    # get 0.005 each — vanishingly small, summing to 1.0, and stalled.
    result = normalise(
        [share("a", 99, 0.05, 1.0), share("b", 0.5, 0.05, 1.0), share("c", 0.5, 0.05, 1.0)]
    )

    assert result["b"] >= 0.05 - EPS
    assert result["c"] >= 0.05 - EPS
    assert sum(result.values()) == pytest.approx(1.0)


def test_the_topic_that_was_clamped_down_keeps_the_rest() -> None:
    # The converse of the floor: whatever the floors took has to come from
    # somewhere, and it comes from the topic that had more than it needed.
    result = normalise(
        [share("a", 99, 0.05, 1.0), share("b", 0.5, 0.05, 1.0), share("c", 0.5, 0.05, 1.0)]
    )

    assert result["a"] == pytest.approx(0.9)


def test_a_ceiling_caps_a_topic_however_large_its_weight() -> None:
    result = normalise([share("a", 99, 0.0, 0.5), share("b", 1, 0.0, 1.0)])

    assert result["a"] == pytest.approx(0.5)
    assert result["b"] == pytest.approx(0.5)


def test_every_floor_holds_across_a_realistic_set() -> None:
    # Six topics, one dominant, all at the spec's 5% floor. A property over the
    # whole result rather than one topic, because clamping one can push the next
    # one under and a spot check would not see it.
    shares = [share("dominant", 50, 0.05, 1.0)] + [share(f"t{i}", 0.1) for i in range(5)]

    result = normalise(shares)

    assert all(value >= bound.floor - EPS for bound in shares for value in [result[bound.topic]])
    assert all(result[bound.topic] <= bound.ceiling + EPS for bound in shares)
    assert sum(result.values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# What cannot be satisfied is refused
# --------------------------------------------------------------------------


def test_floors_that_cannot_all_be_met_are_refused_on_write() -> None:
    # The moment to say so is while somebody is making the mistake. Discovering
    # it three weeks later from a stalled crawl is not useful.
    with pytest.raises(InfeasibleWeights, match="1.2"):
        check_feasible([share(f"t{i}", 1, floor=0.3) for i in range(4)])


def test_a_feasible_configuration_passes_the_check() -> None:
    # The converse, without which the test above would pass against a function
    # that raised unconditionally.
    check_feasible([share(f"t{i}", 1) for i in range(4)])


def test_reading_an_infeasible_configuration_does_not_raise() -> None:
    # A stored configuration that has become infeasible must not take down the
    # screen that would let somebody fix it. Floors scale down together, so
    # every topic keeps a share in the ratio it was promised.
    result = normalise([share(f"t{i}", 1, floor=0.3, ceiling=1.0) for i in range(4)])

    assert sum(result.values()) == pytest.approx(1.0)
    assert all(value == pytest.approx(0.25) for value in result.values())


def test_a_ceiling_yields_when_it_is_the_only_topic_left() -> None:
    # Not a degenerate case: pausing every topic but one leaves a single topic
    # with a 0.6 ceiling, and the seeds still have to come from somewhere. A
    # ceiling guards against crowding out the others; with no others it
    # constrains nothing, and enforcing it would draw 60% of a pool and leave
    # the rest undrawn.
    result = normalise([share("only", 1, 0.05, 0.60)])

    assert result == {"only": pytest.approx(1.0)}


def test_ceilings_that_cannot_reach_one_yield_together() -> None:
    result = normalise([share(f"t{i}", i + 1, 0.0, 0.2) for i in range(3)])

    assert sum(result.values()) == pytest.approx(1.0)


def test_floors_summing_to_exactly_one_are_allowed() -> None:
    # The boundary. Refusing it would reject a configuration that is exactly
    # satisfiable, and a guard written with `>=` is the usual way that happens.
    check_feasible([share(f"t{i}", i, floor=0.25, ceiling=1.0) for i in range(4)])
    result = normalise([share(f"t{i}", i, floor=0.25, ceiling=1.0) for i in range(4)])

    assert all(value == pytest.approx(0.25) for value in result.values())


# --------------------------------------------------------------------------
# Boosts decay by expiry, never by memory
# --------------------------------------------------------------------------


def test_a_running_boost_multiplies_the_weight() -> None:
    row = Row("a", 0.2, boost_factor=2.0, boost_expires_at=NOW + dt.timedelta(days=1))

    assert effective_weight(row, now=NOW) == pytest.approx(0.4)
    assert boost_is_active(row, now=NOW) is True


def test_an_expired_boost_stops_counting_without_anything_clearing_it() -> None:
    # The whole mechanism. If this needed a cleanup pass, a boost would outlive
    # its expiry on any machine where that pass failed — and the mode's promise
    # is that you do not have to remember.
    row = Row("a", 0.2, boost_factor=2.0, boost_expires_at=NOW - dt.timedelta(seconds=1))

    assert effective_weight(row, now=NOW) == pytest.approx(0.2)
    assert boost_is_active(row, now=NOW) is False


def test_the_row_keeps_the_expired_boost_as_a_record() -> None:
    # Not cleared on purpose: the row still says what was boosted and until
    # when, which is the only trace of a temporary intervention after it ends.
    row = Row("a", 0.2, boost_factor=2.0, boost_expires_at=NOW - dt.timedelta(days=30))

    effective_weight(row, now=NOW)

    assert row.boost_factor == 2.0


def test_a_factor_without_an_expiry_is_not_applied() -> None:
    # A permanent multiplier wearing a temporary one's clothes. Applying it
    # would make "it decays on its own" false for whoever set it.
    row = Row("a", 0.2, boost_factor=5.0, boost_expires_at=None)

    assert effective_weight(row, now=NOW) == pytest.approx(0.2)


def test_a_boost_expiring_exactly_now_has_expired() -> None:
    row = Row("a", 0.2, boost_factor=2.0, boost_expires_at=NOW)

    assert boost_is_active(row, now=NOW) is False


# --------------------------------------------------------------------------
# Who is in the pool (§10.2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["paused", "archived", "maintenance"])
def test_only_active_topics_draw_seeds(status: str) -> None:
    # §10.2: archiving "drops out of the weight-normalization pool, exactly like
    # maintenance mode". Maintenance keeps processing its queue and generates no
    # new seeds, so it is out of the draw too.
    rows = [Row("a", 0.5, ceiling=1.0), Row("b", 0.5, status=status)]

    shares = draw_shares(rows, now=NOW)

    assert "b" not in shares
    assert shares["a"] == pytest.approx(1.0)


def test_a_paused_topic_keeps_its_stored_weight() -> None:
    # "Nothing is deleted, so returning costs nothing" applies to whole topics.
    # Zeroing the weight on pause would make un-pausing a decision rather than a
    # status change.
    rows = [Row("a", 0.5, ceiling=1.0), Row("b", 0.3, status="paused")]

    draw_shares(rows, now=NOW)

    assert rows[1].weight == 0.3


def test_a_boost_shifts_the_draw_without_touching_the_weights() -> None:
    rows = [
        Row("a", 0.5, boost_factor=3.0, boost_expires_at=NOW + dt.timedelta(days=7)),
        Row("b", 0.5),
    ]

    shares = draw_shares(rows, now=NOW)

    assert shares["a"] > shares["b"]
    assert rows[0].weight == 0.5


def test_no_active_topics_draws_nothing_rather_than_raising() -> None:
    # Everything archived is a legitimate state — a corpus somebody has put
    # down — and it must not make the screen that would let them restart it
    # throw.
    assert draw_shares([Row("a", 0.5, status="archived")], now=NOW) == {}
