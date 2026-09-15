"""The API service (task P2-07, spec §12.6, scaffold §5).

Serves `/api/explore/*` today. `/api/admin/*` and the MCP surface (`P3-01`)
mount here later, which is why the app is assembled by a factory rather than
created at import: a test, a health check and a future admin build-out all need
to construct it with different routers mounted, and a module-level `app = ...`
makes that a monkeypatch.

**No CORS middleware, deliberately.** The frontend reaches this through Vite's
dev proxy in development and through the same origin behind `cloudflared` in
production (`P2-11` and scaffold §5 both turn on there being no
environment-specific base URL), so there is no cross-origin request to permit.
Adding permissive CORS "just in case" would make the API answerable from any
page a reader happens to have open — which, for a service fronted by Cloudflare
Access as its only auth (§12.6), hands away exactly what Access is protecting.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from meridian_core.db import check_connection, dispose_engines
from meridian_core.logging import bind_run_id, configure_logging, get_logger

from .access import AccessSettings, AccessVerifier, access_middleware
from .mcp.server import build_mcp
from .routes import explore

log = get_logger(__name__)

#: Where the MCP surface mounts. A client is given this path, so moving it
#: breaks every configured client — it is API, not a detail.
MCP_PATH = "/mcp"


def mcp_auth() -> dict[str, object]:
    """Whether the MCP surface verifies tokens, and against what.

    Anonymous mode is deliberately the *opt-out*. The alternative — open unless
    configured — is one forgotten environment variable away from publishing the
    corpus, and the person who forgets is deploying rather than reading this.

    `AuthSettings` is only constructed when authentication is on, because it
    requires issuer and resource URLs that a loopback development machine has no
    meaningful answer for, and demanding them there would push everybody towards
    turning auth off to get started.
    """
    from .auth import MeridianTokenVerifier, allows_anonymous

    if allows_anonymous():
        log.warning(
            "mcp surface allows anonymous access",
            extra={"reason": "MERIDIAN_MCP_ALLOW_ANONYMOUS is set"},
        )
        return {}

    issuer = os.environ.get("MERIDIAN_MCP_ISSUER_URL")
    resource = os.environ.get("MERIDIAN_MCP_RESOURCE_URL")
    if not (issuer and resource):
        # No credentials configured and no opt-out: the tools will refuse
        # everything. Said loudly, because the symptom is every call failing
        # and the cause is an absent environment variable.
        log.warning(
            "mcp surface has no verifier; every tool will refuse",
            extra={
                "fix": "set MERIDIAN_MCP_ISSUER_URL and MERIDIAN_MCP_RESOURCE_URL, "
                "or MERIDIAN_MCP_ALLOW_ANONYMOUS for local use"
            },
        )
        return {}

    return {
        "token_verifier": MeridianTokenVerifier(),
        "auth": AuthSettings(issuer_url=issuer, resource_server_url=resource),
    }


def transport_security() -> TransportSecuritySettings:
    """Which Host and Origin headers the MCP transport will answer.

    Read from the environment rather than hardcoded, because the answer is a
    deployment fact: locally it is loopback, in production it is the hostname
    Cloudflare fronts. Unset means loopback only, which is the safe default for
    a service that is not yet exposed — and a deployment that puts this behind
    a tunnel without setting it will find the tunnel refused rather than
    silently reachable from anywhere.
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

    Uvicorn's own access log is plain text, and `meridian_core.logging` exists
    because Docker's json-file driver makes stdout the only collection point:
    one JSON object per line, greppable by severity from day one. Interleaving
    two formats there means a health check parsing the stream hits a line it
    cannot read — so uvicorn's access log is disabled in the Dockerfile CMD and
    this replaces it.

    `bind_run_id` is the other half. AGENTS.md requires every run to log one,
    and under an API "a run" is a request: several concurrent requests each log
    from the same modules, and without a correlation id their records interleave
    into something no one can separate afterwards. The ContextVar is per-task,
    so requests cannot bleed ids into each other.
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

    Engines are disposed on shutdown rather than left to the interpreter: the
    container is stopped with SIGTERM and Postgres runs with `max_connections=40`
    shared across every service (scaffold §3), so a deploy that abandoned its
    pool would spend other services' connection budget until the server reaped
    them.
    """
    configure_logging("api")
    log.info("api starting", extra={"routes": len(app.routes)})

    # The MCP transport keeps per-connection state — streamable HTTP is a
    # long-lived session, not a request/response — so its manager has to be
    # entered for the life of the app. Mounting the sub-app without running it
    # gives a route that accepts a connection and then fails on the first
    # message, which reads as a client bug rather than a missing lifespan.
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

    # `P3-08`. Installed only when a team domain is configured: this is for a
    # service behind a tunnel, and demanding it on a loopback machine would push
    # everyone into disabling it. `P3-03`'s token check does not depend on
    # deployment shape and fails closed on its own, so an un-fronted deployment
    # is not left open by this being absent.
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

    # §11.1's agent-initiated direction (`P3-01`). Mounted on the same app on
    # purpose: it is the same corpus, the same read-only role and the same
    # provenance, and §11.1b is explicit that all three integration directions
    # hit one validation layer with none privileged. A separate service would
    # be a second place for that to drift.
    #
    # Authentication is `P3-03`: a bearer token resolved against `agent_tokens`,
    # plus a per-tool scope check that runs whether or not a verifier is
    # configured. Anonymous access is an explicit opt-out, so a deployment that
    # simply forgets to configure credentials refuses callers rather than
    # serving them. `P3-05` (Cloudflare Access) is the layer in front.
    mcp = build_mcp(version=os.environ.get("MERIDIAN_VERSION", "0"), **mcp_auth())
    app.state.mcp = mcp
    app.mount(
        MCP_PATH,
        # `streamable_http_path="/"` because the sub-app serves `/mcp` by
        # default and mounting *that* at `/mcp` would publish `/mcp/mcp`. The
        # symptom is a client that connects and gets "Not Found" from
        # `initialize`, which reads as a protocol mismatch rather than a path
        # one.
        mcp.streamable_http_app(
            streamable_http_path="/",
            # DNS-rebinding protection. It is off by default and matters the
            # moment this is behind a tunnel (`P3-05`): without it a page on
            # any origin can point a hostname at loopback and drive the MCP
            # surface through the reader's own browser.
            transport_security=transport_security(),
        ),
    )

    @app.exception_handler(RequestValidationError)
    async def readable_validation_error(request: Request, exc: RequestValidationError):
        """Make `detail` a string, always (`P2-18`).

        FastAPI hands back a string from `HTTPException` and a list of error
        objects from validation, so every client must normalise both shapes or
        render `[object Object]` at the one moment a user needs to read the
        message. That is FastAPI's convention rather than a bug, but each
        consumer re-pays it — and a browser, an MCP client and a curl user are
        three consumers already.

        The structured form is kept alongside under `errors`, because a client
        that wants to highlight the offending field should not have to parse
        prose to find it.
        """
        parts = []
        for error in exc.errors():
            location = ".".join(str(piece) for piece in error.get("loc", ()) if piece != "query")
            parts.append(
                f"{location}: {error.get('msg', 'invalid')}"
                if location
                else error.get("msg", "invalid")
            )
        return JSONResponse(
            status_code=422,
            content={"detail": "; ".join(parts) or "invalid request", "errors": exc.errors()},
        )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, object]:
        """Liveness plus whether the read role can actually reach the database.

        Both, because they fail separately and the difference decides what to do
        about it: a process that is up and cannot reach Postgres is a database
        or credential problem, and restarting the container fixes neither.

        Deliberately says nothing about the corpus. §12.5 puts the interesting
        detail on the health *line* in the logs; an endpoint that reported queue
        depth and crawl rates would be describing the operator's research
        activity to anyone who can reach it.
        """
        return {"status": "ok", "database": await check_connection("ro")}

    return app


app = create_app()
