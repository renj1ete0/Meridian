"""A response kept for a while, refreshed behind the reader (`B-121`).

For reads that are expensive and slow-moving: Gaps counts every on-topic
passage in the corpus, several seconds at corpus size and growing with it,
for a list that changes when the crawl does — over hours, not seconds.

Stale-while-revalidate, in-process: within ``ttl_s`` the kept value is served;
after it, the kept value is *still* served and one refresh runs in the
background, so no reader waits but the first after a restart. The value
carries its own time (`computed_at`), so a page can say how old it is.

Not shared between API processes and not persisted — a restart recomputes
once. That is the right size for one API behind one front door.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from meridian_core.logging import get_logger

log = get_logger(__name__)


class Kept[T]:
    def __init__(self, ttl_s: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self._clock = clock
        self._value: T | None = None
        self._at: float | None = None
        self._refresh: asyncio.Task | None = None
        self._first = asyncio.Lock()

    def forget(self) -> None:
        """Drop the kept value, so the next read computes."""
        self._value, self._at = None, None

    async def get(self, load: Callable[[], Awaitable[T]]) -> T:
        """The kept value, or a freshly loaded one if none is kept yet.

        ``load`` must open its own session: a background refresh outlives the
        request that started it.
        """
        if self._value is None:
            # One computation for a burst of first readers, not one each.
            async with self._first:
                if self._value is None:
                    await self._store(load)
            assert self._value is not None
            return self._value
        if self._clock() - (self._at or 0.0) >= self.ttl_s and (
            self._refresh is None or self._refresh.done()
        ):
            self._refresh = asyncio.create_task(self._background(load))
        return self._value

    async def _store(self, load: Callable[[], Awaitable[T]]) -> None:
        self._value = await load()
        self._at = self._clock()

    async def _background(self, load: Callable[[], Awaitable[T]]) -> None:
        try:
            await self._store(load)
        except Exception:
            # The old value keeps being served; the next stale read tries again.
            log.exception("background refresh failed; serving the kept value")

    async def settled(self) -> None:
        """Wait for a refresh in flight. For tests and shutdown."""
        if self._refresh is not None:
            await asyncio.gather(self._refresh, return_exceptions=True)


class KeptByKey[K, T]:
    """One `Kept` per key, the least recently read dropped beyond ``limit`` (`B-140`).

    For reads parameterised by a few options, such as a window and a topic filter. The bound
    keeps an arbitrary mix of filters from growing the cache without end.
    """

    def __init__(
        self, ttl_s: float, *, limit: int = 32, clock: Callable[[], float] = time.monotonic
    ):
        self.ttl_s = ttl_s
        self.limit = limit
        self._clock = clock
        self._kept: dict[K, Kept[T]] = {}

    def forget(self) -> None:
        self._kept.clear()

    async def get(self, key: K, load: Callable[[], Awaitable[T]]) -> T:
        kept = self._kept.pop(key, None) or Kept[T](self.ttl_s, clock=self._clock)
        self._kept[key] = kept  # most recently read last
        while len(self._kept) > self.limit:
            self._kept.pop(next(iter(self._kept)))
        return await kept.get(load)
