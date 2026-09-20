"""The model call, against a real registry and a real budget (task `P4-15`).

No provider is reached — the call shapes are replaced — because what needs a
database is the bookkeeping around them: the reservation taken before the call,
the settlement that follows it, and the chain walked when an agent will not
answer.

The token tests are the ones to read. A cap checked after the call is not a
cap, so the worst case is reserved first; without the settlement that follows,
a run would exhaust its allowance on answers it never gave, and every later
call in the run would be refused for tokens nobody spent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select

from meridian_core import provider as provider_module
from meridian_core.budget import BudgetError
from meridian_core.models import Agent, Run
from meridian_core.provider import ProviderError, complete
from meridian_core.routing import NoAgentAvailable
from meridian_core.runs import begin_or_resume, unfinished

pytestmark = pytest.mark.usefixtures("require_db")

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)
FIELDS = ("enabled", "model", "provider", "api_key_env_var", "task_types", "fallback_agent_id")


@pytest.fixture
async def registry(session_for) -> AsyncIterator:
    """An enabled registry and a fresh run, both put back afterwards."""
    sess = await session_for("rw")
    await sess.rollback()

    before_agents = {
        row.agent_id: {f: getattr(row, f) for f in FIELDS}
        for row in await sess.scalars(select(Agent))
    }
    before_runs = {row.run_id for row in await sess.scalars(select(Run))}

    stranded = await unfinished(sess)
    held = None
    if stranded is not None:
        held = (stranded.run_id, stranded.status, stranded.stage, stranded.heartbeat_at)
        stranded.status = "failed"
    await sess.flush()

    for row in await sess.scalars(select(Agent)):
        row.enabled = True
        if (row.model or "").startswith("<"):
            # The local row ships without a model string, because which model a
            # local server exposes is the operator's business. A chain test
            # needs it callable; the fixture puts the placeholder back.
            row.model = "zz-local-test-model"
    run, _ = await begin_or_resume(sess, now=NOW)
    await sess.flush()

    yield sess, run

    await sess.rollback()
    await sess.execute(delete(Run).where(Run.run_id.notin_(before_runs or {-1})))
    for row in await sess.scalars(select(Agent)):
        for field, value in before_agents.get(row.agent_id, {}).items():
            setattr(row, field, value)
    if held is not None:
        run_id, status, stage, heartbeat = held
        row = await sess.get(Run, run_id)
        row.status, row.stage, row.heartbeat_at, row.error = status, stage, heartbeat, None
    await sess.commit()


@pytest.fixture
def answers(monkeypatch):
    """Replace the call shapes. Returns a log of which agents were asked."""
    asked: list[str] = []

    def install(**behaviour):
        async def shape(agent, *, prompt, system, max_tokens, timeout_s):
            asked.append(agent.agent_id)
            outcome = behaviour.get(agent.agent_id, behaviour.get("*"))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome or ("an answer", 100, 50)

        monkeypatch.setattr(
            provider_module, "_SHAPES", {"anthropic": shape, "openai_compatible": shape}
        )
        return asked

    return install


# --------------------------------------------------------------------------
# A call that works
# --------------------------------------------------------------------------


async def test_a_completion_carries_what_a_derived_write_must_record(registry, answers) -> None:
    sess, run = registry
    answers()

    result = await complete(
        sess, run, "drafting", prompt="write something", token_cap=100_000, now=NOW
    )

    # §2.3 and §11.12: every derived write names the agent and the exact model.
    assert result.agent_id == "hosted-frontier"
    assert result.model == "claude-opus-5"
    assert result.text == "an answer"
    assert result.total_tokens == 150


async def test_the_run_records_which_agent_answered(registry, answers) -> None:
    # `runs.agent_id` is how a bad batch of edges is later traced to a model.
    sess, run = registry
    answers()

    await complete(sess, run, "drafting", prompt="x", token_cap=100_000, now=NOW)

    assert run.agent_id == "hosted-frontier"


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


async def test_the_reservation_is_settled_down_to_what_was_actually_spent(
    registry, answers
) -> None:
    """Without this a run exhausts its allowance on answers it never gave.

    The reservation has to be the worst case — prompt plus `max_tokens` — and
    almost every answer comes in far under it.
    """
    sess, run = registry
    answers()

    await complete(
        sess, run, "drafting", prompt="x" * 300, max_tokens=8000, token_cap=100_000, now=NOW
    )

    assert run.tokens_used == 150, "the 8000-token reservation was released"


async def test_a_call_that_cannot_be_afforded_never_reaches_a_provider(registry, answers) -> None:
    # A cap checked after the call is not a cap.
    sess, run = registry
    asked = answers()

    with pytest.raises(BudgetError):
        await complete(sess, run, "drafting", prompt="x", max_tokens=8000, token_cap=10, now=NOW)

    assert asked == [], "the cap must be enforced before anybody is asked"


async def test_an_unset_cap_refuses_rather_than_reading_as_unlimited(registry, answers) -> None:
    """The position every cap in this system takes (§16): the failure it guards
    against is unattended, so a missing configuration must not open the gate."""
    sess, run = registry
    answers()

    with pytest.raises(BudgetError):
        await complete(sess, run, "drafting", prompt="x", token_cap=None, now=NOW)


async def test_a_failed_call_releases_what_it_reserved(registry, answers) -> None:
    """A provider that was down cost nothing, so it must not have spent
    anything — otherwise a flapping agent eats the allowance the working one
    needs, and the run is refused for tokens nobody used."""
    sess, run = registry
    answers(**{"hosted-mid": ProviderError("down"), "*": ("ok", 10, 5)})

    result = await complete(
        sess, run, "tag_attributes", prompt="x" * 300, max_tokens=8000, token_cap=100_000, now=NOW
    )

    assert result.total_tokens == 15
    assert run.tokens_used == 15, "the failed agent's reservation was released in full"


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------


async def test_an_agent_that_will_not_answer_hands_on_to_the_next(registry, answers) -> None:
    """§11.3: an outage degrades rather than halting.

    Retrying the same agent would spend the seconds a provider outage lasts
    anyway; the next one is tried instead.
    """
    sess, run = registry
    asked = answers(**{"hosted-mid": ProviderError("503"), "*": ("ok", 1, 1)})

    result = await complete(sess, run, "tag_attributes", prompt="x", token_cap=100_000, now=NOW)

    assert asked[0] == "hosted-mid", "§11.3 aims attribute tagging at the mid tier"
    assert result.agent_id != "hosted-mid"


async def test_every_agent_refusing_is_one_error_naming_all_of_them(registry, answers) -> None:
    """What §13.4 means by deferring a run rather than failing it — and the
    caller needs to know it was the whole chain, not one bad agent."""
    sess, run = registry
    answers(**{"*": ProviderError("unreachable")})

    with pytest.raises(ProviderError, match="every agent"):
        await complete(sess, run, "drafting", prompt="x", token_cap=100_000, now=NOW)


async def test_a_row_with_a_placeholder_model_is_skipped_rather_than_called(
    registry, answers
) -> None:
    """A placeholder reaches the vendor and returns a bad request hours into a
    run. Skipping it names the fix instead."""
    sess, run = registry
    asked = answers()
    mid = await sess.get(Agent, "hosted-mid")
    mid.model = "<fill in exact model string>"
    await sess.flush()

    result = await complete(sess, run, "tag_attributes", prompt="x", token_cap=100_000, now=NOW)

    assert "hosted-mid" not in asked
    assert result.agent_id != "hosted-mid"


async def test_a_provider_nothing_implements_is_skipped(registry, answers) -> None:
    sess, run = registry
    answers()
    mid = await sess.get(Agent, "hosted-mid")
    mid.provider = "some-new-vendor"
    await sess.flush()

    result = await complete(sess, run, "tag_attributes", prompt="x", token_cap=100_000, now=NOW)

    assert result.agent_id != "hosted-mid"


async def test_nothing_enabled_is_a_different_error_from_nothing_answering(
    registry, answers
) -> None:
    # One is fixed by editing a config row, the other by waiting or by looking
    # at a provider's status page.
    sess, run = registry
    answers()
    for row in await sess.scalars(select(Agent)):
        row.enabled = False
    await sess.flush()

    with pytest.raises(NoAgentAvailable):
        await complete(sess, run, "drafting", prompt="x", token_cap=100_000, now=NOW)


async def test_a_missing_key_takes_that_agent_out_rather_than_the_run(
    registry, answers, monkeypatch
) -> None:
    """A key that was never set is a deployment that has not finished, and the
    rest of the chain may still be able to work."""
    sess, run = registry
    monkeypatch.delenv("ZZ_ABSENT_KEY", raising=False)
    answers(
        **{
            "hosted-mid": provider_module.NotConfigured("ZZ_ABSENT_KEY is unset"),
            "*": ("ok", 1, 1),
        }
    )

    result = await complete(sess, run, "tag_attributes", prompt="x", token_cap=100_000, now=NOW)

    assert result.agent_id != "hosted-mid"
