<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/design/assets/meridian-lockup-dark-transparent.png">
    <img src="docs/design/assets/meridian-lockup-light-transparent.png" alt="Meridian" width="420">
  </picture>
</p>

<p align="center"><em>A line to measure everything else against.</em></p>

<p align="center">
  <a href="#license"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-0D6F7C"></a>
  <img alt="Version 0.26" src="https://img.shields.io/badge/version-0.26-0D6F7C">
  <img alt="Status: phase 1" src="https://img.shields.io/badge/status-phase%201%20of%207-805A28">
</p>

---

**Meridian is a self-hosted research system that reads the web so you can read the graph.**

It continuously crawls public sources on the topics you care about, extracts entities
and relationships into a knowledge graph, and records where every single claim came
from. You explore that graph, annotate it, and ask questions of it — and it tells you
not just what it knows, but where the evidence is thin, stale, or contradictory.

> **Status: phase 1 of 7.** The design, the shared core and the schema are in place,
> and the crawler runs: it drains a queue unattended, fetches politely, keeps what it
> fetched, reads it, chunks it, and widens its own frontier from both links and
> sitemaps — supervised, in containers, on a network topology that keeps the browser
> away from the database. Chunks carry embeddings, and a novelty gate marks the ones
> that duplicate something already collected. **Nothing searches them yet**, and
> that is the next thing: phase 2 asks whether searching this corpus is useful with no
> model involved, and it is a real go/no-go.
> See the [roadmap](docs/roadmap.md) and [TASKS.md](TASKS.md).

## Why it exists

Reading widely on a complex topic is slow, and the slowness isn't in the reading — it's
in the *finding*, the *relating*, and the *forgetting*. Meridian automates only those parts.

- **Primary goal:** learn a domain deeply, and see how its topics interconnect.
- **Secondary goal:** the accumulated graph eventually supports written output.

The organising principle: **autonomous acquisition, deliberately non-autonomous
consumption.** Everything mechanical — crawling, extraction, tagging, coverage scoring,
gap detection — runs unattended. The reading is not automated, because the reading *is*
the learning.

Three things make it different from a bookmarking tool or a RAG chatbot:

1. **Provenance on everything.** Every node, edge, and tag records the source chunk that
   justifies it. Nothing is assertable without a citation you can follow back to a file.
2. **Contradictions are signal, not error.** When sources disagree, both edges are kept
   and the pair is marked contested. Contested nodes are the highest-value nodes — they
   locate live debates.
3. **Absence is visible.** Coverage scoring shows topic × dimension cells where the
   evidence is thin or ageing, which is what drives the system's next crawl.

## How it works

Three decoupled planes sharing one Postgres database. No plane blocks another.

```
  INGESTION  ── 24/7, no LLM ──┐
  crawl · extract · embed      │
  novelty gate · frontier      ▼
                        ┌──────────────┐
                        │   POSTGRES   │        REASONING ── ~1h/day
                        │ queue·graph  │◄────── extract relations · tag
                        │   vectors    │        coverage · gap analysis
                        └──────┬───────┘
                               ▼
                          INTERFACE
                 search · graph · steering · export
```

The ingestion loop never calls a language model, so it keeps working whether or not any
model is reachable. Reasoning happens in one batched pass, which sees a whole day of
material at once and therefore produces better gap analysis than trickled inference.

| Layer | Choice |
|---|---|
| Queue, metadata, graph | Postgres + Apache AGE |
| Vectors | pgvector (HNSW) |
| Embeddings | bge-m3 (multilingual) |
| Entity extraction | spaCy + curated gazetteer |
| Backend | FastAPI — serves UI and MCP |
| Frontend | React + TypeScript + Tailwind, with Sigma.js v3 + graphology |
| Search | SearXNG, self-hosted, with a paid API fallback |
| Fetch and extract | Crawl4AI (version-pinned) |
| Document conversion | MarkItDown |
| Reasoning | Any frontier model over MCP, or a local OpenAI-compatible endpoint |
| Remote access | Cloudflare Tunnel — the only exposed service |

Rationale for each choice, and the alternatives rejected, are in the
[architecture spec](docs/spec/autonomous-research-system-spec.md) §4.

## Minimum requirements

Meridian is designed to run unattended on one modest always-on machine. It is not
picky about which one.

| | Minimum | Recommended |
|---|---|---|
| OS | 64-bit Linux (x86_64 or arm64) | same |
| CPU | 4 cores | 6+ cores |
| Memory | 8 GB | 16 GB or more |
| Storage | 100 GB SSD | 500 GB+ NVMe |
| Runtime | Docker + Compose v2 | same |
| Native dev extras | `poppler-utils` (PDF extraction) | + `ghostscript` to run the PDF tests |
| Network | Outbound HTTPS | + Cloudflare Tunnel for remote access |

Storage is the figure that grows: extracted text, embeddings, and the graph together
stay in the low single-digit gigabytes for a 50k-document corpus, but retained raw PDFs
and HTML can reach 50–150 GB. Retention is tiered and configurable.

**Optional:** a machine on your network running an OpenAI-compatible endpoint (llama.cpp,
Ollama, vLLM) for local inference, and/or an API key for a hosted model. Meridian routes
work between them by task type and cost.

## Getting started

```bash
git clone <this-repo> && cd meridian
cp .env.example .env.dev                          # fill in secrets — see comments inside

docker compose -f docker-compose.dev.yml up -d     # postgres, searxng, crawl4ai
make migrate                                      # schema
make seed                                         # config/*.yaml → database, once
```

Application services run natively during development for fast iteration:

```bash
uv run uvicorn api.main:app --reload --port 21114   # API + MCP
uv run python -m worker.main                        # ingestion loop
cd web && npm run dev                               # UI on :21115
```

Production runs the whole stack in containers behind `cloudflared`
(`docker compose up -d`), supervised by [`deploy/meridian.service`](deploy/meridian.service).
`make build-worker` builds the ingestion image locally. Full sequence:
[scaffold doc](docs/spec/meridian-project-scaffold.md) §6.

> `packages/meridian_core` and the worker are built and tested, so `make migrate`,
> `make seed`, `make test` and `python -m worker.main` all work today — the worker
> drains the queue, fetches politely, records every attempt, keeps what it fetched
> as a `sources` row plus (for primary sources) a local copy, extracts HTML, PDFs and
> Office documents to text, bibliographic metadata and citations, and cuts it into
> citable chunks — page-accurate for PDFs. Scanned PDFs are detected and filed for
> OCR rather than read, every page
> is screened for prompt injection on the way past. Two further passes run on demand
> rather than in the loop: `python -m worker.embed` gives chunks their vectors and
> `python -m worker.novelty` marks the near-duplicates among them. Nothing searches
> them yet (`P2-04`–`P2-06`). Frontier expansion is on through three channels: a
> fetched page's links, a site's own sitemaps, and search queries run against a
> self-hosted SearXNG — all filtered and queued, so the crawl keeps going past
> its seed list. The API and
> web commands are phase 2 and 3. See
> [docs/handover.md](docs/handover.md) for what runs today and what does not.
>
> The loop is configured entirely from the environment — `MERIDIAN_WORKER_CONCURRENCY`,
> `MERIDIAN_WORKER_TOPICS`, `MERIDIAN_WORKER_MAX_TASKS` and friends, all listed in
> `.env.example`. `MERIDIAN_WORKER_MAX_TASKS=5` gives a bounded run, which is the
> way to try it without leaving a crawler going. Set `MERIDIAN_RAW_ROOT` when
> running natively — it defaults to the container's `/data/raw` mount, and the
> raw store is where fetched primary sources land (spec §5.4).

Host ports sit in a distinctive `211xx` block — `21111` postgres, `21112` searxng,
`21113` crawl4ai, `21114` api, `21115` web — so a fresh clone doesn't collide with
whatever else is already bound to 5432 or 8080. Container-internal ports are
unchanged.

**Configuration lives in the database, not in files.** The YAML in `config/` seeds topic
weights, the attribute schema, fetch policy, the agent registry, and the gazetteer
exactly once at first boot. After that, change things in the Admin UI or over MCP —
editing the files has no effect.

## Repository layout

```
config/       first-boot seed values (YAML → DB, then the DB is authoritative)
packages/     meridian_core — shared models and schemas, imported by every service
services/     worker (ingestion) · orchestrator (synthesis) · api (Explore, Admin, MCP)
web/          Explore and Admin frontend
migrations/   alembic
docs/         specs, roadmap, design system and brand assets
```

## Documentation

| Document | What's in it |
|---|---|
| [Architecture spec](docs/spec/autonomous-research-system-spec.md) | The full design: data model, the two loops, agent integration, interface, evaluation. The "why." |
| [Project scaffold](docs/spec/meridian-project-scaffold.md) | Directory layout, container topology, database roles, build and deploy. |
| [Roadmap](docs/roadmap.md) | Build phases and their acceptance checkpoints. |
| [TASKS.md](TASKS.md) | The live build list — what's done, what's next, broken into single-sitting tasks. |
| [Handover](docs/handover.md) | How the built parts fit together, the traps already discovered, and what is verified live rather than only tested. |
| [Design system](docs/design/design-system.md) | Mark geometry, colour tokens, typography, voice, interaction rules. |
| [AGENTS.md](AGENTS.md) | Conventions and invariants for anyone writing code here, human or agent. |
| [CHANGELOG.md](CHANGELOG.md) | Version history. |

## FAQ

**Can I run it today?**
You can run the *ingestion* half. `make migrate && make seed` gives you a real, empty
database, and the worker will then crawl unattended — politely, storing what it fetches,
extracting and chunking it, and expanding its own frontier. What you cannot do is read
any of it back: there is no search, no API and no interface yet, so the corpus is only
reachable through SQL. The [roadmap](docs/roadmap.md) tracks progress; phase 2 is the
honest go/no-go.

**How is this different from Zotero, Obsidian, or a RAG chatbot?**
Those store or retrieve documents. Meridian builds a *typed graph of claims* with
provenance, tracks where sources contradict each other, and measures its own coverage so
it can tell you what it doesn't know. Retrieval is a feature of it, not the point of it.

**Does it need a language model running constantly?**
No. Ingestion runs 24/7 with no model at all. Reasoning is a batched pass of roughly an
hour a day, and if no model is reachable it simply defers — crawling continues regardless.

**Is my data sent anywhere?**
Only what you configure. Crawling reads public sources. During synthesis, source chunks
go to whichever model you point it at — which can be a local one on your own network, in
which case nothing leaves the building.

**What does it cost to run?**
Hardware you already own, plus optional model API usage. Token budgets, per-run seed caps,
and a monthly ceiling are enforced server-side, because the seed→crawl→synthesis loop
compounds if left uncapped.

**Can I use it for my own topics?**
Yes. Topics are configuration, not code — the set in `config/topics.yaml` is just the
author's. Topics can be added, weighted, paused, and archived at runtime.

**Why "Meridian"?**
A meridian is the reference line a position is measured against — and the sun's zenith,
its highest point. Both senses fit: a fixed line for relating scattered things, and the
clarity you're aiming at.

## Versioning

`MAJOR.MINOR.PATCH`, bumped on every change to application code. Documentation and design
changes don't require a bump. See [CHANGELOG.md](CHANGELOG.md) for history and
[AGENTS.md](AGENTS.md) for the rules on which number moves.

## Conduct

Public sources only. The crawler identifies itself honestly, respects `robots.txt` and
per-domain rate limits, and never routes around paywalls. Retained raw files are for
personal research — cite them, don't redistribute them.

## License

[MIT](LICENSE) © 2026 [renj1ete0](https://github.com/renj1ete0)
