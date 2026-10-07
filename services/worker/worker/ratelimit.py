"""Per-domain concurrency and delay (task P1-04, spec §6.4).

Concurrency bounds requests in flight to a domain; delay bounds how often a request
*starts*. The delay passed in is already jittered (``ResolvedPolicy.next_delay_ms``,
P1-18). See docs/features/crawling.md#one-polite-fetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

from meridian_core.logging import get_logger

log = get_logger(__name__)

#: A domain whose next request cannot start within this long is busy (`B-112`).
BUSY_HORIZON_S = 5.0


class _DomainState:
    """The two limits for one domain, plus the clock they share."""

    def __init__(self, concurrency: int) -> None:
        self.semaphore = asyncio.Semaphore(concurrency)
        self.limit = concurrency
        self.gate = asyncio.Lock()
        self.next_start = 0.0
        #: Requests inside `slot` not yet started — queued behind the limits.
        self.waiting = 0


class DomainLimiter:
    """Rate-limits requests per domain. One instance per worker process.

    State is in-process: two workers against one domain double the limit.
    """

    def __init__(self) -> None:
        self._domains: dict[str, _DomainState] = {}

    def _state(self, domain: str, concurrency: int) -> _DomainState:
        state = self._domains.get(domain)
        if state is None:
            state = self._domains[domain] = _DomainState(concurrency)
        elif state.limit != concurrency:
            # The policy was edited in Admin. Requests holding the old semaphore finish
            # on it; the new limit applies from here.
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

        The gate is held across the sleep, so waiters' starts are spaced rather than
        woken together.
        """
        state = self._state(domain, concurrency)
        started_waiting = time.monotonic()

        # Counted from arrival until the request may start (`B-112`), so the
        # claim can see a domain with work already queued behind its limits.
        state.waiting += 1
        counted = True
        try:
            async with state.semaphore:
                async with state.gate:
                    now = time.monotonic()
                    wait_s = state.next_start - now
                    if wait_s > 0:
                        await asyncio.sleep(wait_s)
                        now = state.next_start
                    state.next_start = now + delay_ms / 1000
                state.waiting -= 1
                counted = False
                yield (time.monotonic() - started_waiting) * 1000
        finally:
            if counted:
                state.waiting -= 1

    def busy(self, *, horizon_s: float | None = None) -> set[str]:
        """Domains a new request would wait on (`B-112`).

        One already has a request queued behind its limits, or its next start is further off than
        ``horizon_s``. The claim leaves their pages for later, so a lane takes other work instead of
        parking behind one slow host.
        """
        horizon = BUSY_HORIZON_S if horizon_s is None else horizon_s
        now = time.monotonic()
        return {
            domain
            for domain, state in self._domains.items()
            if state.waiting > 0 or state.next_start - now > horizon
        }

    def forget(self, domain: str) -> None:
        """Drop a domain's state.

        Worth calling when a domain is marked blocked: a crawl that runs for
        weeks would otherwise accumulate one entry per domain it ever touched.
        """
        self._domains.pop(domain, None)

    @property
    def tracked_domains(self) -> int:
        return len(self._domains)
