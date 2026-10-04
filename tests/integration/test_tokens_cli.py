"""Issuing tokens for external assistants (task `B-138`, ADR 0003)."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import delete, select

from api import tokens as cli
from meridian_core.grants import PROFILE_TOOLS
from meridian_core.models import AgentToken
from meridian_core.tokens import hash_token

pytestmark = pytest.mark.usefixtures("require_db")

NAME = "test-tokens-cli"


@pytest.fixture(autouse=True)
async def cleanup(session_for):
    yield
    sess = await session_for("rw")
    await sess.execute(delete(AgentToken).where(AgentToken.agent_id == NAME))
    await sess.commit()
    # The command opens its own engine; drop it before the next test's event loop.
    from meridian_core.db import dispose_engines

    await dispose_engines()


def _secret(out: str) -> str:
    match = re.search(r"^  (\S{40,})$", out, re.MULTILINE)
    assert match, out
    return match.group(1)


async def test_issue_prints_the_secret_once_and_stores_only_its_hash(capsys, session_for) -> None:
    assert await cli._issue(NAME, "reader", 90) == 0
    out = capsys.readouterr().out
    secret = _secret(out)

    sess = await session_for("ro")
    row = await sess.scalar(select(AgentToken).where(AgentToken.agent_id == NAME))
    assert row.token_hash == hash_token(secret)
    assert secret not in row.token_hash
    assert set(row.allowed_tools) == PROFILE_TOOLS["reader"]
    assert row.expires_at is not None
    assert "only time the token is shown" in out


async def test_the_setup_is_ready_to_paste_for_each_client(capsys) -> None:
    await cli._issue(NAME, "reader", 90)
    out = capsys.readouterr().out
    secret = _secret(out)
    assert f'--header "Authorization: Bearer {secret}"' in out
    assert '"httpUrl"' in out


async def test_a_listing_never_shows_a_secret(capsys) -> None:
    await cli._issue(NAME, "analyst", 30)
    secret = _secret(capsys.readouterr().out)
    await cli._list(include_revoked=False)
    listing = capsys.readouterr().out
    assert NAME in listing and "run_readonly_query" in listing
    assert secret not in listing


async def test_revoke_turns_it_off_and_hides_it_from_the_default_list(capsys, session_for) -> None:
    await cli._issue(NAME, "reader", 30)
    capsys.readouterr()
    sess = await session_for("ro")
    token_id = await sess.scalar(select(AgentToken.token_id).where(AgentToken.agent_id == NAME))

    assert await cli._revoke(token_id) == 0
    await cli._list(include_revoked=False)
    assert NAME not in capsys.readouterr().out
    await cli._list(include_revoked=True)
    assert "revoked" in capsys.readouterr().out
    assert await cli._revoke(10**9) == 1, "an unknown id says so"


def test_a_public_url_gets_the_stronger_warning() -> None:
    assert "from anywhere" in cli.exposure_note("https://meridian.example.org/mcp")
    assert "LAN" in cli.exposure_note("http://192.168.1.5:8080/mcp")


def test_an_unknown_profile_is_refused_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        cli.main(["issue", NAME, "--profile", "admin"])
