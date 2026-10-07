"""Authenticating the MCP surface (task P3-03, spec §11.4).

The bridge between a bearer token on the wire and a refusal inside a tool. Anonymous
access is an explicit opt-out (`MERIDIAN_MCP_ALLOW_ANONYMOUS`). The transport verifies
the token, and each tool checks that the token was scoped to it.
See docs/features/mcp.md#authentication.
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

    Reports the token's `allowed_tools` as its scopes, and stamps this server as the
    token's resource so `validate_token_resource` can refuse tokens for any other.
    See docs/features/mcp.md#authentication.
    """

    def __init__(self, resource: str | None = None) -> None:
        self._resource = resource

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
            resource=self._resource,
        )


def require_tool(name: str) -> None:
    """Refuse unless the caller's token was scoped to this tool.

    Raises `ToolError`, which the client sees with its message intact. The message
    names the tool, never the token.
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
