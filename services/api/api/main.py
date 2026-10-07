"""The API service (task P2-07, spec §12.6, scaffold §5).

Serves `/api/explore/*`, `/api/admin/*`, the MCP surface at `/mcp` and `/health`. Built by
`create_app` rather than at import, so tests can mount different routers. No CORS: the
web app is same-origin. See docs/features/api-and-access.md.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from meridian_core.db import check_connection, dispose_engines
from meridian_core.logging import bind_run_id, configure_logging, get_logger

from .access import AccessSettings, AccessVerifier, access_middleware
from .mcp.server import build_mcp
from .routes import (
    admin,
    connect,
    explore,
    gaps,
    graph,
    growth,
    neighbourhood,
    steering_proposals,
    tokens,
)

log = get_logger(__name__)

#: Where the MCP surface mounts. A client is given this path, so moving it
#: breaks every configured client — it is API, not a detail.
MCP_PATH = "/mcp"

#: Names this server when `MERIDIAN_MCP_ISSUER_URL` is unset. Tokens are Meridian's own, so a
#: placeholder is enough for verification; set the real public URL behind a tunnel.
DEFAULT_MCP_ISSUER = "http://localhost"


def mcp_auth() -> dict[str, object]:
    """Whether the MCP surface verifies tokens, and against what.

    Anonymous mode is the opt-out (`MERIDIAN_MCP_ALLOW_ANONYMOUS`). Tokens are Meridian's own
    (`meridian_core.tokens`), so verification needs no external issuer; the URLs default to
    local placeholders and only name this server in the metadata a client may read.
    See docs/features/mcp.md.
    """
    from .auth import MeridianTokenVerifier, allows_anonymous

    if allows_anonymous():
        log.warning(
            "mcp surface allows anonymous access",
            extra={"reason": "MERIDIAN_MCP_ALLOW_ANONYMOUS is set"},
        )
        return {}

    # Unset, the surface still verifies Meridian's own tokens (`B-138`): they are issued and
    # checked here, so the URLs only name this server in the OAuth metadata clients may read.
    # Requiring them made every issued token useless on a deployment that had not set them.
    issuer = os.environ.get("MERIDIAN_MCP_ISSUER_URL") or DEFAULT_MCP_ISSUER
    resource = os.environ.get("MERIDIAN_MCP_RESOURCE_URL") or f"{issuer.rstrip('/')}{MCP_PATH}"

    return {
        "token_verifier": MeridianTokenVerifier(resource=resource),
        "auth": AuthSettings(
            issuer_url=issuer,
            resource_server_url=resource,
            # Refuse a token issued for a different resource (off in the SDK until 3.0).
            validate_token_resource=True,
        ),
    }


def transport_security() -> TransportSecuritySettings:
    """Which Host and Origin headers the MCP transport will answer.

    From the environment; unset means loopback only, so a tunnel added without
    setting it is refused rather than open.
    """
    hosts = [h for h in os.environ.get("MERIDIAN_MCP_ALLOWED_HOSTS", "").split(",") if h]
    origins = [o for o in os.environ.get("MERIDIAN_MCP_ALLOWED_ORIGINS", "").split(",") if o]
    return TransportSecuritySettings(
        allowed_hosts=hosts or ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
        allowed_origins=origins or ["http://127.0.0.1:*", "http://localhost:*"],
    )


async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One structured record per request, and a run_id that ties it to its work.

    Replaces uvicorn's plain-text access log, which the Dockerfile disables. Each
    request is a run, with its own per-task run id. See docs/features/api-and-access.md.
    """
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on the way up, close the pool on the way down.

    Engines are disposed on shutdown, because `max_connections` is shared by every
    service (scaffold §3).
    """
    configure_logging("api")
    log.info("api starting", extra={"routes": len(app.routes)})

    # Streamable HTTP keeps per-connection state, so its manager runs for the app's
    # life; mounted but not run, it fails on the first message.
    mcp = app.state.mcp
    async with mcp.session_manager.run():
        log.info("mcp surface ready", extra={"path": MCP_PATH})
        try:
            yield
        finally:
            await dispose_engines()
            log.info("api stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Meridian",
        version=os.environ.get("MERIDIAN_VERSION", "0"),
        summary="Read surface over the corpus and the graph.",
        lifespan=lifespan,
    )
    app.middleware("http")(log_requests)

    # `P3-08`. Installed only when a team domain is configured. See
    # docs/features/api-and-access.md#identity.
    access = AccessSettings.from_env()
    if access is not None:
        app.middleware("http")(access_middleware(AccessVerifier(access)))
        log.info("cloudflare access verification enabled", extra={"team": access.team_domain})
    else:
        log.info(
            "cloudflare access verification not configured",
            extra={"fix": "set CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD when behind a tunnel"},
        )
    app.include_router(explore.router)
    # The graph workspace's reads (`P6-01`–`P6-03`): under `/api/explore`, so
    # on the read-only role like every other Explore route.
    app.include_router(graph.router)
    # A term's neighbourhood (`P6-33`), read-only for the same reason.
    app.include_router(neighbourhood.router)
    # Routes across claims and resemblance (`P6-32`), read-only likewise.
    app.include_router(connect.router)

    # `/api/admin/*` (`P6-13`). Mounted unconditionally and gated per handler by
    # `admin_is_allowed`, so a misconfiguration is a 503, not a 404.
    app.include_router(admin.router)
    # Gaps (`P6-36`): the list reads on `/api/explore`, the actions write on
    # `/api/admin` — one module, two routers, the prefix still the role boundary.
    app.include_router(gaps.explore_router)
    app.include_router(gaps.admin_router)
    # Steering proposals (`P6-38`): list, accept, reject — all under `/api/admin`.
    app.include_router(steering_proposals.router)
    app.include_router(growth.router)
    app.include_router(tokens.router)

    # The MCP surface (`P3-01`), on the same app: one corpus, role and validation
    # layer (§11.1b). Authentication is `P3-03`; see docs/features/mcp.md.
    mcp = build_mcp(version=os.environ.get("MERIDIAN_VERSION", "0"), **mcp_auth())
    app.state.mcp = mcp
    app.mount(
        MCP_PATH,
        # `streamable_http_path="/"`: the default, mounted at `/mcp`, would publish
        # `/mcp/mcp`.
        mcp.streamable_http_app(
            streamable_http_path="/",
            # DNS-rebinding protection, off by default in the SDK and needed behind
            # a tunnel (`P3-05`).
            transport_security=transport_security(),
        ),
    )

    @app.exception_handler(RequestValidationError)
    async def readable_validation_error(request: Request, exc: RequestValidationError):
        """Make `detail` a string, always (`P2-18`).

        Validation errors are joined into prose; the structured form is kept under
        `errors`.
        """
        parts = []
        for error in exc.errors():
            location = ".".join(str(piece) for piece in error.get("loc", ()) if piece != "query")
            parts.append(
                f"{location}: {error.get('msg', 'invalid')}"
                if location
                else error.get("msg", "invalid")
            )
        # A `field_validator` that raises puts the exception object itself in
        # `ctx`, which JSON cannot encode — the refusal became a 500 (found by
        # `P6-36`'s seed validator). Encoded with exceptions as their message.
        errors = jsonable_encoder(exc.errors(), custom_encoder={Exception: str})
        return JSONResponse(
            status_code=422,
            content={"detail": "; ".join(parts) or "invalid request", "errors": errors},
        )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, object]:
        """Liveness plus whether the read role can actually reach the database.

        Says nothing about the corpus. See docs/features/api-and-access.md#design-choices.
        """
        return {"status": "ok", "database": await check_connection("ro")}

    return app


app = create_app()
