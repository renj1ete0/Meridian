"""Inbound Telegram commands, without a bot or a database (task `P5-07`, §13.3).

Everything that decides whether a message is allowed to do anything is here:
the chat check, the command table, and every argument bound. §13.3 makes the
bot "a control surface, not just a notifier", so the interesting cases are the
refusals — a stranger's message, a command that needs a phase that does not
exist, a multiplier that is not a number.

The two drift tests are the point of the file. One walks the command block in
the spec and fails when it gains a command nothing here knows about; the other
fails when a command becomes reachable in `parse` with no handler behind it,
which is the shape a half-finished command takes.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from worker.commands import NOT_YET, Command, authorised, parse

SPEC = pathlib.Path(__file__).resolve().parents[2] / "docs/spec/autonomous-research-system-spec.md"


# --------------------------------------------------------------------------
# Who may command at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize("allowed", [None, "", "   "])
def test_an_unconfigured_chat_id_refuses_everyone(allowed) -> None:
    # The failure this exists to stop: a missing environment variable read as
    # "no restriction". §11.11 keeps the token in the environment, which is
    # exactly where a value goes missing without anything noticing.
    assert authorised("12345", allowed) is False
    assert authorised("", allowed) is False


def test_a_different_chat_is_refused() -> None:
    assert authorised("99999", "12345") is False


def test_the_configured_chat_is_allowed_however_it_is_typed() -> None:
    # Telegram hands the id back as an int; the environment holds a string, and
    # a `.env` file keeps whatever whitespace was typed after the `=`.
    assert authorised(12345, "12345") is True
    assert authorised("12345", 12345) is True
    assert authorised(" 12345 ", "12345\n") is True


def test_a_chat_id_is_not_matched_loosely() -> None:
    # Prefix or substring matching would let a neighbouring id through.
    assert authorised("123456", "12345") is False
    assert authorised("2345", "12345") is False


def test_authorisation_cannot_see_the_message() -> None:
    # Structural, and the reason the two are separate functions: a check that
    # took the text could grow a branch that parses first, and a refusal that
    # explains itself is how a stranger maps the surface.
    import inspect

    assert list(inspect.signature(authorised).parameters) == ["chat_id", "allowed"]


# --------------------------------------------------------------------------
# What is a command
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "status", "hello there", "boost x 2 4"])
def test_text_that_is_not_a_command_is_refused_without_guessing(text) -> None:
    command = parse(text)

    assert not command.ok
    assert command.name == ""


def test_an_unknown_command_says_so() -> None:
    command = parse("/frobnicate")

    assert not command.ok
    assert "/frobnicate" in (command.error or "")


def test_a_group_mention_suffix_is_stripped() -> None:
    # In a group Telegram delivers `/status@meridianbot`. Without this the bot
    # is silent in exactly the chat somebody added it to.
    assert parse("/status@meridianbot").ok
    assert parse("/status@meridianbot").name == "status"


def test_a_command_is_case_insensitive() -> None:
    # A phone keyboard capitalises the first letter after a newline.
    assert parse("/Status").name == "status"


def test_an_unclosed_quote_is_a_reply_rather_than_a_traceback() -> None:
    command = parse('/seed "half a query')

    assert not command.ok
    assert "quote" in (command.error or "")


def test_a_quoted_argument_stays_one_argument() -> None:
    command = parse('/seed "two words" atopic')

    assert command.ok
    assert command.args == ("two words", "atopic")


# --------------------------------------------------------------------------
# Commands that need a phase that does not exist yet
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(NOT_YET))
def test_a_command_waiting_on_a_later_phase_refuses_by_name(name) -> None:
    # "/run is not a command" and "/run exists and does nothing" are
    # indistinguishable from a phone, and only one of them is true.
    command = parse(f"/{name}")

    assert not command.ok
    assert f"/{name}" in (command.error or "")
    assert "Unknown" not in (command.error or "")
    assert NOT_YET[name] in (command.error or "")


@pytest.mark.parametrize("name", sorted(NOT_YET))
def test_a_command_waiting_on_a_later_phase_is_refused_with_its_arguments_too(name) -> None:
    # `/contested [topic]` and `/reprocess [sample|all]` take arguments, and a
    # refusal that only fired for the bare form would half-work.
    assert not parse(f"/{name} something").ok


def test_the_spec_command_list_is_fully_accounted_for() -> None:
    """Drift: every command §13.3 lists is handled, refused by name, or fails here.

    A command added to the spec and nowhere else is the failure this catches —
    it is otherwise invisible until somebody types it into a phone.
    """
    block = re.search(r"\*\*Inbound commands:\*\*\s*```(.*?)```", SPEC.read_text(), re.S)
    assert block, "the spec no longer has an inbound command block to check against"

    listed = {
        line.strip().lstrip("/").split()[0]
        for line in block.group(1).splitlines()
        if line.strip().startswith("/")
    }
    assert len(listed) >= 10, "the spec block parsed to too few commands to be right"

    import worker.commands as module

    for name in sorted(listed):
        # Not `parse(...).ok`: a bare `/boost` correctly fails on arity, and a
        # drift test that could not tell that apart from "never heard of it"
        # would have to be loosened until it caught nothing.
        known = name in module._HANDLERS or name in NOT_YET
        assert known, f"/{name} is in the spec but nothing here knows it"


def test_no_command_can_delete() -> None:
    """§10.2: archiving keeps every node, edge and tag. Nothing here removes.

    Source-level because it is a claim about the whole surface rather than one
    handler: the worst a compromised chat can do is misweight the crawl, and
    that is recorded in `steering_log` and reversible.
    """
    import worker.commands as module

    source = pathlib.Path(module.__file__).read_text()

    assert "sess.delete" not in source
    assert "delete(" not in source
    assert not {"delete", "drop", "purge", "forget"} & set(module._HANDLERS)


# --------------------------------------------------------------------------
# Argument bounds
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["/pause", "/pause one two", "/boost t 2", "/boost t 2 4 5", "/seed"],
)
def test_wrong_arity_is_refused_with_a_usage_line(text) -> None:
    command = parse(text)

    assert not command.ok
    assert "Usage" in (command.error or "")


@pytest.mark.parametrize("text", ["/boost t two 4", "/boost t 2 four", "/boost t 2 4.5"])
def test_a_boost_that_is_not_numbers_is_refused(text) -> None:
    # `4.5` too: `int("4.5")` raises, and a boost for half a week is not a
    # thing the caller meant.
    assert not parse(text).ok


@pytest.mark.parametrize("text", ["/boost t 0 4", "/boost t -2 4", "/boost t 2 0", "/boost t 2 -4"])
def test_a_boost_that_would_do_nothing_or_go_backwards_is_refused(text) -> None:
    # A zero-week boost expires before it is stored, and `set_boost` refuses it
    # anyway; catching it here means the reply names the argument.
    command = parse(text)

    assert not command.ok
    assert "positive" in (command.error or "")


def test_a_valid_boost_keeps_its_arguments_as_typed() -> None:
    command = parse("/boost atopic 2.5 3")

    assert command.ok
    assert command.args == ("atopic", "2.5", "3")


def test_a_seed_may_omit_its_topic() -> None:
    assert parse("/seed https://example.test/a").ok
    assert parse("/seed https://example.test/a atopic").args[1] == "atopic"


# --------------------------------------------------------------------------
# Parse and execute agree
# --------------------------------------------------------------------------


def test_every_parseable_command_has_a_handler() -> None:
    """Drift: a command `parse` accepts with nothing behind it would be silent.

    This is the shape a half-added command takes — the branch lands in `parse`
    and the handler is forgotten, and the bot then replies "Unknown command"
    for a command it just accepted.
    """
    import worker.commands as module

    for name in module._HANDLERS:
        # Reached `parse` at all: an unknown command is the only refusal that
        # loses the name it was given.
        assert "Unknown" not in (parse(f"/{name}").error or "")

    for name in ("status", "weights", "pause", "boost", "seed"):
        assert name in module._HANDLERS, f"/{name} parses but nothing executes it"


def test_a_handler_is_never_reached_for_a_command_waiting_on_a_phase() -> None:
    import worker.commands as module

    assert not set(NOT_YET) & set(module._HANDLERS)


def test_a_refusal_carries_no_silent_success() -> None:
    # `ok` is derived from `error`, so the two cannot disagree.
    assert Command("status").ok
    assert not Command("status", error="no").ok
