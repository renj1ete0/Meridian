"""One structured log record per request (task P2-07), shared by the API and the connector.

Replaces uvicorn's plain-text access log, which the Dockerfile disables. Each request is a
run, with its own run id. See docs/features/api-and-access.md.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from meridian_core.logging import bind_run_id, get_logger

log = get_logger("api.main")


async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log the request with its status and duration, and hand back an `x-request-id`."""
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    with bind_run_id(f"req-{request_id}"):
        response = await call_next(request)
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
    # Handed back so a caller chasing a slow or failed request can quote it.
    response.headers["x-request-id"] = request_id
    return response
