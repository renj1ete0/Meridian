"""Structured JSON logging, shared by every Meridian service.

The ingestion node runs unattended for weeks (§13.4: self-healing so absence is
safe) and the only observability surface is the daily health line — queue depth,
fetch success rate, novelty pass rate, edges added (§12.5). None of that is
worth anything if the underlying log records aren't structured: "unattended
systems fail silently" means every record has to be greppable/parseable from
day one, not retrofitted after the first silent failure.

Docker runs every service with the json-file logging driver plus rotation, so
stdout is the collection point — there is no separate log shipper. That means
the format decision is made here, once, rather than per-service: one JSON
object per line on stdout.

Standard library only (``logging`` + a custom ``Formatter``). The ingestion box
is a memory-constrained arm64 node and the dependency list is deliberately
short (AGENTS.md); structlog/loguru buy convenience this project has decided
not to pay for.

A synthesis run spans many modules and, under the API and orchestrator, many
concurrent asyncio tasks. Threading a logger (or a run_id) through every call
signature would leak the concern into unrelated code, and a plain global
variable would bleed one request's run_id into another's concurrently-running
task. ``contextvars.ContextVar`` is the one primitive that gets both:
context.run() and asyncio tasks each get their own copy, isolated from
siblings, while still being ambient rather than explicit.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

# Populated by bind_run_id(); read by JsonFormatter on every record. None
# means "no run in progress" (e.g. a one-off CLI invocation or a health check),
# in which case the field is simply omitted rather than emitted as null.
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "meridian_run_id", default=None
)

# Populated once by configure_logging(); read by JsonFormatter. Unlike run_id
# this doesn't change per-task, but it's still a ContextVar (not a module
# global) so it participates in the same copy-on-context-switch semantics and
# there is exactly one mechanism to reason about, not two.
_service: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "meridian_service", default=None
)

# Attributes stdlib LogRecord always carries. Anything a caller passes via
# `extra={...}` shows up as an attribute NOT in this set, which is how we tell
# "caller-supplied field" apart from "stdlib bookkeeping" without an allowlist
# that would have to be kept in sync with logging internals by hand.
_STANDARD_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=None, exc_info=None
    ).__dict__
)

_DEFAULT_LEVEL: Final[str] = "INFO"

# Third-party libraries log at INFO/DEBUG far more chattily than this project
# wants on an unattended box (every SQL statement, every connection checkout).
# Kept overridable per AGENTS.md's "structured logging" requirement not meaning
# "no way to turn the noise back on" when actually debugging a connection issue.
_NOISY_DEFAULTS: Final[dict[str, int]] = {
    "sqlalchemy.engine": logging.WARNING,
    "asyncpg": logging.WARNING,
    # One `httpx` line per request would double the crawl log, and the fetcher
    # already logs every fetch with the fields that matter.
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    # Loading bge-m3 emits ~40 INFO lines of HEAD requests against the Hub
    # before it says anything useful. That is a model load, not an event.
    "huggingface_hub": logging.WARNING,
    "sentence_transformers": logging.WARNING,
    "transformers": logging.WARNING,
    "urllib3": logging.WARNING,
    "filelock": logging.WARNING,
}


class JsonFormatter(logging.Formatter):
    """Renders one LogRecord as one JSON line.

    Deliberately not using ``logging.Formatter``'s ``fmt`` string machinery:
    a format string can't conditionally omit a field (run_id when unbound) or
    nest arbitrary extras, and both are required here.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        service = _service.get()
        if service is not None:
            payload["service"] = service

        run_id = _run_id.get()
        if run_id is not None:
            payload["run_id"] = run_id

        # Caller-supplied extra= fields. Explicit loop rather than dict-diffing
        # against __dict__ wholesale so ordering is stable and predictable.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value

        # Formatted here (not left as exc_info on the record) because exc_info
        # is a (type, value, traceback) tuple — not JSON-serialisable — and a
        # health check grepping for "Traceback" needs the text inline, not a
        # pointer to object state that's gone by the time anything reads it.
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    """Attach ``run_id`` to every log record emitted within this context.

    A synthesis run (§13.4) crosses many function calls and, in the
    orchestrator and API, concurrent asyncio tasks. Binding once here and
    reading it in JsonFormatter beats passing run_id as an argument through
    every intermediate call — and beats a module-level global, which would
    leak across concurrently-running tasks instead of following just this one.
    """
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


def _resolve_level(level: str | None) -> int:
    raw = level or os.environ.get("MERIDIAN_LOG_LEVEL") or _DEFAULT_LEVEL
    resolved = logging.getLevelName(raw.strip().upper())
    if not isinstance(resolved, int):
        raise RuntimeError(f"MERIDIAN_LOG_LEVEL must be a valid level name, got {raw!r}")
    return resolved


# Set on the root logger once configure_logging has installed our handler, so
# a second call (a library re-importing a service module, a test harness
# calling it per-test) is a level/service update rather than a second handler
# stacking duplicate output onto every record — the classic doubled-log-lines bug.
_CONFIGURED_MARKER: Final[str] = "_meridian_json_configured"


def configure_logging(service: str, level: str | None = None) -> None:
    """Install JSON stdout logging for this process. Call once at startup.

    Every service (worker, orchestrator, API) calls this before doing
    anything else, per AGENTS.md's "structured logging; every run logs
    run_id." Idempotent: safe to call again (e.g. from a test fixture, or a
    module imported twice) without doubling handlers.
    """
    _service.set(service)
    root = logging.getLogger()
    root.setLevel(_resolve_level(level))

    if not getattr(root, _CONFIGURED_MARKER, False):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        setattr(root, _CONFIGURED_MARKER, True)

    for logger_name, default_level in _NOISY_DEFAULTS.items():
        noisy = logging.getLogger(logger_name)
        # Only apply the default while nothing more specific has been set
        # (NOTSET, logging's "inherit" sentinel) — an explicit override made
        # before or after configure_logging() must win either way.
        if noisy.level == logging.NOTSET:
            noisy.setLevel(default_level)


def get_logger(name: str) -> logging.Logger:
    """Return a standard logger; records pass through the JSON formatter
    installed by :func:`configure_logging`."""
    return logging.getLogger(name)
