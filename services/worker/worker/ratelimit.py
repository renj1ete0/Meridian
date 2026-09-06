"""Per-domain concurrency and delay (task P1-04, spec §6.4).

§6.4 names these as two of the four defaults that do real work: they keep one
slow domain from monopolising the worker, and they keep a small municipal server
from being hammered by something that thinks it is being efficient.

They are genuinely two limits, not one, and conflating them gets the behaviour
wrong in both directions:

**Concurrency** bounds how many requests are *in flight* to a domain. It exists
because a domain that takes 30 seconds per response would otherwise consume the
whole worker.

**Delay** bounds how often a request *starts*. It exists because politeness is
about the rate a server sees, and a server sees arrivals, not completions.
Spacing completions instead would let two slow requests start simultaneously and
then wait a second after both returned, which is the opposite of the intent.

So `concurrency_per_domain: 2` with `delay_per_domain_ms: 1000` means: at most
two requests outstanding, and starts no closer together than a second. The delay
passed in is already jittered by ``ResolvedPolicy.next_delay_ms()`` (P1-18) — a
fixed interval is both a recognisable fingerprint and a way to synchronise
bursts across domains.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

from meridian_core.logging import get_logger

log = get_logger(__name__)


class _DomainState:
    """The two limits for one domain, plus the clock they share."""

    def __init__(self, concurrency: int) -> None:
        self.semaphore = asyncio.Semaphore(concurrency)
        self.limit = concurrency
        self.gate = asyncio.Lock()
        self.next_start = 0.0


class DomainLimiter:
    """Rate-limits requests per domain. One instance per worker process.

    State is in-process, deliberately. A shared limiter across worker processes
    would need a coordination service, and the queue design (§2 principle 2)
    exists precisely so that no such thing is required — the concurrency figure
    is per worker, and running two workers against one domain means doubling it
    knowingly rather than discovering it.
    """

    def __init__(self) -> None:
        self._domains: dict[str, _DomainState] = {}

    def _state(self, domain: str, concurrency: int) -> _DomainState:
        state = self._domains.get(domain)
        if state is None:
            state = self._domains[domain] = _DomainState(concurrency)
        elif state.limit != concurrency:
            # The policy was edited in Admin between requests. Requests already
            # holding the old semaphore keep it and finish normally; everything
            # from here uses the new limit. Briefly exceeding the new figure
            # during the changeover is better than tracking permits by hand.
            log.info(
                "per-domain concurrency changed",
                extra={"domain": domain, "was": state.limit, "now": concurrency},
            )
            state.semaphore = asyncio.Semaphore(concurrency)
            state.limit = concurrency
        return state

    @contextlib.asynccontextmanager
    async def slot(self, domain: str, *, concurrency: int, delay_ms: int) -> AsyncIterator[float]:
        """Hold a request slot for ``domain``. Yields how long it waited, in ms.

        The gate is held *across* the sleep on purpose. Releasing it first would
        let every waiter read the same ``next_start``, sleep the same interval,
        and then all start together — which is a burst wearing a delay's
        clothing. Serialising the waits is what actually spaces the arrivals.
        """
        state = self._state(domain, concurrency)
        started_waiting = time.monotonic()

        async with state.semaphore:
            async with state.gate:
                now = time.monotonic()
                wait_s = state.next_start - now
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                    now = state.next_start
                state.next_start = now + delay_ms / 1000
            yield (time.monotonic() - started_waiting) * 1000

    def forget(self, domain: str) -> None:
        """Drop a domain's state.

        Worth calling when a domain is marked blocked: a crawl that runs for
        weeks would otherwise accumulate one entry per domain it ever touched.
        """
        self._domains.pop(domain, None)

    @property
    def tracked_domains(self) -> int:
        return len(self._domains)
