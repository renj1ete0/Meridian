"""Authenticating the MCP surface (task P3-03, spec §11.4).

`meridian_core.tokens` is the mechanism — issue, resolve, revoke, with a tool
scope. This is the enforcement: the bridge between a bearer token on the wire
and a refusal inside a tool.

**Anonymous access is an explicit opt-out, not a default.** The surface refuses
unauthenticated callers unless `MERIDIAN_MCP_ALLOW_ANONYMOUS` is set, because
the alternative default — open unless configured — is one forgotten environment
variable away from publishing the corpus, and the person who forgets will be
deploying rather than reading this file. Development sets the opt-out in
`.env.dev`, where it is visible and local.

**Two checks, not one.** The transport verifies the token; each tool then checks
that *this* token was scoped to *it*. §11.4 is specific that the point of a
scope is that an interactive session holds read-only tools while only the
orchestrator's profile carries write ones, and that distinction cannot live at
the transport — by the time the request is authenticated, which tool was asked
for is the only thing that separates them.
"""

from __future__ import annotations

import os

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.mcpserver.exceptions import ToolError

from meridian_core.db import session_ro
from meridian_core.logging import get_logger
from meridian_core.tokens import resolve_token

log = get_logger(__name__)


def allows_anonymous() -> bool:
    """Whether an unauthenticated caller may use the tools.

    Read per call rather than captured at import so a test can flip it without
    rebuilding the server, and so the answer in a log line is the answer that
    was actually applied.
    """
    return os.environ.get("MERIDIAN_MCP_ALLOW_ANONYMOUS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


class MeridianTokenVerifier(TokenVerifier):
    """Resolves a bearer token against `agent_tokens`.

    The scopes it reports are the token's `allowed_tools`, so the SDK's own
    machinery and :func:`require_tool` are reading one source rather than two
    that can disagree.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        async with session_ro() as sess:
            scope = await resolve_token(sess, token)

        if scope is None:
            # `resolve_token` already logged which of unknown/revoked/expired it
            # was. Nothing more is said here, and nothing is said to the caller.
            return None

        log.info(
            "mcp authenticated",
            extra={"agent_id": scope.agent_id, "token_id": scope.token_id},
        )
        return AccessToken(
            token=token,
            client_id=scope.agent_id,
            scopes=sorted(scope.allowed_tools),
        )


def require_tool(name: str) -> None:
    """Refuse unless the caller's token was scoped to this tool.

    Raises `ToolError`, not a bare exception: the SDK reports it to the client
    as a tool failure with the message intact, where an unexpected exception
    becomes "error executing tool" and tells the caller nothing it can act on.

    The message deliberately names the tool and not the token. A caller that
    presented a valid credential is entitled to know which capability it lacks —
    that is a scope problem it can ask its operator to fix — and is not entitled
    to anything about the credential itself.
    """
    token = get_access_token()

    if token is None:
        if allows_anonymous():
            return
        raise ToolError(
            "This Meridian requires an access token. Present one as a bearer "
            "token, or ask the operator to issue a scoped credential."
        )

    if name not in (token.scopes or ()):
        raise ToolError(
            f"This credential is not scoped to `{name}`. "
            "Ask the operator to widen it, or use a tool it does carry."
        )
