# Roadmap

Build phases and their acceptance checkpoints. This is the *shape* of the build;
[TASKS.md](../TASKS.md) at the repo root holds the live task list and current state.

**The system is useless until the loop closes.** Build thin end-to-end first — a
narrow slice that runs from crawl to graph to interface — rather than finishing any
one layer. Each phase should run unattended before the next begins.

| Phase | Deliverable | Done when |
|---|---|---|
| **0** | `meridian_core` models, migrations, seed script, cold-start config | `make migrate && make seed` yields a clean, **empty** database ready to crawl — config only, no content |
| **1** | Queue, fetcher, extractor, raw retention | Runs 48h unattended without failing. The result becomes the first development corpus |
| **2** | Embeddings, novelty gate, hybrid search, minimal Explore | **Go/no-go** — is searching the corpus already useful with no model involved? |
| **3** | MCP server, read tools only | An external agent can retrieve usefully |
| **4** | Graph tables, entity resolution, MCP write tools, validation | **The loop closes here** |
| **5** | Frontier expansion, coverage scoring, scheduling, alerting | Now autonomous |
| **6** | Graph UI, node detail, annotation, admin surfaces | The payoff layer |
| **7** | Attribute audit, analogies, contradictions, temporal flags, enrichment, reports | Full design |

## Phase 2 is the real decision point

If searching your own corpus is not already valuable with no language model involved,
the later layers will not rescue it — and that is worth discovering in a weekend rather
than a month. Hold the checkpoint honestly.

## Two things that resist automation

Both are deliberate, not gaps:

- **Relevance judgment** — cheap for a human, hard to specify, and the best training
  signal the priority scorer will ever get.
- **Attribute semantics** — audits optimise for internal usefulness (does it
  discriminate, does it get used), not for whether an attribute still means what you
  intended. Reading the audit log monthly is enough to catch divergence.

## Before writing phase 0 code

Do the backwards pass from the architecture spec §14.3: write down the ten questions
most worth asking this system in a year, then check the schema can actually answer them.
It reliably surfaces a missing node type or edge attribute, far more cheaply than
discovering it in month four. The same exercise produces the held-out question set that
§14.1 uses as the only real regression test.
