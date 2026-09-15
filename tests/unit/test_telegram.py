"""Telegram, outbound (task P5-07, spec §13.3, §11.11).

The channel that reports failures must not fail loudly itself: the digest has
already written its findings to `notifications` by the time this is called, so a
send that cannot happen should lose the delivery and not the evidence.
"""

from __future__ import annotations

import httpx
import pytest

from worker.telegram import MAX_MESSAGE, Telegram


def responder(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_both_settings_are_required(monkeypatch) -> None:
    """A token with nowhere to send and a chat id that cannot send are two
    half-configured states that fail differently. One state is easier to reason
    about than two."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert Telegram.from_env() is None

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    assert Telegram.from_env() is not None


def test_no_configuration_is_not_an_error(monkeypatch) -> None:
    """A deployment that notifies nobody is a choice. The digest still runs,
    still evaluates conditions and still records them."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert Telegram.from_env() is None


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


async def test_a_message_goes_to_the_configured_chat() -> None:
    seen: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return ok(request)

    assert await Telegram("tok", "999", client=responder(capture)).send("hello") is True
    assert seen["chat_id"] == "999"
    assert seen["text"] == "hello"


async def test_no_markdown_parse_mode_is_requested() -> None:
    """A digest interpolates URLs, titles and error strings from crawled pages.
    An unbalanced asterisk in one of them makes Telegram reject the whole
    message — losing an alert to a formatting character in somebody else's
    page title."""
    seen: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return ok(request)

    await Telegram("tok", "1", client=responder(capture)).send("*not* _markdown_")

    assert "parse_mode" not in seen


async def test_an_overlong_message_is_shortened_rather_than_rejected() -> None:
    """Telegram refuses anything over 4096 characters. A digest that grew past
    it would fail entirely rather than arrive shortened, which is the wrong
    failure for the channel that exists to say things are wrong."""
    seen: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return ok(request)

    assert await Telegram("tok", "1", client=responder(capture)).send("x" * 9000) is True
    assert len(seen["text"]) <= MAX_MESSAGE


# --------------------------------------------------------------------------
# Failing quietly
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_a_refused_send_returns_false_rather_than_raising(status: int) -> None:
    """A reporter that crashes on its own failure takes the report with it."""
    client = responder(lambda request: httpx.Response(status, json={"ok": False}))

    assert await Telegram("tok", "1", client=client).send("hi") is False


async def test_an_unreachable_telegram_returns_false() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert await Telegram("tok", "1", client=responder(refuse)).send("hi") is False
