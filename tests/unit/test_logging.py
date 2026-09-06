"""Logging behaviour that would otherwise fail silently in production.

Unattended systems fail silently (§12.5), so the logging layer is load-bearing
observability rather than a convenience. Each test here covers a way it could
be quietly wrong while still appearing to work.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest

from meridian_core.logging import bind_run_id, configure_logging, get_logger


@pytest.fixture
def captured() -> io.StringIO:
    """Point Meridian's own handler at a buffer rather than stdout.

    Only handlers carrying our JsonFormatter are redirected — pytest attaches
    handlers of its own, and swapping those too would mix pytest's plain-text
    records into the buffer and make every parse fail.
    """
    from meridian_core.logging import JsonFormatter

    configure_logging("test-service")
    buf = io.StringIO()
    ours = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)
    ]
    assert ours, "configure_logging installed no JSON handler"
    original = [h.stream for h in ours]
    for handler in ours:
        handler.stream = buf
    yield buf
    for handler, stream in zip(ours, original, strict=True):
        handler.stream = stream


def _records(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().strip().splitlines() if line.strip()]


def test_every_record_is_valid_json(captured) -> None:
    """A record that isn't parseable is invisible to any log query."""
    get_logger("t").info("hello", extra={"queue_depth": 12})
    records = _records(captured)
    assert len(records) == 1
    assert records[0]["message"] == "hello"
    assert records[0]["queue_depth"] == 12


def test_configure_logging_is_idempotent(captured) -> None:
    """A second call must not stack a handler and double every line.

    Doubled logs are the classic symptom, and they corrupt any rate or count
    derived from the log stream — like the fetch success rate in the digest.
    """
    configure_logging("test-service")
    configure_logging("test-service")
    get_logger("t").info("once")
    assert len(_records(captured)) == 1


def test_run_id_is_absent_until_bound(captured) -> None:
    """Records outside a run must not claim to belong to one."""
    get_logger("t").info("no run")
    assert "run_id" not in _records(captured)[0]


def test_run_id_is_unbound_after_the_context_exits(captured) -> None:
    """A leaked run_id would misattribute later work to a finished run."""
    log = get_logger("t")
    with bind_run_id("run-1"):
        log.info("inside")
    log.info("outside")
    inside, outside = _records(captured)
    assert inside["run_id"] == "run-1"
    assert "run_id" not in outside


def test_run_id_is_isolated_between_concurrent_tasks(captured) -> None:
    """The orchestrator and API run concurrent tasks; a module-level global
    would bleed one run's id into another's records."""
    log = get_logger("t")

    async def emit(run_id: str, delay: float) -> None:
        with bind_run_id(run_id):
            await asyncio.sleep(delay)
            log.info("concurrent")

    async def main() -> None:
        await asyncio.gather(emit("run-A", 0.02), emit("run-B", 0.01))

    asyncio.run(main())
    seen = {r["run_id"] for r in _records(captured) if r["message"] == "concurrent"}
    assert seen == {"run-A", "run-B"}


def test_exceptions_are_captured_as_text(captured) -> None:
    """exc_info is a tuple — neither JSON-serialisable nor still valid later."""
    try:
        raise ValueError("kaboom")
    except ValueError:
        get_logger("t").exception("failed")
    record = _records(captured)[0]
    assert "ValueError" in record["exception"]
    assert "kaboom" in record["exception"]


def test_noisy_libraries_are_quietened(captured) -> None:
    """Per-statement SQL logging would bury the health line on an unattended box."""
    assert logging.getLogger("sqlalchemy.engine").level >= logging.WARNING
    assert logging.getLogger("asyncpg").level >= logging.WARNING


def test_invalid_level_fails_loudly() -> None:
    """A typo'd level must not silently fall back and hide records."""
    with pytest.raises(RuntimeError):
        configure_logging("test-service", level="LOUD")
