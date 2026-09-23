"""Calling an agent, without calling one (task `P4-15`, §11.3, §11.9, §11.11).

Nothing here reaches a provider. What is tested is everything around the call,
which is where the failures that matter live: a key read from the wrong place,
a reservation that is never released, a chain that stops at the first failure,
a placeholder model string reaching a vendor.

The estimate and the settlement are the pair worth reading together. A cap
checked after the call is not a cap, so the worst case is reserved first — and
without the settlement that follows, a run would exhaust its allowance on
answers it never gave.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from meridian_core.models import Agent
from meridian_core.provider import (
    _SHAPES,
    CHARS_PER_TOKEN,
    DEFAULT_MAX_TOKENS,
    RELAY_DIR_ENV,
    Completion,
    NotConfigured,
    ProviderError,
    _api_key,
    _call_relay,
    _estimate,
    relay_key,
)
from meridian_core.routing import TASK_TYPES

REGISTRY = pathlib.Path(__file__).resolve().parents[2] / "config/agents.yaml"


def agent(**kwargs) -> Agent:
    defaults = {
        "agent_id": "a",
        "provider": "anthropic",
        "model": "claude-opus-5",
        "enabled": True,
        "quality_tier": 4,
    }
    return Agent(**{**defaults, **kwargs})


# --------------------------------------------------------------------------
# The key is a name, never a value (§11.11)
# --------------------------------------------------------------------------


def test_the_key_is_read_from_the_variable_the_row_names(monkeypatch) -> None:
    monkeypatch.setenv("ZZ_TEST_KEY", "sk-secret")

    assert _api_key(agent(api_key_env_var="ZZ_TEST_KEY")) == "sk-secret"


def test_a_row_naming_no_variable_needs_no_key() -> None:
    """How a local endpoint is configured — distinct from naming a variable
    that is unset, which is a deployment that has not finished."""
    assert _api_key(agent(api_key_env_var=None)) is None
    assert _api_key(agent(api_key_env_var="   ")) is None


def test_a_named_variable_that_is_unset_refuses_and_says_which(monkeypatch) -> None:
    monkeypatch.delenv("ZZ_MISSING_KEY", raising=False)

    with pytest.raises(NotConfigured, match="ZZ_MISSING_KEY"):
        _api_key(agent(api_key_env_var="ZZ_MISSING_KEY"))


def test_an_empty_variable_is_treated_as_unset(monkeypatch) -> None:
    # A `.env` line with nothing after the `=` is the commonest way a key goes
    # missing, and it reads as set to `os.environ`.
    monkeypatch.setenv("ZZ_BLANK_KEY", "   ")

    with pytest.raises(NotConfigured):
        _api_key(agent(api_key_env_var="ZZ_BLANK_KEY"))


def test_no_key_is_ever_logged_or_carried_on_the_result() -> None:
    # A `Completion` travels into log lines and run summaries. It carries what
    # a derived write has to record and nothing else.
    fields = set(Completion.__dataclass_fields__)

    assert fields == {"text", "agent_id", "model", "input_tokens", "output_tokens"}


# --------------------------------------------------------------------------
# The reservation
# --------------------------------------------------------------------------


def test_the_estimate_covers_the_prompt_and_the_whole_answer() -> None:
    """The worst case, because the real figure is only knowable afterwards.

    An under-estimate is the dangerous direction: it lets a call start that the
    cap should have refused, and the cap stops meaning anything.
    """
    prompt = "x" * 3000

    estimate = _estimate(prompt, None, 1000)

    assert estimate >= len(prompt) // CHARS_PER_TOKEN + 1000


def test_the_estimate_counts_the_system_prompt_too() -> None:
    # A framed batch (`P4-06`) puts most of its bulk in the system half; an
    # estimate that ignored it would under-reserve by most of the call.
    assert _estimate("x" * 300, "y" * 3000, 10) > _estimate("x" * 300, None, 10)


def test_the_estimate_is_never_zero() -> None:
    # A zero reservation is refused by `reserve_tokens`, which would turn an
    # empty prompt into a budget error rather than an empty answer.
    assert _estimate("", None, 1) >= 1


def test_the_default_answer_size_is_generous() -> None:
    # An answer truncated at the limit costs everything it spent and returns
    # something unusable; the reservation is settled back down moments later.
    assert DEFAULT_MAX_TOKENS >= 8000


# --------------------------------------------------------------------------
# Drift against the registry
# --------------------------------------------------------------------------


def test_every_provider_in_the_registry_can_be_called() -> None:
    """A row naming a provider nothing implements is an agent that is chosen
    and then skipped — visible only as a run that found nobody to ask."""
    rows = yaml.safe_load(REGISTRY.read_text())["agents"]
    named = {row["provider"] for row in rows}

    assert named <= set(_SHAPES), f"no call shape for: {sorted(named - set(_SHAPES))}"


def test_the_hosted_rows_name_a_model_rather_than_a_placeholder() -> None:
    """A placeholder reaches the vendor and comes back as a bad request hours
    into a run. `_SHAPES` never sees it — the chain skips the row — but the
    registry should not be shipping one either."""
    rows = yaml.safe_load(REGISTRY.read_text())["agents"]

    for row in rows:
        if row["provider"] == "anthropic":
            assert not str(row["model"]).startswith("<"), row["agent_id"]


def test_every_hosted_row_names_the_variable_holding_its_key() -> None:
    """§11.11. Without it the SDK falls back to its own default variable, so
    the deployment works by coincidence wherever that happens to be set."""
    rows = yaml.safe_load(REGISTRY.read_text())["agents"]

    for row in rows:
        if row["provider"] == "anthropic":
            assert row.get("api_key_env_var"), row["agent_id"]


def test_the_seed_carries_the_key_variable_into_the_database() -> None:
    # A column the YAML sets and the seed drops is a value that exists in the
    # file and nowhere that matters.
    source = (REGISTRY.parent.parent / "scripts/seed.py").read_text()

    assert "api_key_env_var=row.get(" in source


def test_a_task_type_maps_to_a_real_task() -> None:
    # `complete` is called with a task type; an unknown one raises from
    # `resolve_chain` rather than reaching a provider.
    assert "relation_extraction" in TASK_TYPES


# --------------------------------------------------------------------------
# The relay: an attended model, through files (`P4-18`)
# --------------------------------------------------------------------------


def relay_agent() -> Agent:
    return agent(agent_id="session", provider="relay", model="claude-opus-5-5")


async def ask(prompt: str = "extract from these passages", system: str | None = "be precise"):
    return await _call_relay(
        relay_agent(), prompt=prompt, system=system, max_tokens=100, timeout_s=1.0
    )


async def test_an_unanswered_prompt_is_left_for_somebody_and_the_call_fails(
    monkeypatch, tmp_path
) -> None:
    """Fails as an unreachable provider does, so the run defers (§13.4) rather
    than proceeding as if an empty answer had come back."""
    monkeypatch.setenv(RELAY_DIR_ENV, str(tmp_path))

    with pytest.raises(ProviderError, match="waiting for"):
        await ask()

    (left,) = tmp_path.glob("*.prompt.json")
    body = __import__("json").loads(left.read_text())
    assert body["prompt"] == "extract from these passages"
    assert body["system"] == "be precise"
    assert body["model"] == "claude-opus-5-5"
    assert left.name == f"{relay_key('extract from these passages', 'be precise')}.prompt.json"


async def test_asking_again_does_not_leave_a_second_copy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(RELAY_DIR_ENV, str(tmp_path))
    for _ in range(3):
        with pytest.raises(ProviderError):
            await ask()
    assert len(list(tmp_path.glob("*.prompt.json"))) == 1


async def test_the_answer_to_the_same_question_is_found_on_the_next_ask(
    monkeypatch, tmp_path
) -> None:
    """How a deferred batch resumes: the mark has not moved, `pull` chooses the
    same passages, the prompt is the same bytes, so the key is the same."""
    monkeypatch.setenv(RELAY_DIR_ENV, str(tmp_path))
    with pytest.raises(ProviderError):
        await ask()
    key = relay_key("extract from these passages", "be precise")
    (tmp_path / f"{key}.answer.txt").write_text('{"edges": []}', encoding="utf-8")

    text, used_in, used_out = await ask()

    assert text == '{"edges": []}'
    # Charged, not free: a relay run must show on §11.9's comparison.
    assert used_in > 0 and used_out > 0


async def test_an_answer_to_a_different_question_is_not_used(monkeypatch, tmp_path) -> None:
    """The failure this design has to rule out: an answer about one batch
    applied to another, citing passages by numbers that now mean different
    text."""
    monkeypatch.setenv(RELAY_DIR_ENV, str(tmp_path))
    other = relay_key("a different batch", "be precise")
    (tmp_path / f"{other}.answer.txt").write_text("stale", encoding="utf-8")

    with pytest.raises(ProviderError):
        await ask()


async def test_an_empty_answer_file_is_still_waiting(monkeypatch, tmp_path) -> None:
    """A file created and not yet written — an editor's first save, a copy in
    progress — must not be read as the model answering nothing."""
    monkeypatch.setenv(RELAY_DIR_ENV, str(tmp_path))
    key = relay_key("extract from these passages", "be precise")
    (tmp_path / f"{key}.answer.txt").write_text("  \n", encoding="utf-8")

    with pytest.raises(ProviderError, match="waiting for"):
        await ask()


def test_the_key_covers_the_system_prompt_as_well() -> None:
    """Two questions that differ only in their instructions are two questions."""
    assert relay_key("same", "extract") != relay_key("same", "tag")
    assert relay_key("same", None) == relay_key("same", "")


async def test_a_relay_with_nowhere_to_leave_prompts_is_a_configuration_error(
    monkeypatch,
) -> None:
    monkeypatch.delenv(RELAY_DIR_ENV, raising=False)
    with pytest.raises(NotConfigured, match=RELAY_DIR_ENV):
        await ask()


def test_the_relay_row_ships_disabled() -> None:
    """Enabled with nobody answering, it defers every run. Turning it on is a
    decision somebody makes while they are watching the directory."""
    rows = yaml.safe_load(REGISTRY.read_text())["agents"]
    (relay,) = [row for row in rows if row["provider"] == "relay"]
    assert relay["enabled"] is False
    assert not relay.get("api_key_env_var")


def test_the_same_passages_under_a_fresh_fence_are_the_same_question() -> None:
    """Found by the end-to-end test: every framing draws a new random
    delimiter, so a digest of the raw prompt never matched the resumed batch
    and every relay run deferred for ever."""
    from meridian_core.framing import new_delimiter

    first, second = new_delimiter(), new_delimiter()
    assert first != second
    template = "begins after {d}\n{d}\n[1] a passage\n/{d}"

    assert relay_key(template.format(d=first), None) == relay_key(template.format(d=second), None)
    assert relay_key(template.format(d=first), None) != relay_key(
        template.format(d=first).replace("a passage", "another passage"), None
    ), "only the fence is ignored; the passages still count"
