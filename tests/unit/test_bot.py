"""The inbound Telegram loop (task `P5-07`, §13.3).

Four claims, each of which has a failure mode that only shows up in production:
a restart that replays yesterday's commands, a message that wedges the loop
forever, an unreachable network that turns into a hot loop, and a stranger who
learns the bot is listening by getting an answer.

No database and no bot. `execute` is exercised for real in
`tests/integration/test_commands.py`; what is tested here is the loop around
it, and the loop's job is to be right when things are wrong.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest

from worker.bot import handle, run_bot, skip_backlog
from worker.telegram import Telegram


def bot_with(handler) -> Telegram:
    return Telegram(
        "token", "12345", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def message(update_id: int, text: str, *, chat: int | str = 12345) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}


class Responder:
    """A fake Telegram: hands out queued update batches, records what was sent."""

    def __init__(self, batches: list) -> None:
        self.batches = list(batches)
        self.sent: list[str] = []
        self.offsets: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            self.offsets.append(request.url.params.get("offset"))
            batch = self.batches.pop(0) if self.batches else []
            if isinstance(batch, httpx.Response):
                return batch
            return httpx.Response(200, json={"ok": True, "result": batch})
        self.sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def no_waiting(monkeypatch) -> None:
    """The backoff is real and 15 seconds long. Its existence is what is tested."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("worker.bot.asyncio.sleep", fake_sleep)
    return slept


@pytest.fixture
def no_database(monkeypatch) -> list:
    """`execute` without a session. What it does is tested against a real one."""
    executed: list = []

    @contextlib.asynccontextmanager
    async def fake_session():
        yield object()

    async def fake_execute(_sess, command, **_kwargs) -> str:
        executed.append(command)
        return command.error or f"did /{command.name}"

    monkeypatch.setattr("worker.bot.session", fake_session)
    monkeypatch.setattr("worker.bot.execute", fake_execute)
    return executed


# --------------------------------------------------------------------------
# Who is answered
# --------------------------------------------------------------------------


async def test_an_unauthorised_chat_gets_no_reply_at_all(no_database) -> None:
    """Silence rather than a refusal.

    A refusal confirms the bot exists, is listening and has commands worth
    guessing at. §13.3 restricts the bot to one chat precisely because it can
    change steering, and the restriction should not advertise itself.
    """
    responder = Responder([])
    bot = bot_with(responder)

    reply = await handle(bot, message(1, "/status", chat=99999))

    assert reply is None
    assert responder.sent == []
    assert no_database == [], "the message must not even be parsed"


async def test_the_configured_chat_is_answered(no_database) -> None:
    responder = Responder([])
    bot = bot_with(responder)

    reply = await handle(bot, message(1, "/status"))

    assert reply == "did /status"
    assert responder.sent == ["did /status"]


async def test_a_message_with_no_text_is_not_a_crash(no_database) -> None:
    # A photo, a sticker, a pinned-message event: Telegram delivers all of them
    # as `message` objects with no `text`.
    responder = Responder([])
    bot = bot_with(responder)

    reply = await handle(bot, {"update_id": 1, "message": {"chat": {"id": 12345}}})

    assert reply is not None
    assert "command" in reply.lower()


async def test_a_handler_that_raises_is_reported_without_leaking_it(monkeypatch) -> None:
    """The reply names the type, never the message.

    A database error's text can carry a connection string, and this reply goes
    to a chat that may be read on a phone in public.
    """

    @contextlib.asynccontextmanager
    async def exploding_session():
        raise RuntimeError("postgresql://user:hunter2@db/meridian is unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr("worker.bot.session", exploding_session)
    responder = Responder([])
    bot = bot_with(responder)

    reply = await handle(bot, message(1, "/status"))

    assert "RuntimeError" in reply
    assert "hunter2" not in reply
    assert responder.sent == [reply]


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


async def test_the_backlog_is_acknowledged_without_being_run() -> None:
    """A bot restarted after a night down must not execute yesterday's commands.

    `/run` typed at midnight is not a request to start a run at breakfast, and
    a `/boost` applied twice is a boost nobody asked for.
    """
    responder = Responder([[message(41, "/run"), message(42, "/boost t 2 4")]])
    bot = bot_with(responder)

    offset = await skip_backlog(bot)

    assert offset == 43, "everything up to the last update is acknowledged"
    assert responder.sent == [], "and none of it is answered"
    assert responder.offsets == ["-1"], "only the last update is even fetched"


async def test_an_empty_backlog_leaves_the_offset_unset() -> None:
    bot = bot_with(Responder([[]]))

    assert await skip_backlog(bot) is None


async def test_the_offset_advances_past_a_message_that_fails(
    monkeypatch, no_waiting, configured
) -> None:
    """A poison message must not be redelivered forever.

    Telegram replays anything unacknowledged. A loop that advanced its offset
    only after a handler succeeded would spend the rest of its life on the one
    update it cannot process, and no later command would ever be seen — so the
    offset moves for a command that raised and for one that was never answered
    alike.
    """

    @contextlib.asynccontextmanager
    async def exploding_session():
        raise RuntimeError("the database is gone")
        yield  # pragma: no cover

    monkeypatch.setattr("worker.bot.session", exploding_session)
    responder = Responder(
        [
            [],
            # One that fails inside the handler, one from a chat that is never
            # answered at all. Neither may be seen twice.
            [message(7, "/status"), message(8, "/status", chat=99999)],
            [],
        ]
    )
    monkeypatch.setattr("worker.bot.Telegram.from_env", staticmethod(lambda: bot_with(responder)))

    handled = await run_bot(poll_timeout_s=0, max_polls=3)

    assert responder.offsets[-1] == "9", "both updates are acknowledged, whatever they did"
    assert handled == 1, "the unauthorised one is not counted as handled"


async def test_a_failed_poll_is_waited_out_rather_than_retried_instantly(
    monkeypatch, no_waiting, no_database, configured
) -> None:
    """`None` and `[]` arrive at the same speed and mean different things.

    A loop that conflated them would hammer an unreachable host as fast as the
    connection failed, which is the opposite of what a network fault needs.
    """
    responder = Responder([[], httpx.Response(500), httpx.Response(500)])
    monkeypatch.setattr("worker.bot.Telegram.from_env", staticmethod(lambda: bot_with(responder)))

    await run_bot(poll_timeout_s=0, max_polls=3)

    assert no_waiting == [pytest.approx(15.0), pytest.approx(15.0)]


async def test_a_quiet_poll_is_not_waited_out(
    monkeypatch, no_waiting, no_database, configured
) -> None:
    # The long poll is the pacing on the happy path; sleeping after it too
    # would double every interval.
    responder = Responder([[], [], []])
    monkeypatch.setattr("worker.bot.Telegram.from_env", staticmethod(lambda: bot_with(responder)))

    await run_bot(poll_timeout_s=0, max_polls=3)

    assert no_waiting == []


async def test_commands_are_counted_and_acknowledged_in_order(
    monkeypatch, no_waiting, no_database, configured
) -> None:
    responder = Responder([[], [message(3, "/status"), message(4, "/weights")], []])
    monkeypatch.setattr("worker.bot.Telegram.from_env", staticmethod(lambda: bot_with(responder)))

    handled = await run_bot(poll_timeout_s=0, max_polls=3)

    assert handled == 2
    # -1 discards the backlog, then no offset is known, then the last id + 1.
    assert responder.offsets[-1] == "5"
    assert [c.name for c in no_database] == ["status", "weights"]


async def test_an_unconfigured_bot_starts_and_does_nothing(monkeypatch) -> None:
    """No token is a deployment that has no control surface, not a failed start.

    Admin and the MCP tools are unaffected, and the outbound half already
    treats absence the same way.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert await run_bot(max_polls=1) == 0


async def test_an_unconfigured_bot_idles_rather_than_exiting(monkeypatch) -> None:
    """`restart: unless-stopped` restarts a container that exits cleanly too.

    Returning on a missing token would turn a supported state into a container
    restarting every second for as long as the stack is up. It keeps beating so
    the healthcheck can still tell idle from dead.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    beats: list[int] = []

    async def stop_after_two(_seconds: float) -> None:
        beats.append(1)
        if len(beats) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("worker.bot.asyncio.sleep", stop_after_two)
    monkeypatch.setattr("worker.bot.beat", lambda: None)

    with pytest.raises(KeyboardInterrupt):
        await run_bot()

    assert len(beats) == 2, "it waited rather than returning"


@pytest.fixture
def configured(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
