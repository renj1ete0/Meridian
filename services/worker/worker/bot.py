"""Telegram, inbound (task `P5-07`, §13.3).

The loop that turns a message into a command. `worker/commands.py` decides what
a message means; this decides when to ask, what to do with the answer, and how
to fail — and all three are about the same thing, which is that this process is
a door into a system that can steer a crawl.

**Long polling, not a webhook.** A webhook needs an inbound port, a certificate
and a public hostname. This needs none of them, so the control surface works
from a machine with no ingress at all — the same deployment `cloudflared`
exists to avoid poking holes in.

**The backlog is dropped at startup.** Telegram holds undelivered updates for
24 hours and replays them on the next poll, so a bot restarted after a night
down would execute every command sent while it was gone. A command is an
instruction about *now*: `/run` typed at midnight is not a request to start a
run at breakfast, and a `/boost` applied twice is a boost nobody asked for. The
first poll therefore acknowledges the queue without reading it.

**The offset advances before the work, not after.** An update that makes a
handler raise is one Telegram will redeliver forever if it is never
acknowledged — the loop would spend the rest of its life on the one message it
cannot process, and no later command would ever be seen.

**An unknown chat gets no reply at all.** Not "unauthorised": silence. §13.3
restricts the bot to one chat because it can change steering, and a refusal
that answers is a refusal that confirms the bot exists and is listening.
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

    `offset=-1` asks Telegram for the *last* update only. Its id plus one
    acknowledges everything before it, which is the cheapest way to discard a
    queue that may hold a hundred messages — and the id is all that is needed,
    so nothing that was sent while the bot was down is even read.
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
        # **Idle rather than exit.** `restart: unless-stopped` restarts a
        # container that exits *cleanly* too, so returning here would turn "no
        # bot configured" into a container restarting every second for as long
        # as the stack is up — which is how a supported state becomes a page of
        # logs. It keeps beating, so the healthcheck can still tell an idle bot
        # from a dead one.
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
