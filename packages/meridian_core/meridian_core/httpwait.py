"""Reading a server's "try again later" (`B-248`, ADR 0023).

A model server that loads on request answers ``503`` or ``429`` with ``Retry-After`` while
the model loads, as hosted APIs do when overloaded. See
docs/features/embedding.md#a-server-that-loads-on-request.
"""

from __future__ import annotations

import email.utils
import math
import time
from collections.abc import Mapping

#: Statuses that mean "not now" rather than "failed".
WAIT_STATUSES = frozenset({429, 503})

#: The longest wait taken from a server; a larger value is a misconfigured server, and
#: honouring it would park a dependency for hours.
MAX_WAIT_S = 600.0


def retry_after_s(headers: Mapping[str, str], default: float, *, now: float | None = None) -> float:
    """Seconds to wait from ``Retry-After`` (seconds or an HTTP date), else ``default``.

    Never negative, never above :data:`MAX_WAIT_S`. ``retry-after-ms`` wins when present,
    as the OpenAI and Anthropic clients read it.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    wait: float | None = None
    raw_ms = lowered.get("retry-after-ms", "").strip()
    raw = lowered.get("retry-after", "").strip()
    if raw_ms:
        wait = _number(raw_ms)
        wait = wait / 1000 if wait is not None else None
    if wait is None and raw:
        wait = _number(raw)
        if wait is None:
            try:
                when = email.utils.parsedate_to_datetime(raw)
            except TypeError, ValueError:
                when = None
            if when is not None and when.tzinfo is not None:
                wait = when.timestamp() - (time.time() if now is None else now)
    if wait is None:
        wait = default
    return min(max(wait, 0.0), MAX_WAIT_S)


def _number(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
