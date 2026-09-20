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
    CHARS_PER_TOKEN,
    DEFAULT_MAX_TOKENS,
    Completion,
    NotConfigured,
    _api_key,
    _estimate,
    _SHAPES,
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
