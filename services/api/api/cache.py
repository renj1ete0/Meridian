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
from typing import Generic, TypeVar

from meridian_core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class Kept(Generic[T]):
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
