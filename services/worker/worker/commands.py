"""Inbound Telegram commands (task `P5-07`, §13.3).

§13.3 chose Telegram for both directions because inline keyboards make
intervention possible from a phone. The outbound half shipped in `v0.58.0`.
This is inbound, and §13.3 is blunt about what that makes the bot: "it is a
control surface, not just a notifier."

**The task named two conditions and did not build this until they held.** Most
commands needed steering (`P5-06`) or the orchestrator (phase 4). `P5-06` now
runs, so the steering commands are reachable; the orchestrator ones are not,
and they **refuse by name rather than being absent** — a command that silently
does nothing is worse than one that says it cannot yet, especially on a surface
somebody is using from a phone because they are not at a desk.

`worker/bot.py` is the loop that feeds this module; what is here decides only
what a message means and what doing it involves.

Three properties, in the order they matter:

**Authorisation before parsing.** A message from an unknown chat is discarded
without looking at what it said. Parsing first would mean a stranger could
probe the command surface by reading error messages, and §13.3's single-chat
restriction exists precisely because this can change steering and trigger runs.

**Parsing is pure and separate from doing.** `parse` returns a description of
what was asked; `execute` performs it. That makes every refusal and every
argument bound testable without a database or a bot token — which is most of
what can go wrong here.

**Nothing destructive is reachable.** There is no command that deletes, and
§10.2's argument applies: archiving a topic is a status change that keeps every
node, edge and tag. The most damaging thing a compromised chat could do is
misweight the crawl, which is visible in `steering_log` and reversible.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import shlex
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from meridian_core import steering
from meridian_core.logging import get_logger
from meridian_core.queueing import enqueue
from meridian_core.stats import corpus_stats
from meridian_core.validation import ValidationError, check_seed_allowed

log = get_logger(__name__)

#: Commands §13.3 lists that need phase 4, with what they are waiting for.
#: Named rather than omitted: "/run is not a command" and "/run exists and does
#: nothing" are indistinguishable from a phone, and only one is the truth.
NOT_YET = {
    "run": "the orchestrator (phase 4) — nothing can start a synthesis run yet",
    "contested": "edges, which phase 4 writes — nothing has written one",
    "tiers": "artifacts from a synthesis run, which phase 4 produces",
    "reprocess": "artifacts to re-derive (`P7-08`)",
    "enrich": "the enrichment passes (`P7-07`)",
}

__all__ = ["Command", "NOT_YET", "authorised", "execute", "parse"]


@dataclasses.dataclass(frozen=True)
class Command:
    """One parsed command, or a refusal carrying its reason."""

    name: str
    args: tuple[str, ...] = ()
    #: Set when the message could not become a command. The bot replies with
    #: it verbatim: a phone is a bad place to guess what went wrong.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def authorised(chat_id: str | int, allowed: str | int | None) -> bool:
    """Whether this chat may command the system at all (§13.3).

    Checked **before** parsing. A stranger who can see error messages can map
    the command surface; one whose messages are dropped unread cannot.

    An unset `allowed` refuses everything. The alternative — treating "no chat
    configured" as "any chat" — turns a missing environment variable into an
    open control surface, and §11.11 keeps the bot token in the environment
    precisely because that is where a misconfiguration is most likely.
    """
    if allowed is None or str(allowed).strip() == "":
        return False
    return str(chat_id).strip() == str(allowed).strip()


def parse(text: str) -> Command:
    """Turn a message into a command, or into a refusal that says why.

    Pure. Everything that can go wrong with an argument — a missing topic, a
    multiplier that is not a number, a command that needs phase 4 — is decided
    here and is testable without a bot or a database.
    """
    body = (text or "").strip()
    if not body.startswith("/"):
        return Command("", error="Not a command. Commands begin with /.")

    try:
        parts = shlex.split(body)
    except ValueError:
        # An unbalanced quote. `shlex` raises, and a raw traceback is a poor
        # reply to a phone.
        return Command("", error="That has an unclosed quote.")

    name = parts[0].lstrip("/").lower()
    # `/boost@meridianbot` in a group: Telegram appends the bot's name.
    name = name.split("@", 1)[0]
    args = tuple(parts[1:])

    if name in NOT_YET:
        return Command(name, args, error=f"/{name} is not available yet: it needs {NOT_YET[name]}.")

    if name in {"status", "weights"}:
        return Command(name, args)

    if name == "pause":
        if len(args) != 1:
            return Command(name, args, error="Usage: /pause <topic>")
        return Command(name, args)

    if name == "boost":
        if len(args) != 3:
            return Command(name, args, error="Usage: /boost <topic> <multiplier> <weeks>")
        try:
            factor, weeks = float(args[1]), int(args[2])
        except ValueError:
            return Command(name, args, error="The multiplier and the weeks must be numbers.")
        if factor <= 0 or weeks <= 0:
            return Command(name, args, error="The multiplier and the weeks must be positive.")
        return Command(name, args)

    if name == "seed":
        if not args:
            return Command(name, args, error="Usage: /seed <url or query> [topic]")
        return Command(name, args)

    return Command(name, args, error=f"Unknown command /{name}.")


async def execute(sess: AsyncSession, command: Command, *, actor: str = "telegram") -> str:
    """Do what was asked, and return what to say back.

    Every steering change is recorded with `actor="telegram"`. §10.1 wants a
    reason on every change, and "who" matters as much as "what": a weight that
    moved from a phone at midnight is a different thing to review than one
    changed in Admin.
    """
    if not command.ok:
        return command.error or "That did not work."

    handler = _HANDLERS.get(command.name)
    if handler is None:  # pragma: no cover - `parse` has already refused these
        return f"Unknown command /{command.name}."

    log.info("telegram command", extra={"command": command.name})
    return await handler(sess, command, actor)


async def _status(sess: AsyncSession, _command: Command, _actor: str) -> str:
    stats = await corpus_stats(sess)
    return (
        f"{stats.sources:,} sources · {stats.chunks:,} chunks · "
        f"{stats.embedded_chunks:,} embedded · {stats.entities:,} entities · "
        f"{stats.edges:,} edges"
    )


async def _weights(sess: AsyncSession, _command: Command, _actor: str) -> str:
    rows = await steering.topics(sess)
    if not rows:
        return "No topics configured."
    shares = steering.draw_shares(rows, now=_now())
    lines = [f"{row.topic}: {shares.get(row.topic, 0.0) * 100:.1f}% ({row.status})" for row in rows]
    return "\n".join(lines)


async def _pause(sess: AsyncSession, command: Command, actor: str) -> str:
    topic = command.args[0]
    try:
        await steering.set_status(
            sess, topic, "paused", actor=actor, reason="paused from Telegram", now=_now()
        )
    except (ValueError, LookupError) as exc:
        return f"Could not pause {topic}: {exc}"
    await sess.commit()
    return f"{topic} is paused. Its weight is kept, and nothing it produced is touched."


async def _boost(sess: AsyncSession, command: Command, actor: str) -> str:
    topic, factor, weeks = command.args[0], float(command.args[1]), int(command.args[2])
    now = _now()
    try:
        # §10's mechanism is decay: the expiry is what makes a boost temporary,
        # and `set_boost` refuses a factor without one for that reason. The
        # stored weight is untouched, so nothing needs restoring afterwards.
        await steering.set_boost(
            sess,
            topic,
            factor=factor,
            expires_at=now + _dt.timedelta(weeks=weeks),
            actor=actor,
            reason=f"boosted {factor}x for {weeks}w from Telegram",
            now=now,
        )
    except (ValueError, LookupError) as exc:
        return f"Could not boost {topic}: {exc}"
    await sess.commit()
    return f"{topic} boosted {factor}x for {weeks} week(s). It decays on its own."


async def _seed(sess: AsyncSession, command: Command, _actor: str) -> str:
    target = command.args[0]
    topic = command.args[1] if len(command.args) > 1 else None

    task_type = "url" if "://" in target else "query"
    if task_type == "url":
        try:
            await check_seed_allowed(sess, target)
        except ValidationError as exc:
            # The same checks a model's seed gets. A private address is no
            # safer for having been typed into a phone.
            return f"Refused: {exc}"

    await enqueue(sess, target, topic=topic, seed_source="user", task_type=task_type, priority=100)
    await sess.commit()
    return f"Queued {task_type}: {target}" + (f" (topic: {topic})" if topic else "")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


Handler = Callable[[AsyncSession, Command, str], Awaitable[str]]

_HANDLERS: dict[str, Handler] = {
    "status": _status,
    "weights": _weights,
    "pause": _pause,
    "boost": _boost,
    "seed": _seed,
}
