"""Pure-function tests for ``backoff_delay_s`` (queueing.py, spec §13.4).

No database needed here — the claiming and leasing behaviour that touches
Postgres lives in ``tests/integration/test_queueing.py``. This file only
protects the backoff formula: it must grow with attempts, stay capped, never
go negative, reject nonsense input, and — the point of the whole function —
actually vary across draws, since a fixed delay is exactly how a transient
outage turns into a thundering herd against a domain that just came back.
"""

from __future__ import annotations

import random

import pytest
from meridian_core.queueing import backoff_delay_s


class _UpperBoundRandom:
    """A fake rng whose ``uniform`` always returns the ceiling.

    Lets the exponential-growth-then-cap formula be checked exactly, rather
    than only approximately through many statistical draws.
    """

    def uniform(self, a: float, b: float) -> float:
        return b


class _LowerBoundRandom:
    """A fake rng whose ``uniform`` always returns the floor (0)."""

    def uniform(self, a: float, b: float) -> float:
        return a


def test_backoff_ceiling_grows_exponentially_then_caps() -> None:
    rng = _UpperBoundRandom()
    assert backoff_delay_s(0, base_s=5, max_s=3600, rng=rng) == 5
    assert backoff_delay_s(1, base_s=5, max_s=3600, rng=rng) == 10
    assert backoff_delay_s(3, base_s=5, max_s=3600, rng=rng) == 40
    assert backoff_delay_s(20, base_s=5, max_s=3600, rng=rng) == 3600, (
        "must cap at max_s rather than growing without bound"
    )


def test_backoff_delay_never_negative() -> None:
    rng = _LowerBoundRandom()
    for attempts in (0, 1, 5, 20):
        assert backoff_delay_s(attempts, rng=rng) == 0


def test_negative_attempts_is_rejected() -> None:
    with pytest.raises(ValueError):
        backoff_delay_s(-1)


def test_backoff_delay_varies_across_draws() -> None:
    """Full jitter: a draw from ``[0, ceiling]``, not a fixed value.

    Synchronised retries are how one transient outage becomes a thundering
    herd the instant a domain recovers — this is the property that prevents
    that, so it has to be checked directly rather than assumed from reading
    the formula.
    """
    rng = random.Random(0)
    ceiling = min(5 * 2**6, 3600)
    values = {backoff_delay_s(6, base_s=5, max_s=3600, rng=rng) for _ in range(500)}
    assert len(values) > 100, (
        "a fixed delay would let synchronised retries hammer a recovering domain"
    )
    assert min(values) >= 0
    assert max(values) <= ceiling
