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
- **tag** tags attributes, then advances the mark. Its prompt lists, under each active
  attribute, up to `VALUES_IN_PROMPT` (8) wordings already in the graph, most used first
  (`B-170`): the prompt asked for "the same wording for the same value" but never showed the
  wording, and after a few dozen values `operating_environment` held both "road" and
  "public road", `autonomy_level` both "driverless" and "unmanned". An attribute whose values
  are all phrased differently discriminates nothing.
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

### The cycle

`worker/orchestrate.py` (`P4-09`, `P4-16`) is the thing that calls the run's stages, with the
`--dry-run` and `--once` flags §11.10 asks for.

- **It lives in the worker package, not `services/orchestrator/`.** When it was written the
  stages were not built, and a separate service would have meant a Dockerfile, a compose entry,
  a release-script line and a healthcheck for a process with nothing to do. The worker package
  already ran `python -m worker.<x>` entry points; moving the module is a rename.
- **`--dry-run` is a rolled-back transaction, not a flag each stage checks.** A flag one stage
  has to remember is a flag one stage will forget. The whole cycle runs in a transaction that is
  rolled back unconditionally, and the journal records and prints every intended call.
- **A dry run does not call the model.** It would spend money and then roll back the *record*
  of having spent it, understating the ledger. It reports the batch and prompt size and stops.
- **A cycle that changes nothing stops the loop.** The exit condition is progress, not
  emptiness: a cycle that advanced neither the mark nor any counter will do the same next time,
  and an orchestrator spinning on a daily timer looks, from outside, like a busy one.
- **Unbuilt stages are named** (`BUILT_BY`) rather than absent: "there is no tagging stage" and
  "the tagging stage did nothing" read the same in a log. A stage is in `RUNNERS` or in
  `BUILT_BY`, never both. `done` is in neither: it is a state, not work.
- **The batch is ordered by chunk id, never by novelty.** §11.9 suggests most-novel first when a
  day exceeds the budget, which is incompatible with a high-water mark: reasoning over chunk 900
  and marking 900 abandons 400–899. Novelty is spent as a filter instead. `pull` leaves out
  chunks the gate judged duplicates, chunks a re-crawl superseded (`P1-32`), and junk-tier
  sources (`P2-21`), as search and the map do: text the corpus holds elsewhere, or has decided
  is not evidence. With `MERIDIAN_SYNTHESIS_ON_TOPIC_ONLY` (`B-63`) it also stops short of any
  passage not yet examined by content labelling.
- **The batch is small** (`BATCH`, 40 chunks), so a frontier model reads all of it rather than
  summarising the middle, and a failure costs one batch rather than a day. More cycles drain the
  corpus; `max_cycles` bounds them.
- **The batch is carried between stages, not re-queried.** `extract` and `tag` must see the same
  passages or their citation numbers mean different things, and the crawler writes to the table
  concurrently. A run resumed at `extract` therefore has no batch and says so, rather than
  reasoning over different passages than its last answer's citations referred to.
- **The mark moves in `tag`, not `extract`**, because `tag` is the last stage that reads the
  batch, and only over a batch a model actually saw. It moves even when there was nothing to tag:
  an empty active attribute set is a configuration, and refusing to advance over it would re-read
  the batch forever.
- **Mention vectors** (`B-40`) are computed for the batch's names and for entities without one,
  so resolution can see that two differently worded names mean the same thing. An unreachable
  embedder degrades to names alone.
- **The run is deferred only when the model is unreachable**: no agent declares the task, every
  agent refused, or the budget will not allow the call (§13.4). A deferred run is left unfinished
  at its stage, rather than walking on to stages that would fail the same way. Within one wake
  it is not retried: asking again only closed the deferred run and opened another that deferred
  the same way, five throwaway runs per tick on the relay's first afternoon.
- **An unset budget refuses before the provider is asked**, with its own message: "nobody set a
  token cap" sends someone to Admin, "every agent refused" to the registry.
- **`--stop-after` leaves the run unfinished on purpose**, so it resumes rather than being
  treated as a fresh crash: the way to step through a run by hand.
- **The pool is closed in `_main`, inside the loop that created it.** A library function that
  tears down shared engines surprises a caller that calls it twice, and disposing from a second
  `asyncio.run` fails at exit with a traceback that says nothing about the cause.

<a id="daemon"></a>**The daemon** (`--daemon`) is its own service rather than a timetable row:
the scheduler spawns jobs in its own container, the worker image, which deliberately lacks
`meridian-core[agent]` (§2.1). It runs daily, and checks the backlog every `POLL_S` (15 min) so
a run starts early once `DEFAULT_EARLY_AT` (500) chunks wait past the mark; below that a run
spends a frontier model on a handful of passages. The daily run still happens on a quiet
corpus, because the run that finds little is the one that reports the corpus is quiet.

### Resuming

A run that defers (no model answered) stays unfinished at that stage, and the next wake resumes
it. The batch `pull` chose lives only in memory, so a run resumed at `extract` or `tag` re-reads
it from the run's mark (`B-162`). Only `tag` moves the mark, so these are the passages it deferred
on, and a relay answer filed under that prompt is found. A model reached by `tag` counts as having
read the batch, so a run resumed there moves the mark.

Until `B-162` a resumed run had no batch: it noted "no batch", finished having reasoned over
nothing, and left the next run to pull the same passages and ask the model again for the stages
already answered. Through the relay that cost only time; with a paid model, every deferral would
have bought its extraction twice. Found in the first attended relay session after the stack moved
to a server target (2026-10-07: 11 relations and 2 attribute values from 40 passages, each citing
its passage, with the relay's model as provenance).

### Run state

`runs.py` (`P4-08`, §11.10) moves one row through the stages and does none of the work, so the
resumability rules are testable without a model or a corpus.

- **At most one unfinished run, enforced by the database.** Two orchestrators would double-spend
  against the monthly ceiling and race the same mark. A unique partial index makes the second an
  error; an application check would be a race. `begin_or_resume` takes `FOR UPDATE`, and the
  index catches the case the lock cannot: two callers both finding no row and inserting.
- **Resuming is the default.** A wake that started fresh whenever it found a run in progress
  would redo a day's extraction after every crash, and pay twice.
- **A heartbeat tells a crashed run from a live one**; both are `running` with a stage. NULL
  reads as stale, since a run that died before completing a step most needs taking over. The
  stale window is generous because one stage can wait minutes on a model, and a window that
  fired during normal work would hand the row to a second process.
- **Stages only move forward, and the stage tuple is the order.** The stage claims what is
  committed; moving back would redo work and the duplicates would be indistinguishable.
- **One mark for the system, not per run** (`B-36`). A new run starts where the furthest earlier
  run reached. Left unset, `pull` read "no mark" as "from the beginning", and every run re-read
  and re-paid for the first batch. Furthest rather than latest, because a failed run's mark only
  ever moved over committed writes.
- **The mark is monotonic and refuses while writes are pending** (§6.3, `P4-11`). Marking first
  and writing second loses chunks permanently and silently: nothing is missing from the corpus,
  only from the reasoning over it. `mark()` refuses rather than clamps (a clamp would hide the
  caller's bug), and refuses while unflushed objects sit in the session. `advancing()` does the
  ordering, and since writes and mark share a transaction they commit together or not at all.
- **Counters accumulate and refuse negative deltas**, so a per-run figure compared week on week
  means the same thing in every run.
- **`defer` keeps the stage, leaves `completed_at` empty, clears the heartbeat** (nobody holds the
  run) and takes no `now`, since it records no time. **`finish`** sets `done` from any stage (a
  run with nothing to do finishes at `pull`, and an unclosable run would block every later one
  through the unique index), and clears `error`: a deferred run can resume and finish, and a row
  saying `done` with an error reads as "finished with a problem". The column means "why this run
  is not finished"; what stopped it earlier is in `steering_log` and the logs.

### Routing rules

`routing.py` (`P4-07`, §11.3) answers "who"; the caller asks. Swapping models is a config row, not
a code change.

- **A task type is a name from a fixed set** (`TASK_TYPES`, with a drift test). `task_types` is
  free text, so a typo is an agent that is never chosen, with the expensive agent handling
  everything; the seeded registry had exactly that typo.
- **Disabled is never routed, and an empty `task_types` declares nothing.** Absent is refused,
  as in `budget.py` and `trust.py`. The three refusals differ because only one is fixed by
  editing a row: an empty registry (nothing seeded), no row enabled (placeholders never filled
  in, how every install starts), or none declaring the task or large enough.
- **An unrecorded `max_context` refuses** when the caller gives a payload size: an agent that
  cannot be shown to fit is found out from a provider error mid-run otherwise.
- **The strongest agent is not the right agent.** §11.3 aims each task at a *tier*: strongest for
  hard reasoning, mid for attribute tagging, any multilingual for translation. Sending narrow
  schema-constrained work to the frontier model is the bill the registry exists to avoid, which
  is why `quality_tier` is ordinal and separate from `cost_tier` (§11.12). Unordered rows sort by
  distance from the target; ties go to the stronger, since bad output is harder to detect than
  an invoice (§16). An unrecorded tier reads as 0. `agent_id` breaks ties so two runs choose
  the same.
- **Availability is not quality.** An `opportunistic` agent that may be asleep still routes
  first if best; the chain is for when it does not answer.
- **The stated fallback chain is walked, not re-sorted** (a fallback is a stated preference),
  skipping links that are disabled or do not declare the task, and stopping on a cycle (nothing
  prevents A → B → A). **It is a preference, not the whole answer**: the seeded mid tier pointed
  at a frontier model that did not declare attribute tagging, leaving tagging with no fallback
  while a local agent that declared it sat unused. Every other eligible row follows the chain,
  nearest the target first.

### Calling a model

`provider.py` (`P4-15`) makes the call routing chose.

- **The key is read from the variable the registry names** (`api_key_env_var`), because §11.11
  keeps credentials out of a database that is snapshotted off-device.
- **The chain is walked, not retried.** A provider that is down stays down for the seconds a
  retry would take.
- **Two shapes, not one client.** Local servers share the OpenAI-compatible protocol; Anthropic
  goes through its SDK, not a shim. The response is returned as text and a token count, never
  interpreted: framing goes in, validation comes out.
- **The reservation is pessimistic** (characters per token set low), since under-reserving makes
  the cap meaningless and over-reserving is released by `settle_tokens` moments later.
- <a id="relay"></a>**The relay** (`P4-18`) stands in for an API key with an operator-attended
  session. An exchange is filed under a digest of exactly what was asked, with the random fence
  masked (`framing.without_delimiters`): after a deferral the mark has not moved, `pull` chooses
  the same passages, and the second ask finds the first's answer with no state but the files.
  The answer goes through the same parser, guards and writes as any model's, and provenance
  records the row's model string. Nothing is exposed: no port, no credential; it trusts only the
  relay directory, which only the operator can write.

### Budgets

`budget.py` (`P4-10`, `P4-13`) bounds the one loop nothing else does: gaps emit seeds, seeds grow
the corpus, a larger corpus has more gaps. §16 calls the risk manageable "if caps are set before
first autonomous run", and this module enforces that ordering.

- **Absent is refused, never unlimited**, because the loop is unattended and a default of
  infinity makes the first signal the invoice. `check_can_start_run` refuses in the order
  someone would fix things (no budget row, caps missing, ceiling reached) and returns the budget
  so the run is governed by the numbers it was checked against.
- **Cost is checked before a run, not during it.** A run that overshoots is recorded honestly
  rather than killed halfway with a half-written graph; the month's *next* run is refused. The
  month is counted on `started_at`, so a long run spanning the 1st counts from the start, and
  runs without a recorded cost count as zero rather than blanking the total.
- **The calendar month in UTC**, not a rolling 30 days, so the ceiling resets with the invoice.
- **Token reservations are all or nothing, on a locked row**: two concurrent calls reading 900
  against a cap of 1000 would otherwise both pass. The remaining allowance is returned so a caller
  can stop before being refused mid-answer. **Settling only releases**: an answer that cost more
  than reserved keeps the larger figure, since those tokens were spent.
- **Spend is additive**, so the per-run figure means "all calls" in every run.
- **`BudgetError` is not `ValidationError`**: the latter goes back to a model as a tool failure
  it might retry with better arguments, which is exactly wrong for a budget.

### Framing and parsing

<a id="framing-design"></a>**Framing** (`framing.py`, §11.8). The crawler fetches arbitrary
content and hands it to a model holding write tools. Framing does not stop a determined
injection; it tells the model which text is data, so the obvious attacks stop working. The
guards (`P4-05`) and screening (`P4-14`) are what refuse.

- **A delimiter the content cannot forge**: a fixed `<untrusted>` can be contained in a page to
  close the fence early; a random one did not exist when the page was written. For recognising
  the same question twice (`P4-18`), `without_delimiters` masks it; what is sent keeps it.
- **Nothing is stripped**: `P1-23` found articles *about* injection quote its phrases, and a
  corpus that rewrote its documents could not answer questions about them.
- **The instruction comes first**, worded as a fact about the text ("a quotation from a
  document") rather than a plea, which a page could argue with. Text after the payload is text
  an injection can imitate.
- **Attribution travels inside the fence**: URL and tier beside each passage, so a citation is
  something the model read rather than reconstructed, and it can weigh conflicting sources as a
  reader would.

**Parsing** (`proposals.py`, `P4-16`). Named "proposals" because `worker/extract/` is document
text extraction, and what a model returns is a suggestion the writes may refuse.

- **The parse never raises**: one bad answer would otherwise end the unattended run instead of
  skipping a batch. Every unusable item is a `Rejection` with a reason.
- **A truncated answer keeps its good items.** The array is tried first; failing that, each
  top-level object is parsed alone. The scanner is hand-written because JSON nests (a regex
  `{.*}` spans every item) and must be string-aware (a quoted brace would close an object early).
  An unclosed final span yields nothing, and the truncation is reported beside the kept items,
  so repeated truncation does not look like a terse model.
- **Citations are passage numbers mapped to the batch that was sent.** A number outside the
  batch raises `CitationOutOfRange` for the whole proposal rather than dropping the number: the
  citations are the evidence, and an edge written from two of three would claim support it was
  never given.
- **The vocabulary is read from the schema's `constrained(...)` tuples**, so the prompt cannot
  drift from the CHECK constraints. `annotation` is excluded: it is the reader's own notes
  (`P6-05`), and `resolve_mention` refuses it again at write time.
- **Prompts carry what the stage needs**: topics when the run has them (left out, not empty,
  otherwise); the *active* attributes read from the database (§7.3 caps and audits them, so a
  list in code would be a second schema); and existing entity names, so tagging uses the graph's
  spellings instead of inventing near-misses.

## Configuration

- Registry rows: **Admin → Agent registry** (model strings are editable there); seeded from
  `config/agents.yaml`, disabled.
- Keys and endpoints: see [reference/environment.md](../reference/environment.md#models-synthesis-and-the-ask-panel).
- Budget caps: **Admin**, budget screen. Runs refuse until all caps are set.

## Current state

Every stage up to `tag` is built. Synthesis has run only through the relay, in attended
sessions: the second (2026-10-07) wrote 34 relations and 5 attribute values from 120 passages
in three batches, every one citing its passage, and found `B-162`. What it showed about passage
selection is `B-163`. Unattended runs wait on a model (ADR 0002, `B-135`) and on enabling the
orchestrator.

## Tests

`tests/integration/test_orchestrate.py`, `test_runs.py`, `test_budget.py`,
`test_provider.py`, `test_routing.py`; `tests/unit/test_framing.py`, `test_proposals.py`,
`test_orchestrator_image.py`.
