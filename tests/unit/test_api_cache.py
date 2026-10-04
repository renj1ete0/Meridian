"""The kept-response cache behind Gaps (task B-121).

Stale-while-revalidate is easy to get subtly wrong: a stale read that waits for
the refresh is the slowness it exists to remove, two refreshes at once double
the cost, and a failed refresh that drops the value turns a slow page into a
broken one.
"""

from __future__ import annotations

import asyncio

import pytest

from api.cache import Kept


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def loader(values: list):
    calls = {"n": 0}

    async def load():
        calls["n"] += 1
        v = values.pop(0)
        if isinstance(v, Exception):
            raise v
        await asyncio.sleep(0)
        return v

    return load, calls


async def test_within_the_window_the_kept_value_is_served_without_loading() -> None:
    clock = Clock()
    kept: Kept[str] = Kept(10, clock=clock)
    load, calls = loader(["a", "b"])
    assert await kept.get(load) == "a"
    clock.now = 9
    assert await kept.get(load) == "a"
    assert calls["n"] == 1


async def test_a_stale_read_is_answered_at_once_and_refreshed_behind_it() -> None:
    clock = Clock()
    kept: Kept[str] = Kept(10, clock=clock)
    load, calls = loader(["a", "b"])
    await kept.get(load)
    clock.now = 11

    assert await kept.get(load) == "a", "a stale read must not wait for the refresh"
    await kept.settled()
    assert await kept.get(load) == "b"
    assert calls["n"] == 2


async def test_only_one_refresh_runs_at_a_time() -> None:
    clock = Clock()
    kept: Kept[str] = Kept(10, clock=clock)
    gate = asyncio.Event()
    calls = {"n": 0}

    async def slow():
        calls["n"] += 1
        if calls["n"] > 1:
            await gate.wait()
        return f"v{calls['n']}"

    await kept.get(slow)
    clock.now = 11
    await asyncio.gather(*(kept.get(slow) for _ in range(5)))
    gate.set()
    await kept.settled()
    assert calls["n"] == 2


async def test_a_failed_refresh_keeps_serving_the_old_value() -> None:
    clock = Clock()
    kept: Kept[str] = Kept(10, clock=clock)
    load, _ = loader(["a", RuntimeError("database gone"), "c"])
    await kept.get(load)
    clock.now = 11
    await kept.get(load)
    await kept.settled()
    assert await kept.get(load) == "a"


async def test_a_burst_of_first_readers_loads_once() -> None:
    kept: Kept[str] = Kept(10)
    load, calls = loader(["a"] * 5)
    assert set(await asyncio.gather(*(kept.get(load) for _ in range(5)))) == {"a"}
    assert calls["n"] == 1


async def test_a_first_read_that_fails_raises_rather_than_serving_nothing() -> None:
    kept: Kept[str] = Kept(10)
    load, _ = loader([RuntimeError("down")])
    with pytest.raises(RuntimeError):
        await kept.get(load)


async def test_keyed_values_are_kept_apart_and_bounded() -> None:
    """`B-140`: one value per key, and the least recently read dropped past the limit."""
    from api.cache import KeptByKey

    kept: KeptByKey[str, str] = KeptByKey(60, limit=2)
    loads: list[str] = []

    def loader(key: str):
        async def load() -> str:
            loads.append(key)
            return key.upper()

        return load

    assert await kept.get("a", loader("a")) == "A"
    assert await kept.get("b", loader("b")) == "B"
    assert await kept.get("a", loader("a")) == "A", "kept, not reloaded"
    await kept.get("c", loader("c"))  # evicts b, the least recently read
    await kept.get("b", loader("b"))
    assert loads == ["a", "b", "c", "b"]
