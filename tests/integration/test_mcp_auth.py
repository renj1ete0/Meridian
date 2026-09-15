"""Enforcement on the MCP surface (task P3-03, spec §11.4).

`test_tokens.py` covers the mechanism — issue, resolve, revoke. This covers the
part that decides whether a request is answered, which is a different question
and fails in a different direction: a token that resolves correctly and is then
never checked against the tool it called is a scope that exists on paper.

The refusals are the subject. A caller wrongly refused reports it; a caller
wrongly served does not.
"""

from __future__ import annotations

import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import delete

from api import auth
from api.auth import MeridianTokenVerifier, require_tool
from api.mcp.server import build_mcp
from meridian_core.models import AgentToken
from meridian_core.tokens import issue_token, revoke_token

pytestmark = pytest.mark.usefixtures("require_db")

TOOLS = ["search_chunks", "corpus_overview"]


@pytest.fixture
def agent() -> str:
    return f"mcpauth-{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def cleanup(session_for, agent):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(AgentToken).where(AgentToken.agent_id == agent))
    await sess.commit()


@pytest.fixture(autouse=True)
async def _dispose():
    yield
    from meridian_core.db import dispose_engines

    await dispose_engines()


@pytest.fixture
def anonymous(monkeypatch):
    """Turn the opt-out on and off explicitly, rather than inheriting `.env.dev`.

    A test that silently depended on the developer's environment would pass here
    and fail in CI, or worse, pass in both while testing nothing.
    """

    def _set(allowed: bool) -> None:
        monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", "true" if allowed else "")

    return _set


def as_token(scopes: list[str] | None):
    """A stand-in for a verified bearer token in the request context."""

    class _Token:
        def __init__(self) -> None:
            self.scopes = scopes

    return _Token()


# --------------------------------------------------------------------------
# Failing closed
# --------------------------------------------------------------------------


def test_no_token_and_no_opt_out_is_refused(anonymous, monkeypatch) -> None:
    """The default. A deployment that simply forgot to configure credentials
    refuses callers rather than serving the corpus to them — the opposite
    default is one missing environment variable from publishing everything."""
    anonymous(False)
    monkeypatch.setattr(auth, "get_access_token", lambda: None)

    with pytest.raises(Exception) as caught:
        require_tool("search_chunks")

    assert "requires an access token" in str(caught.value)


def test_the_opt_out_is_explicit_and_narrow(anonymous, monkeypatch) -> None:
    """Anonymous access works, but only because something said so out loud."""
    anonymous(True)
    monkeypatch.setattr(auth, "get_access_token", lambda: None)

    require_tool("search_chunks")  # does not raise


@pytest.mark.parametrize("value", ["", "false", "no", "0", "maybe"])
def test_only_a_real_yes_opens_the_door(monkeypatch, value: str) -> None:
    """A half-set variable — `MERIDIAN_MCP_ALLOW_ANONYMOUS=` from a template, or
    `false` from someone turning it off — must not read as permission."""
    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", value)
    assert auth.allows_anonymous() is False


# --------------------------------------------------------------------------
# The scope is checked per tool, not per connection
# --------------------------------------------------------------------------


def test_a_token_cannot_call_a_tool_it_was_not_scoped_to(anonymous, monkeypatch) -> None:
    """§11.4's whole point: an interactive session holds read tools while only
    the orchestrator's profile carries write ones. That distinction cannot live
    at the transport — by the time a request is authenticated, which tool it
    asked for is the only thing separating them."""
    anonymous(False)
    monkeypatch.setattr(auth, "get_access_token", lambda: as_token(["search_chunks"]))

    require_tool("search_chunks")  # permitted

    with pytest.raises(Exception) as caught:
        require_tool("corpus_overview")
    assert "not scoped to" in str(caught.value)


def test_a_token_with_no_scopes_calls_nothing(anonymous, monkeypatch) -> None:
    """NULL `allowed_tools` authenticates and authorises nothing, and the
    enforcement side must agree with `resolve_token` about that."""
    anonymous(False)
    monkeypatch.setattr(auth, "get_access_token", lambda: as_token(None))

    # `ToolError` specifically: the SDK reports that type to the client with the
    # message intact, where any other exception becomes "error executing tool"
    # and tells the caller nothing it can act on. Raising the right type is part
    # of the behaviour, not an implementation detail.
    with pytest.raises(ToolError):
        require_tool("search_chunks")


def test_the_refusal_names_the_tool_and_not_the_credential(anonymous, monkeypatch) -> None:
    """A caller holding a valid token is entitled to know which capability it
    lacks — that is a scope problem its operator can fix. It is not entitled to
    anything about the credential itself."""
    anonymous(False)
    monkeypatch.setattr(auth, "get_access_token", lambda: as_token(["search_chunks"]))

    with pytest.raises(Exception) as caught:
        require_tool("list_new_since")

    message = str(caught.value)
    assert "list_new_since" in message
    assert "token_id" not in message and "hash" not in message


# --------------------------------------------------------------------------
# The verifier
# --------------------------------------------------------------------------


async def test_the_verifier_reports_the_tokens_tools_as_scopes(session_for, agent, cleanup) -> None:
    """One source of truth. If the SDK's view of a token's scopes and
    `require_tool`'s could differ, the surface would enforce two policies."""
    sess = await session_for("rw")
    secret, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)
    await sess.commit()

    verified = await MeridianTokenVerifier().verify_token(secret)

    assert verified is not None
    assert verified.client_id == agent
    assert set(verified.scopes) == set(TOOLS)


async def test_the_verifier_refuses_a_revoked_token(session_for, agent, cleanup) -> None:
    sess = await session_for("rw")
    secret, row = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)
    await revoke_token(sess, row.token_id)
    await sess.commit()

    assert await MeridianTokenVerifier().verify_token(secret) is None


async def test_the_verifier_refuses_an_unknown_token() -> None:
    assert await MeridianTokenVerifier().verify_token("nobody-issued-this") is None


# --------------------------------------------------------------------------
# Every tool is guarded
# --------------------------------------------------------------------------


#: Minimal valid arguments per tool.
#:
#: Needed because the SDK validates arguments *before* the tool body runs, so
#: calling everything with `{}` tests pydantic rather than the guard. That
#: ordering is worth knowing and is not a leak: when a verifier is configured
#: the transport rejects an unauthenticated request before either happens, and
#: in anonymous mode the schema is public anyway via `list_tools`.
MINIMAL_ARGS = {
    "search_chunks": {"query": "x"},
    "get_source_metadata": {"source_id": 1},
    "list_new_since": {},
    "corpus_overview": {},
}


async def test_no_tool_is_reachable_without_a_scope_check(anonymous, monkeypatch) -> None:
    """A completeness probe over every registered tool.

    A tool added later without `require_tool` is reachable by anyone who can
    reach the transport, and nothing else here would notice — the guard is one
    line and its absence looks like nothing at all.
    """
    anonymous(False)
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    mcp = build_mcp(version="test")

    tools = await mcp.list_tools()
    assert tools, "no tools registered — this probe would be vacuous"

    unknown = {t.name for t in tools} - set(MINIMAL_ARGS)
    assert not unknown, f"new tool with no arguments recorded for this probe: {unknown}"

    for tool in tools:
        # `ToolError` propagates out of `call_tool` rather than becoming an
        # error result — the SDK reports it to the client with the message
        # intact, which is exactly why `require_tool` raises that type.
        with pytest.raises(Exception) as caught:
            await mcp.call_tool(tool.name, MINIMAL_ARGS[tool.name])
        assert "access token" in str(caught.value), (
            f"{tool.name} failed, but not because of the missing credential"
        )


# --------------------------------------------------------------------------
# OAuth discovery, and the resource a token was issued for (P3-05, P3-09)
# --------------------------------------------------------------------------


async def test_the_verifier_stamps_the_resource_the_token_is_for(
    session_for, agent, cleanup
) -> None:
    """Meridian's credentials are rows in this database, issued for this server
    and no other, so naming the resource is simply true — and it is what lets
    `validate_token_resource` refuse a token minted for a *different* resource
    on the same issuer. Without it the surface would accept one, which is the
    same mistake as verifying an Access assertion without its audience.
    """
    sess = await session_for("rw")
    secret, _ = await issue_token(sess, agent_id=agent, allowed_tools=TOOLS)
    await sess.commit()

    resource = "https://meridian.example.test/mcp"
    verified = await MeridianTokenVerifier(resource=resource).verify_token(secret)

    assert verified is not None
    assert verified.resource == resource


def test_the_server_advertises_where_to_authenticate(monkeypatch) -> None:
    """`P3-09`. A hosted client is given a URL and nothing else — no config file,
    no place to put a header — so it has to *discover* how to authenticate.
    That is what the protected-resource metadata is for, and without it a phone
    assistant handed this URL can only fail.
    """
    from api.main import create_app

    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", "")
    monkeypatch.setenv("MERIDIAN_MCP_ISSUER_URL", "https://team.cloudflareaccess.com")
    monkeypatch.setenv("MERIDIAN_MCP_RESOURCE_URL", "https://meridian.example.test/mcp")

    app = create_app()
    routes = [getattr(r, "path", "") for r in app.state.mcp.streamable_http_app().routes]

    assert any(".well-known/oauth-protected-resource" in path for path in routes)


def test_anonymous_mode_advertises_no_authentication(monkeypatch) -> None:
    """The converse, and it is not cosmetic: a server advertising an
    authorization endpoint it does not enforce would send a client through an
    OAuth flow for nothing."""
    from api.main import create_app

    monkeypatch.setenv("MERIDIAN_MCP_ALLOW_ANONYMOUS", "true")
    app = create_app()
    routes = [getattr(r, "path", "") for r in app.state.mcp.streamable_http_app().routes]

    assert not any(".well-known" in path for path in routes)
