"""Calling the agent that routing chose (task `P4-15`, §11.3, §11.7, §11.9).

Needs `meridian-core[agent]`, which the worker image lacks, so the fast loop cannot call
a model (§2.1). Keys are read from the variable the registry row names; the worst case is
reserved before a call and settled after; the chain is walked, not retried. Providers:
`anthropic` (SDK), `openai_compatible`, and `relay` (files, `P4-18`). The answer is
returned as data. See docs/features/synthesis.md#calling-a-model.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from .budget import reserve_tokens, settle_tokens
from .framing import without_delimiters
from .logging import get_logger
from .models import Agent, Run
from .routing import NoAgentAvailable, chain_for

log = get_logger(__name__)

__all__ = [
    "Completion",
    "NotConfigured",
    "ProviderError",
    "ask",
    "complete",
]

#: Rough characters per token, for the reservation made before a call. Pessimistic:
#: the excess is released by `settle_tokens`.
CHARS_PER_TOKEN: Final[int] = 3

#: What a call may produce unless the caller says otherwise. Generous, because
#: an answer truncated at the limit costs everything it spent and returns
#: something unusable — the reservation is settled back down anyway.
DEFAULT_MAX_TOKENS: Final[int] = 16_000

#: How long to wait on one agent. A frontier model reasoning over a batch is
#: minutes, not seconds; the chain is what handles an agent that never answers.
DEFAULT_TIMEOUT_S: Final[float] = 600.0


class ProviderError(RuntimeError):
    """One agent could not answer. The chain moves on."""


class NotConfigured(ProviderError):
    """One agent is missing something the operator has to supply.

    Separate from a failure because the fix is different and so is the
    urgency: a key that was never set is a deployment that has not finished,
    not a provider having a bad afternoon.
    """


@dataclasses.dataclass(frozen=True)
class Completion:
    """What a model returned, and what it cost.

    The agent and the model are carried through because every derived write has
    to record them (§2.3, §11.12) — and a caller that had to remember which
    agent answered would eventually attribute an edge to the wrong one.
    """

    text: str
    agent_id: str
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _api_key(agent: Agent) -> str | None:
    """The key, from the environment variable the row names (§11.11).

    `None` when the row names no variable at all, which is how a local endpoint
    that needs no key is configured — distinct from naming one that is unset,
    which is a misconfiguration and refuses.
    """
    name = (agent.api_key_env_var or "").strip()
    if not name:
        return None
    value = os.environ.get(name, "").strip()
    if not value:
        raise NotConfigured(
            f"{agent.agent_id} reads its key from {name}, which is unset. "
            "The registry stores the name of the variable, never the key (§11.11)."
        )
    return value


def _estimate(prompt: str, system: str | None, max_tokens: int) -> int:
    """The worst case, in tokens, for the reservation made before the call."""
    text = len(prompt) + len(system or "")
    return max(1, text // CHARS_PER_TOKEN) + max_tokens


#: Reasoning effort sent to Anthropic models (ADR 0004). Set explicitly rather than left to
#: the model's default, which differs between generations.
EFFORT_ENV: Final[str] = "MERIDIAN_MODEL_EFFORT"
EFFORTS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT: Final[str] = "high"


def effort() -> str:
    """The configured reasoning effort; refuses a value the API would reject."""
    value = os.environ.get(EFFORT_ENV, "").strip().lower() or DEFAULT_EFFORT
    if value not in EFFORTS:
        raise NotConfigured(f"{EFFORT_ENV} must be one of {', '.join(EFFORTS)}, got {value!r}.")
    return value


async def _call_anthropic(
    agent: Agent, *, prompt: str, system: str | None, max_tokens: int, timeout_s: float
) -> tuple[str, int, int]:
    try:
        from anthropic import AsyncAnthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the extra
        raise NotConfigured(
            "the anthropic SDK is not installed. This image was built without "
            "`meridian-core[agent]`, which is how the fast loop is kept unable "
            "to call a model (§2.1)."
        ) from exc

    client = AsyncAnthropic(api_key=_api_key(agent), timeout=timeout_s)
    kwargs: dict[str, Any] = {
        "model": agent.model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "adaptive"},
        # Explicit, because model defaults differ by generation (ADR 0004).
        "output_config": {"effort": effort()},
    }
    if system:
        kwargs["system"] = system

    try:
        # Streamed, and the final message taken from the helper. A long answer
        # over a non-streaming request hits the HTTP timeout and loses
        # everything it had already produced.
        async with client.messages.stream(**kwargs) as stream:
            message = await stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 - every provider failure is the same to us
        raise ProviderError(f"{agent.agent_id}: {type(exc).__name__}") from exc
    finally:
        await client.close()

    if message.stop_reason == "refusal":
        # A safety refusal is not a provider fault and retrying it elsewhere is
        # the right move, so it reads as one more agent that could not answer.
        raise ProviderError(f"{agent.agent_id} declined the request")

    text = "".join(block.text for block in message.content if block.type == "text")
    return text, message.usage.input_tokens, message.usage.output_tokens


async def _call_openai_compatible(
    agent: Agent, *, prompt: str, system: str | None, max_tokens: int, timeout_s: float
) -> tuple[str, int, int]:
    """The local tier (§11.7). Ollama, llama.cpp and vLLM all speak this."""
    import httpx

    endpoint = resolved(agent, agent.endpoint, "endpoint")
    # The model can name a variable too (`P6-06`): which model a local server
    # runs is chosen in .env, like its address, not by a migration.
    model = resolved(agent, agent.model, "model")

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    headers = {}
    key = _api_key(agent)
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": max_tokens},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001
        # The type only. A local endpoint's error body can echo the prompt back.
        raise ProviderError(f"{agent.agent_id}: {type(exc).__name__}") from exc

    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"{agent.agent_id} returned a response with no message") from exc

    usage = body.get("usage") or {}
    return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def resolved(agent: Agent, value: str | None, what: str) -> str:
    """A registry value, with a `${VARIABLE}` read from the environment.

    The registry stores `${LOCAL_LLM_URL}` verbatim and resolves at call time,
    the same as the key. An unresolved one is a variable nobody set.
    """
    value = (value or "").strip()
    if not value:
        raise NotConfigured(f"{agent.agent_id} has no {what} configured.")
    if value.startswith("${") and value.endswith("}"):
        name = value[2:-1]
        value = os.environ.get(name, "").strip()
        if not value:
            raise NotConfigured(f"{agent.agent_id} reads its {what} from {name}, which is unset.")
    return value


#: Where a relay agent's prompts and answers are exchanged (`P4-18`).
RELAY_DIR_ENV: Final[str] = "MERIDIAN_RELAY_DIR"


def relay_key(prompt: str, system: str | None) -> str:
    """The name one exchange is filed under: a digest of exactly what was asked.

    Taken with the random fence masked, so the same question asked again after a
    deferral finds the same answer. See docs/features/synthesis.md#relay.
    """
    canonical = without_delimiters(f"{system or ''}\0{prompt}")
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return digest[:24]


async def _call_relay(
    agent: Agent, *, prompt: str, system: str | None, max_tokens: int, timeout_s: float
) -> tuple[str, int, int]:
    """A model that answers through files, not a network (`P4-18`).

    Writes ``<key>.prompt.json``; until ``<key>.answer.txt`` exists the call fails as an
    unreachable provider does, and the run defers (§13.4).
    See docs/features/synthesis.md#relay.
    """
    del max_tokens, timeout_s
    root = os.environ.get(RELAY_DIR_ENV, "").strip()
    if not root:
        raise NotConfigured(
            f"{agent.agent_id} is a relay agent and {RELAY_DIR_ENV} is unset, "
            "so there is nowhere to leave its prompts."
        )
    relay = pathlib.Path(root)
    key = relay_key(prompt, system)
    answer = relay / f"{key}.answer.txt"

    if answer.is_file():
        text = answer.read_text(encoding="utf-8")
        if text.strip():
            # No provider reports usage here, so the ledger is charged the same
            # estimate the reservation used. Counting nothing would make a
            # relay run look free on the week-on-week comparison (§11.9).
            used_in = max(1, (len(prompt) + len(system or "")) // CHARS_PER_TOKEN)
            return text, used_in, max(1, len(text) // CHARS_PER_TOKEN)

    pending = relay / f"{key}.prompt.json"
    if not pending.exists():
        relay.mkdir(parents=True, exist_ok=True)
        pending.write_text(
            json.dumps(
                {
                    "key": key,
                    "agent_id": agent.agent_id,
                    "model": agent.model,
                    "system": system,
                    "prompt": prompt,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    raise ProviderError(f"{agent.agent_id}: waiting for {key}.answer.txt in the relay directory")


_SHAPES = {
    "anthropic": _call_anthropic,
    "openai_compatible": _call_openai_compatible,
    "relay": _call_relay,
}


async def complete(
    sess: AsyncSession,
    run: Run,
    task_type: str,
    *,
    prompt: str,
    system: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    token_cap: int | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    now: dt.datetime,
) -> Completion:
    """Ask the best agent for this task, falling down the chain on failure.

    `token_cap=None` refuses. Raises `NoAgentAvailable` when the registry has nothing to
    offer, and `ProviderError` when every agent in the chain refused (defer, §13.4).
    """
    chain = await chain_for(sess, task_type)
    if not chain:
        raise NoAgentAvailable(f"no enabled agent declares {task_type!r}.")

    reserved = _estimate(prompt, system, max_tokens)
    failures: list[str] = []

    for agent in chain:
        shape = _SHAPES.get(agent.provider)
        if shape is None:
            failures.append(f"{agent.agent_id}: unknown provider {agent.provider!r}")
            continue
        if not (agent.model or "").strip() or (agent.model or "").startswith("<"):
            # The seeded rows ship with a placeholder model string. Saying so
            # is the difference between "fill this in" and a provider error
            # nobody can act on.
            failures.append(f"{agent.agent_id}: no model string is configured")
            continue

        # Reserved per agent, because a failed call on one agent costs nothing
        # and must not eat the allowance the next one needs.
        await reserve_tokens(sess, run.run_id, reserved, cap=token_cap)
        try:
            text, input_tokens, output_tokens = await shape(
                agent, prompt=prompt, system=system, max_tokens=max_tokens, timeout_s=timeout_s
            )
        except ProviderError as exc:
            await settle_tokens(sess, run.run_id, reserved=reserved, actual=0)
            log.warning("agent could not answer", extra={"agent": agent.agent_id})
            failures.append(str(exc))
            continue

        await settle_tokens(
            sess, run.run_id, reserved=reserved, actual=input_tokens + output_tokens
        )
        run.agent_id = agent.agent_id
        await sess.flush()
        return Completion(
            text=text,
            agent_id=agent.agent_id,
            model=agent.model or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    raise ProviderError(f"every agent for {task_type!r} refused: {'; '.join(failures)}")


async def ask(
    sess: AsyncSession,
    task_type: str,
    *,
    prompt: str,
    system: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Completion:
    """`complete` for work that is not a synthesis run (`P6-06`): a reader's question.

    Walks the same chain, with the same refusals, and reserves nothing against a
    run — there is none. The caller holds the cap: `chat` checks a daily token
    allowance before it calls, for the reason every cap here has no default.
    """
    chain = await chain_for(sess, task_type)
    if not chain:
        raise NoAgentAvailable(f"no enabled agent declares {task_type!r}.")

    failures: list[str] = []
    for agent in chain:
        shape = _SHAPES.get(agent.provider)
        if shape is None:
            failures.append(f"{agent.agent_id}: unknown provider {agent.provider!r}")
            continue
        if not (agent.model or "").strip() or (agent.model or "").startswith("<"):
            failures.append(f"{agent.agent_id}: no model string is configured")
            continue
        try:
            text, input_tokens, output_tokens = await shape(
                agent, prompt=prompt, system=system, max_tokens=max_tokens, timeout_s=timeout_s
            )
        except ProviderError as exc:
            log.warning("agent could not answer", extra={"agent": agent.agent_id})
            failures.append(str(exc))
            continue
        model = agent.model or ""
        if model.startswith("${"):
            with contextlib.suppress(NotConfigured):
                model = resolved(agent, model, "model")
        return Completion(
            text=text,
            agent_id=agent.agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    raise ProviderError(f"every agent for {task_type!r} refused: {'; '.join(failures)}")
