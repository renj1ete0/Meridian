# Roadmap

Build phases and their acceptance checkpoints. This is the *shape* of the build;
[TASKS.md](../TASKS.md) at the repo root holds the live task list and current state.

**The system is useless until the loop closes.** Build thin end-to-end first — a
narrow slice that runs from crawl to graph to interface — rather than finishing any
one layer. Each phase should run unattended before the next begins.

| Phase | Deliverable | Done when | State |
|---|---|---|---|
| **0** | `meridian_core` models, migrations, seed script, cold-start config | `make migrate && make seed` yields a clean, **empty** database ready to crawl — config only, no content | closed |
| **1** | Queue, fetcher, extractor, raw retention | Runs 48h unattended without failing. The result becomes the first development corpus | built; **the 48h run has not happened** |
| **2** | Embeddings, novelty gate, hybrid search, minimal Explore | **Go/no-go** — is searching the corpus already useful with no model involved? | built; the checkpoint waits on phase 1's run |
| **3** | MCP server, read tools only | An external agent can retrieve usefully | built; remote access waits on a Cloudflare account |
| **4** | Graph tables, entity resolution, MCP write tools, validation | **The loop closes here** | spine built — store, resolution, write tools, routing, run state, model client. **No run has written an edge**: `P4-16`'s prompt and parse are what close it |
| **5** | Frontier expansion, coverage scoring, scheduling, alerting | Now autonomous | scheduler, alerting, the gazetteer and the Telegram control surface built; coverage needs edges to exist |
| **6** | Graph UI, node detail, annotation, admin surfaces | The payoff layer | everything that does not need the graph is built |
| **7** | Attribute audit, analogies, contradictions, temporal flags, enrichment, reports | Full design | not started |

The phases are not being finished in order, deliberately — §"build thin end-to-end
first" above. What that means in practice is that a phase marked built can still be
waiting on a checkpoint from an earlier one, and phase 1's 48-hour run is the single
thing most of the outstanding checkpoints are waiting for.

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

## The backwards pass, and why it is still owed

Before phase 0, the architecture spec §14.3 asks for a backwards pass: write down the
ten questions most worth asking this system in a year, then check the schema can
actually answer them. It was done once, and it earned its keep — `observations` exists
because of it, after the schema turned out to have no way at all to answer "what is the
market share of X by deployment?".

The half that is still owed is the other output: §14.1's held-out question set, which is
the only real regression test this system will ever have. Phase 2's go/no-go is
supposed to be judged against it, and it does not exist yet.
