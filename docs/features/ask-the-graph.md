# Ask the graph

The Ask panel answers a reader's question in prose, using a language model that sees only
passages and nodes retrieved from the corpus. Every claim in an answer cites what it came
from. Citations the model invents are removed before the answer is stored. The panel is on
every reading surface except Admin.

- **Code:** `packages/meridian_core/meridian_core/chat.py`, `framing.py`, `provider.py`,
  `models/chat.py`; `services/api/api/routes/admin.py` (`POST /api/admin/chat/ask`),
  `routes/explore.py` (thread reads); `web/src/explore/AskPanel.tsx`
- **Tasks:** `P6-06`, `P6-07`
- **Decisions:** [ADR 0002](../adr/0002-model-routing.md)

## How it works

1. **Retrieve.** Hybrid search over the question plus the names of any context the reader
   attached (a node page registers itself as removable context). Up to 8 passages, at most 2
   per source. Then the graph edges whose supporting passages overlap them.
2. **Frame.** Passages go inside a random fence with a preamble saying they are quotations,
   not instructions ([synthesis.md](synthesis.md#framing)). The model is asked to cite
   passages as `[n]` and nodes as `{Nn}`.
3. **Call** the agent routed for task type `chat` ([synthesis.md](synthesis.md#routing)).
4. **Check.** Every marker is looked up against what was supplied. A marker pointing at
   nothing is removed from the text. Valid node markers become the node's name, with a chip.
5. **Store** the thread and message with the validated citations, the agent, the model and
   the token counts.

## Design choices

- **The same retrieval as Find** (§12.4). There is no separate retrieval path that could
  disagree with search.
- **Citations are checked, not trusted** (§2.6). A stored answer only points at evidence its
  model was given.
- **A daily token cap with a default** (`MERIDIAN_CHAT_DAILY_TOKENS`, 200,000 per UTC day).
  A question over the cap is refused in words.
- **It is a write.** Asking spends tokens and stores a thread, so it is under `/api/admin`.
  Reading threads is under `/api/explore`.
- **Not in the worker.** The API calls it on a reader's click; the worker never calls a model
  (§2.1).

## Configuration

ADR 0002 sets the order for this panel: a local model first, then a hosted
OpenAI-compatible API, and never the relay, because a reader cannot wait for an attended
session.

- Local: set `LOCAL_CHAT_LLM_URL` (e.g. `http://host.docker.internal:<port>/v1` locally) and
  `LOCAL_CHAT_MODEL`, recreate the API (`up -d --no-deps api`), then enable `local-chat` in
  **Admin → Agent registry**.
- Hosted fallback: set `HOSTED_LLM_URL`, `HOSTED_LLM_MODEL` and `HOSTED_LLM_API_KEY`, then
  enable `hosted-compatible`.
- In production the API has no route out. The model server must be reachable on the `lan`
  network with an nft `lan_allow` entry, or run as a compose service on `internal`
  (`B-135`).

## Current state

Wired and tested, but never run against a real model. `P6-06` and `P6-07` stay open until one
has answered. With no agent enabled, Admin's "Nothing serves" line lists `chat`, which is
correct.

## Tests

`tests/integration/test_chat*.py`; `tests/unit/test_chat*.py`, `test_framing.py`;
`web/tests/ask*.test.tsx`.
