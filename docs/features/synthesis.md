# Synthesis

Synthesis is the slow loop. It reads new passages in batches, asks a language model which
entities and relations they state, and writes what survives validation into the knowledge
graph, citing the passages. It is the only part of the system, besides the Ask panel, that
calls a model. It runs separately from ingestion, so a model outage pauses synthesis and
nothing else.

- **Code:** `services/worker/worker/orchestrate.py` (the cycle);
  `packages/meridian_core/meridian_core/runs.py` (state), `proposals.py` (prompts and
  parsing), `framing.py`, `routing.py`, `provider.py`, `budget.py`, `writes.py`,
  `validation.py`, `trust.py`
- **Tasks:** `P4-04`–`P4-18`, `B-63`
- **Decisions:** [ADR 0002](../adr/0002-model-routing.md),
  [ADR 0004](../adr/0004-hosted-claude-generation.md)

## How it works

**A run** moves through `pull → extract → tag → score → analogies → gap → seed → done`.
State is one row in `runs`: a crash at `tag` resumes at `tag`. At most one run is unfinished
at a time (a unique partial index). A heartbeat tells a crashed run from a live one.

- **pull** chooses the next batch past the high-water mark: cleared (screened) sources only,
  known duplicates left out, ordered by chunk id. With `MERIDIAN_SYNTHESIS_ON_TOPIC_ONLY`, only
  passages labelled on a topic.
- **extract** frames the batch, asks the model for entities and relations, parses the answer,
  resolves names to nodes and writes edges.
- **tag** tags attributes, then advances the mark.
- `score`, `analogies`, `gap` and `seed` are named stages that say which task builds them.

`python -m worker.orchestrate` runs cycles until one makes no progress (`--max-cycles` caps
it). `--once` runs one cycle; `--dry-run` runs inside a transaction that is always rolled back,
and does not call the model; `--daemon` stays up and runs on a schedule (daily by default).

<a id="routing"></a>**Routing** (`routing.py`). Models are rows in the agent registry
(**Admin → Agent registry**), each with a provider, a model string, the task types it declares,
a quality tier, a cost tier, an optional `route_order` and an optional `fallback_agent_id`.

Rows with a `route_order` are tried first, lowest first, skipping any that are disabled or do
not declare the task (`B-137`, ADR 0002). The seeded order is:

| Order | Row | Provider | Declares |
|---|---|---|---|
| 10 | `local-llamacpp` | openai_compatible (`LOCAL_LLM_URL`) | extraction, tagging, translation, triage |
| 10 | `local-chat` | openai_compatible (`LOCAL_CHAT_LLM_URL`) | chat |
| 20 | `hosted-compatible` | openai_compatible (`HOSTED_LLM_URL`, `HOSTED_LLM_MODEL`, `HOSTED_LLM_API_KEY`) | synthesis tasks and chat |
| 30 | `hosted-frontier`, `hosted-mid` | anthropic | reasoning tasks; tagging |
| 40 | `claude-code-session` | relay | extraction, tagging |

The relay declares no `chat`, so the Ask panel never waits on an attended session. Rows
without an order follow every ordered row, by the older rule: closest to the task's target
tier, ties to the stronger, then the row's `fallback_agent_id` chain. When every stage fails
or is unconfigured, the run defers and tries again on its next wake.

**Providers** (`provider.py`):

| Provider | Talks to | Credential |
|---|---|---|
| `anthropic` | The Claude API, through the official SDK, streamed, adaptive thinking, effort from `MERIDIAN_MODEL_EFFORT` (default `high`) | Variable named by `api_key_env_var` |
| `openai_compatible` | Any `/v1/chat/completions`: vLLM, llama.cpp, Ollama, or a hosted API such as DeepSeek or OpenRouter | Optional bearer key from `api_key_env_var` |
| `relay` | A folder: the prompt is written to `<key>.prompt.json`, and the run defers until `<key>.answer.txt` exists | None; an attended Claude session answers |

Endpoints and models may be written as `${VAR}` and are read from the environment at call
time. A missing variable refuses that agent and the chain moves on.

**Budgets** (`budget.py`). Caps on tokens per run, seeds per run, and a monthly cost ceiling
in UTC calendar months. **A missing cap is a refusal, never unlimited.** The worst case
(prompt plus `max_tokens`) is reserved before each call and settled afterwards. The month's
next run is refused once the ceiling is reached.

<a id="framing"></a>**Framing** (`framing.py`, `P4-06`). Retrieved text is untrusted. It goes
inside a delimiter that is random per call, with the instruction placed before the data.
Nothing in the text is stripped or rewritten. Framing is defence in depth; validation is what
actually refuses.

**Parsing** (`proposals.py`). The parse never raises: whatever it cannot use comes back as a
rejection with a reason. A truncated answer keeps its complete items. The model cites passage
*numbers*, and the server maps them to chunk ids, so an invented id has nowhere to land. The
vocabularies in the prompt are read from the schema's own constraints.

## Design choices

- **The worker image cannot call a model.** The SDK is in an optional extra
  (`meridian-core[agent]`) that the crawl image is built without (§2.1).
- **The mark advances after the writes**, never before, so a failure can only cause a batch
  to be read again, never skipped. Re-reading is safe because `add_edge` corroborates a claim
  it already holds.
- **Deferred is not failed.** When no model is reachable, the run defers at its stage and the
  next wake continues.
- **Model refusals move down the chain.** A safety refusal reads as one more agent that
  could not answer. The API's own server-side fallback is not used: it would answer from a
  different model while provenance records the row's model.
- **One refusal does not end a run.** Malformed items, out-of-range citations and refused
  writes are journalled, and the batch goes on.

## Configuration

- Registry rows: **Admin → Agent registry** (model strings are editable there); seeded from
  `config/agents.yaml`, disabled.
- Keys and endpoints: see [reference/environment.md](../reference/environment.md#models-synthesis-and-the-ask-panel).
- Budget caps: **Admin**, budget screen. Runs refuse until all caps are set.

## Current state

Every stage up to `tag` is built. Synthesis has run only through the relay, in one attended
session. Unattended runs wait on a model (ADR 0002, `B-135`) and on enabling the
orchestrator.

## Tests

`tests/integration/test_orchestrate.py`, `test_runs.py`, `test_budget.py`,
`test_provider.py`, `test_routing.py`; `tests/unit/test_framing.py`, `test_proposals.py`,
`test_orchestrator_image.py`.
