"""Per-domain concurrency and delay (task P1-04, spec §6.4).

Timing tests, with real sleeps at millisecond scale. A fake clock would make
them faster and would also stop them testing the thing that matters: the gate is
held across an ``await asyncio.sleep``, and whether that actually serialises
concurrent waiters is a property of the event loop, not of arithmetic.

Tolerances are one-sided wherever the correctness direction is one-sided. A gap
longer than the configured delay is politeness; a gap shorter than it is the bug,
so the assertions are `>=` with a small allowance for timer granularity and never
an upper bound that a loaded CI machine would trip.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from worker.ratelimit import DomainLimiter

# Timers wake slightly early on some platforms; 5ms of slack keeps that from
# reading as a politeness breach.
SLACK_S = 0.005


async def timed_slot(limiter: DomainLimiter, domain: str, starts: list, **kwargs) -> None:
    async with limiter.slot(domain, **kwargs):
        starts.append(time.monotonic())


async def test_requests_to_one_domain_are_spaced_by_the_delay() -> None:
    limiter = DomainLimiter()
    starts: list[float] = []

    await asyncio.gather(
        *(timed_slot(limiter, "example.test", starts, concurrency=4, delay_ms=40) for _ in range(4))
    )

    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert all(gap >= 0.040 - SLACK_S for gap in gaps), f"starts too close together: {gaps}"


async def test_waiters_do_not_all_start_at_once_after_one_delay() -> None:
    """The failure mode that looks like a delay and is actually a burst.

    Reading ``next_start`` and then releasing the gate before sleeping would let
    every waiter compute the same wake-up time and start together. This asserts
    the total elapsed time grows with the number of waiters, which only holds if
    the waits are serialised.
    """
    limiter = DomainLimiter()
    started = time.monotonic()

    await asyncio.gather(
        *(timed_slot(limiter, "example.test", [], concurrency=8, delay_ms=30) for _ in range(4))
    )

    elapsed = time.monotonic() - started
    # Three gaps between four starts; the first is free.
    assert elapsed >= 3 * 0.030 - SLACK_S, f"four requests took {elapsed:.3f}s — they burst"


async def test_concurrency_bounds_how_many_are_in_flight() -> None:
    limiter = DomainLimiter()
    in_flight = 0
    peak = 0

    async def hold() -> None:
        nonlocal in_flight, peak
        async with limiter.slot("example.test", concurrency=2, delay_ms=0):
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1

    await asyncio.gather(*(hold() for _ in range(6)))

    assert peak == 2, f"{peak} requests were in flight against a limit of 2"


async def test_a_slow_domain_does_not_hold_up_a_different_one() -> None:
    """§6.4's reason for a *per-domain* limit rather than a global one."""
    limiter = DomainLimiter()
    order: list[str] = []

    async def slow() -> None:
        async with limiter.slot("slow.test", concurrency=1, delay_ms=0):
            await asyncio.sleep(0.05)
            order.append("slow")

    async def quick() -> None:
        await asyncio.sleep(0.01)
        async with limiter.slot("quick.test", concurrency=1, delay_ms=0):
            order.append("quick")

    await asyncio.gather(slow(), quick())

    assert order == ["quick", "slow"], "the quick domain queued behind the slow one"


async def test_the_delay_is_measured_from_starts_not_completions() -> None:
    """A server sees arrivals. Spacing completions would be the wrong quantity.

    A request that itself takes longer than the delay should be followed
    immediately, because by the time it finished the interval had already
    elapsed. Spacing from completion would add the delay again.
    """
    limiter = DomainLimiter()

    async with limiter.slot("example.test", concurrency=2, delay_ms=20):
        await asyncio.sleep(0.06)

    started = time.monotonic()
    async with limiter.slot("example.test", concurrency=2, delay_ms=20):
        pass
    waited = time.monotonic() - started

    assert waited < 0.020, f"waited {waited:.3f}s after a request that outlasted the delay"


async def test_the_first_request_to_a_domain_is_not_delayed() -> None:
    limiter = DomainLimiter()
    started = time.monotonic()

    async with limiter.slot("fresh.test", concurrency=1, delay_ms=500):
        pass

    assert time.monotonic() - started < 0.020, "paid the delay before making any request"


async def test_a_changed_concurrency_limit_is_picked_up() -> None:
    """Fetch policy is editable in Admin (§6.4); the limiter must follow it."""
    limiter = DomainLimiter()

    async with limiter.slot("example.test", concurrency=1, delay_ms=0):
        pass

    in_flight = 0
    peak = 0

    async def hold() -> None:
        nonlocal in_flight, peak
        async with limiter.slot("example.test", concurrency=3, delay_ms=0):
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1

    await asyncio.gather(*(hold() for _ in range(5)))

    assert peak == 3


async def test_the_slot_reports_how_long_it_waited() -> None:
    """The worker loop logs this; a domain whose delay dominates should be visible."""
    limiter = DomainLimiter()

    async with limiter.slot("example.test", concurrency=1, delay_ms=40):
        pass
    async with limiter.slot("example.test", concurrency=1, delay_ms=40) as waited_ms:
        assert waited_ms >= 40 - SLACK_S * 1000


async def test_state_is_kept_per_domain_and_can_be_dropped() -> None:
    """A crawl running for weeks must not accumulate one entry per domain seen."""
    limiter = DomainLimiter()
    for host in ("a.test", "b.test", "c.test"):
        async with limiter.slot(host, concurrency=1, delay_ms=0):
            pass

    assert limiter.tracked_domains == 3
    limiter.forget("b.test")
    assert limiter.tracked_domains == 2
    limiter.forget("not-there.test")  # must not raise
    assert limiter.tracked_domains == 2


async def test_the_slot_is_released_when_the_body_raises() -> None:
    """A failed fetch must not leak a permit, or the domain deadlocks."""
    limiter = DomainLimiter()

    with pytest.raises(RuntimeError):
        async with limiter.slot("example.test", concurrency=1, delay_ms=0):
            raise RuntimeError("fetch blew up")

    async with asyncio.timeout(1):
        async with limiter.slot("example.test", concurrency=1, delay_ms=0):
            pass
