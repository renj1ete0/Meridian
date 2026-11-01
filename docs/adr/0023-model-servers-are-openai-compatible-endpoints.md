# 0023. Model servers are OpenAI-compatible endpoints; loading is the server's concern

- **Status:** Accepted
- **Date:** 2026-11-01
- **Tasks:** `B-248`

## Context

The GPU machine runs Windows with two unlike cards: the language model on one, the embedder and
reranker meant for the other, which PyTorch cannot reach on Windows. The operator manages that
machine with a model manager of their own, and wants a model loaded when a request arrives and its
memory freed when idle. Other deployments will use other servers (llama.cpp, Ollama, LM Studio,
vLLM). The embedding client spoke only Meridian's own sidecar API.

## Decision

Meridian connects to model servers by URL and speaks the OpenAI-compatible API: chat completions,
`/v1/embeddings`, `/v1/models` for the model check, and `/v1/rerank` in the Jina/Cohere shape for
reranking. It ships no client library of its own and no knowledge of any particular server or
model manager. Starting, swapping and unloading models belong to the server.

A server that loads on request signals the wait in the standard way: it holds a request while a
short load finishes, and past that answers `503` (or `429`) with `Retry-After`. Meridian reads that
as "away until then", not as a failed call, for embedding and for the language model alike. With
such a server, health checks point at the server, not at a loaded model; a check that waits for a
loaded model and a server that waits for a request would wait for each other.

## Consequences

- Any OpenAI-compatible server works without Meridian code; the operator's model manager is one of
  them, and changes to it need no change here.
- What is beyond plain OpenAI is optional: `/v1/rerank` (reranking is off without it), llama.cpp's
  `/tokenize` (used to cut long texts exactly as the stored vectors were cut; without it the server
  truncates).
- The name the server knows a model by may differ from Meridian's (`bge-m3` against `BAAI/bge-m3`),
  so it is configurable; the model check compares against the server's own list.
- A first request after an idle unload waits for the load; a search in that window falls back to
  words only and says so.

## Alternatives considered

- **A client library for the operator's model manager.** Rejected: every tool would need it, and
  the standard already carries what is needed, including the retry signal.
- **Meridian starting and stopping models.** Rejected: it would tie Meridian to one server and one
  operating system, and the server already knows what fits on its cards.
- **Anthropic's Messages API as the local protocol.** Not needed: local servers speak OpenAI's
  first; some also serve `/v1/messages`, which a server may offer for other tools.
