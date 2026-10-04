"""Issue, list and revoke MCP tokens for external assistants (task `B-138`, ADR 0003).

    docker compose exec api python -m api.tokens issue laptop-claude --profile reader
    docker compose exec api python -m api.tokens list
    docker compose exec api python -m api.tokens revoke 12

Runs in the API container, which holds the read-write database connection. The secret is
printed once and only its hash is stored. See docs/guides/connecting-an-assistant.md.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys

from meridian_core.db import dispose_engines, session
from meridian_core.grants import PROFILE_TOOLS
from meridian_core.logging import configure_logging
from meridian_core.timefmt import display_zone, format_instant
from meridian_core.tokens import DEFAULT_DAYS, issue_token, list_tokens, revoke_token

#: Where `/mcp` is printed as reachable when nothing more specific is configured.
PLACEHOLDER_URL = "http://<this-server>:<web-port>/mcp"


def mcp_url() -> str:
    """The URL to give a client: the configured public one, or a placeholder to fill in."""
    return os.environ.get("MERIDIAN_MCP_RESOURCE_URL") or PLACEHOLDER_URL


def exposure_note(url: str) -> str:
    """Who can use this token, in plain words (ADR 0003)."""
    if url.startswith("https://"):
        return (
            "This deployment has a public URL. Anyone holding this token can read the whole "
            "corpus from anywhere until it expires or is revoked."
        )
    return (
        "Anyone who holds this token and can reach the URL in the setup below (this server, or "
        "your LAN or VPN if the port is published) can read the whole corpus until it expires "
        "or is revoked."
    )


def client_setup(url: str, secret: str) -> str:
    """Copy-ready setup for the clients the operator uses."""
    return f"""\
Claude Code:
  claude mcp add --transport http meridian {url} --header "Authorization: Bearer {secret}"

Gemini CLI (~/.gemini/settings.json):
  "mcpServers": {{
    "meridian": {{
      "httpUrl": "{url}",
      "headers": {{ "Authorization": "Bearer {secret}" }}
    }}
  }}"""


async def _issue(name: str, profile: str, days: int | None) -> int:
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(days=days) if days else None
    async with session() as sess:
        secret, row = await issue_token(
            sess, agent_id=name, allowed_tools=sorted(PROFILE_TOOLS[profile]), expires_at=expires
        )
        zone = await display_zone(sess)
        await sess.commit()
    url = mcp_url()
    until = format_instant(expires, zone) if expires else "never (revoke it when done)"
    print(f"Token {row.token_id} for {name!r}, profile {profile}, expires {until}.")
    print(f"Tools: {', '.join(sorted(PROFILE_TOOLS[profile]))}")
    print()
    print(f"  {secret}")
    print()
    print("This is the only time the token is shown. Store it in the client now.")
    print(exposure_note(url))
    print()
    print(client_setup(url, secret))
    return 0


async def _list(include_revoked: bool) -> int:
    async with session() as sess:
        rows = await list_tokens(sess, include_revoked=include_revoked)
        zone = await display_zone(sess)
    if not rows:
        print("No tokens.")
        return 0
    now = dt.datetime.now(dt.UTC)
    for row in rows:
        state = (
            "revoked"
            if row.revoked
            else ("expired" if row.expires_at and row.expires_at <= now else "active")
        )
        expires = format_instant(row.expires_at, zone) if row.expires_at else "never (no expiry)"
        tools = ", ".join(sorted(row.allowed_tools or [])) or "no tools"
        print(f"{row.token_id:>5}  {state:<8} {row.agent_id:<28} expires {expires}  [{tools}]")
    return 0


async def _revoke(token_id: int) -> int:
    async with session() as sess:
        found = await revoke_token(sess, token_id)
        await sess.commit()
    print(f"Token {token_id} revoked." if found else f"No token {token_id}.")
    return 0 if found else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m api.tokens", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="issue a token and print it once")
    issue.add_argument("name", help="who or what holds it, e.g. laptop-claude-code")
    issue.add_argument("--profile", choices=sorted(PROFILE_TOOLS), default="reader")
    issue.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help="days until it expires; 0 for never"
    )
    listing = sub.add_parser("list", help="list tokens (never their secrets)")
    listing.add_argument("--all", action="store_true", help="include revoked tokens")
    revoke = sub.add_parser("revoke", help="turn a token off")
    revoke.add_argument("token_id", type=int)
    args = parser.parse_args(argv)

    # Warnings only: the JSON log would interleave with the printed token and setup.
    configure_logging("tokens", level="WARNING")

    async def run() -> int:
        try:
            if args.command == "issue":
                return await _issue(args.name, args.profile, args.days or None)
            if args.command == "list":
                return await _list(args.all)
            return await _revoke(args.token_id)
        finally:
            await dispose_engines()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
