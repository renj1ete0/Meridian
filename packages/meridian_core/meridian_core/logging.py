"""Structured JSON logging, shared by every Meridian service.

One JSON object per line on stdout, which Docker's json-file driver collects. Standard
library only. `run_id` is carried by a ``contextvars.ContextVar``, so concurrent asyncio
tasks keep their own. See docs/features/operations.md#logging.
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

# Populated once by configure_logging(); read by JsonFormatter. A ContextVar like
# run_id, so there is one mechanism.
_service: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "meridian_service", default=None
)

# Attributes stdlib LogRecord always carries; anything else came from `extra={...}`.
_STANDARD_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=None, exc_info=None
    ).__dict__
)

_DEFAULT_LEVEL: Final[str] = "INFO"

# Third-party libraries quietened (every SQL statement, every checkout), but
# overridable for debugging.
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
    # An empty or unparseable page is logged at ERROR by trafilatura, and the extractor already
    # records it as a failed extraction; at ERROR it counted against the worker's health.
    "trafilatura": logging.CRITICAL,
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

        # Formatted inline: exc_info is not JSON-serialisable, and a grep for
        # "Traceback" needs the text.
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    """Attach ``run_id`` to every log record emitted within this context.

    A ContextVar, so it follows this task and not its concurrent siblings.
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


# Set on the root logger once our handler is installed, so a second call updates
# the level and service instead of doubling every line.
_CONFIGURED_MARKER: Final[str] = "_meridian_json_configured"


def configure_logging(service: str, level: str | None = None) -> None:
    """Install JSON stdout logging for this process. Call once at startup.

    Every service (worker, orchestrator, API) calls this before doing anything else, per AGENTS.md's
    "structured logging; every run logs run_id." Idempotent: safe to call again (e.g. from a test
    fixture, or a module imported twice) without doubling handlers.
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
    """Return a standard logger.

    Records pass through the JSON formatter installed by :func:`configure_logging`.
    """
    return logging.getLogger(name)
