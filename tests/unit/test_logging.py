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


# --------------------------------------------------------------------------
# `extra` keys that shadow a LogRecord attribute (task `P4-02`)
# --------------------------------------------------------------------------
#
# `logging` refuses to let an `extra` key shadow a `LogRecord` attribute, and
# it **raises** rather than dropping the key — so the offending line takes down
# whatever called it. The handover has carried this since a scheduler died on
# `extra={"module": ...}`, and carrying it was not enough: `P4-02` shipped
# `extra={"name": ...}`, which passed every test run in isolation and failed
# the moment the suite configured logging.
#
# That is the shape worth guarding. The failure is invisible until logging is
# set up, which is exactly when it is least convenient, so this greps the
# source instead of waiting for a code path to be exercised.

import ast as _ast
import pathlib as _pathlib

#: Every attribute `logging.LogRecord.__init__` sets. Derived from a real
#: record rather than typed out, so a new attribute in a future Python is
#: covered without anyone remembering this test exists.
RESERVED = frozenset(
    vars(
        logging.LogRecord(
            name="n", level=20, pathname="p", lineno=1, msg="m", args=(), exc_info=None
        )
    )
) | {"message", "asctime"}

SOURCE_ROOTS = ("packages", "services", "scripts", "migrations")


def _extra_keys(tree: _ast.AST):
    """Every literal key passed as `extra={...}` anywhere in a module."""
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, _ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, _ast.Constant) and isinstance(key.value, str):
                    yield key.value, node.lineno


def test_the_reserved_set_was_actually_derived() -> None:
    """Guard on the derivation. An empty set would make the sweep below pass
    over everything."""
    assert {"name", "module", "args", "levelname", "lineno"} <= RESERVED


def test_no_log_call_shadows_a_logrecord_attribute() -> None:
    """A shadowed key is not dropped — it raises, on the line that logs it.

    Prefix them: `entity_name`, not `name`; `job_module`, not `module`.
    """
    repo = _pathlib.Path(__file__).resolve().parents[2]
    offences: list[str] = []

    for root in SOURCE_ROOTS:
        for path in (repo / root).rglob("*.py"):
            tree = _ast.parse(path.read_text(errors="ignore"), filename=str(path))
            for key, line in _extra_keys(tree):
                if key in RESERVED:
                    offences.append(f"{path.relative_to(repo)}:{line} extra={{{key!r}: ...}}")

    assert not offences, (
        "these `extra` keys shadow a LogRecord attribute and will raise when "
        f"the line is reached with logging configured: {offences}"
    )
