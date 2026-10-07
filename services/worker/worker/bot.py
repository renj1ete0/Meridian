"""Telegram, inbound (task `P5-07`, §13.3).

The loop that turns a message into a command; `worker/commands.py` decides what it
means. Long polling, the backlog dropped at start-up, the offset advanced before the
work, and no reply to an unknown chat. See docs/features/operations.md#telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from meridian_core.db import dispose_engines, session
from meridian_core.logging import bind_run_id, configure_logging, get_logger

from .commands import authorised, execute, parse
from .liveness import beat
from .telegram import LONG_POLL_S, Telegram

log = get_logger(__name__)

__all__ = ["handle", "run_bot", "skip_backlog"]

#: How long to wait after a poll that could not be made. Telegram's own long
#: poll paces the happy path; this paces the unhappy one, where returning
#: immediately would mean hammering a host that is already unreachable.
BACKOFF_S = 15.0

#: How long an unconfigured bot waits between heartbeats. It has nothing to do
#: and never will until the stack is restarted with a token, so this is only
#: long enough to keep the healthcheck answerable without spinning.
IDLE_S = 60.0


async def skip_backlog(bot: Telegram) -> int | None:
    """Acknowledge whatever is waiting, without running it.

    `offset=-1` asks for the last update only; its id plus one acknowledges the rest
    unread.
    """
    updates = await bot.poll(offset=-1, timeout_s=0)
    if not updates:
        return None
    dropped = updates[-1]["update_id"] + 1
    log.info("dropped the telegram backlog", extra={"offset": dropped})
    return dropped


async def handle(bot: Telegram, update: dict) -> str | None:
    """Authorise, parse, execute, reply. Returns what was said back, if anything.

    Never raises. A command that fails takes its own reply down with it at
    worst; the loop that called this has already moved past the update.
    """
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")

    if not authorised(chat_id, bot.chat_id):
        # Logged with the id, because the likeliest cause is not an intruder
        # but a `TELEGRAM_CHAT_ID` that is wrong — and the operator cannot fix
        # that without knowing which id did arrive.
        log.warning("telegram message from an unauthorised chat", extra={"chat": str(chat_id)})
        return None

    command = parse(message.get("text") or "")
    try:
        async with session() as sess:
            reply = await execute(sess, command)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any handler
        # The type only. A database error's message can carry a connection
        # string, and this reply goes to a chat.
        log.exception("telegram command failed", extra={"command": command.name})
        reply = f"/{command.name} failed: {type(exc).__name__}. It is in the log."

    await bot.send(reply)
    return reply


async def run_bot(*, poll_timeout_s: int = LONG_POLL_S, max_polls: int | None = None) -> int:
    """Poll until stopped. Returns how many commands were handled.

    `max_polls` exists for tests and for a one-shot check that the token works;
    the deployed form runs without it.
    """
    bot = Telegram.from_env()
    if bot is None:
        # §13.3's outbound half already treats "not configured" as a supported
        # state, and inbound is the same: no token means no control surface,
        # not a failed start. Admin and the MCP tools still work.
        log.warning("no telegram configured; inbound commands are unavailable")
        # Idle rather than exit: compose restarts a clean exit too. It keeps beating,
        # so an idle bot is not a dead one.
        while max_polls is None:
            beat()
            await asyncio.sleep(IDLE_S)
        return 0

    offset = await skip_backlog(bot)
    handled = 0
    polls = 0

    try:
        while max_polls is None or polls < max_polls:
            polls += 1
            beat()
            updates = await bot.poll(offset=offset, timeout_s=poll_timeout_s)

            if updates is None:
                # Could not ask, as distinct from nothing to say. Both are
                # instant; only one deserves a wait.
                await asyncio.sleep(BACKOFF_S)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                if await handle(bot, update) is not None:
                    handled += 1
    finally:
        await bot.aclose()
        await dispose_engines()

    return handled


def main() -> None:
    """Entry point: ``python -m worker.bot``."""
    parser = argparse.ArgumentParser(description="§13.3's inbound Telegram commands.")
    parser.add_argument(
        "--max-polls",
        type=int,
        default=None,
        help="stop after this many polls. For checking the token without leaving it running.",
    )
    args = parser.parse_args()

    configure_logging("bot")
    with bind_run_id(f"bot-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_bot(max_polls=args.max_polls))


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
