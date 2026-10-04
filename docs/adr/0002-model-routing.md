# 0002. Which model answers what, and in which order

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-135`, `B-137`

## Context

Two things need a language model. Synthesis builds the knowledge graph on a schedule, from
cited passages. The Ask panel answers a reader's question while they wait. The agent
registry already supports three kinds of provider: Anthropic's API, any OpenAI-compatible
endpoint (local servers such as vLLM, llama.cpp and Ollama, and hosted ones such as DeepSeek
or OpenRouter), and a relay, where prompts are left in a folder for an attended Claude
session to answer. Rows chain through `fallback_agent_id`. On some deployments none of these
will be available, or only some of them.

## Decision

- **Synthesis** tries a local model first, then a hosted OpenAI-compatible API, then the
  relay. A stage that is not configured or not reachable is skipped. When no stage can
  answer, the run defers and tries again later; it never fails for want of a model.
- **Claude is the preferred model for quality.** Reaching it through the relay, or through
  the hosted Anthropic rows, is a first-class option. It comes last in the default order
  because the cheaper stages are tried first, not because it is less trusted.
- **The Ask panel** tries a local model, then the hosted OpenAI-compatible API. The relay
  is not used there: a reader cannot wait for an attended session to answer.
- A seeded, disabled row for a hosted OpenAI-compatible API is configured from `.env`
  (endpoint, model, key variable), the same way the local rows are.

## Consequences

- With no model configured, the stack still crawls, searches and maps. Synthesis waits,
  and the Ask panel says no model is available.
- Every stage writes the same provenance (agent, model, quality tier), so the graph records
  which model wrote which edge, whichever stage answered.
- The operator controls cost through the order of the chain and the budget caps, both of
  which can be changed in Admin.

## Alternatives considered

- **Hosted API first.** More consistent quality, but it spends money on every run, even
  when a local model could answer.
- **Claude only.** The best quality, but it makes synthesis depend on one paid provider or
  on an attended session.
