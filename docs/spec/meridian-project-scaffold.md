# Meridian — Project Scaffold & Dockerisation

Companion to the architecture spec. This document is written to be consumed by a coding agent: it defines directory layout, service boundaries, container topology, and build order.

**Read the architecture spec first.** Section references (§n) below point to it.

---

## 1. Structural principles

1. **Monorepo, service per directory.** Three Python services plus a frontend, sharing one core package.
2. **One shared package for anything touching the database.** Worker, orchestrator and API all read the same tables; without a shared package you get three drifting copies of the schema.
3. **Single writer per concern** (§4). Worker owns ingestion tables; orchestrator owns graph writes; API is read-only except through `/admin` endpoints.
4. **Data lives on host bind mounts, never in container layers.** Containers are disposable; `/srv/meridian/` is not.
5. **Only `cloudflared` is exposed.** Postgres, SearXNG, Crawl4AI and the API bind to the internal network or loopback.
6. **Config is seeded from YAML at first boot, then the DB is authoritative** (§13.1). YAML files are not read thereafter.
7. **Production starts empty.** Seeding loads configuration only. Development corpora are snapshots of real crawls, never synthetic fixtures, and never loadable in production.

---

## 2. Directory layout

```
meridian/
├── README.md
├── AGENTS.md                    # build conventions for coding agents
├── Makefile                     # common tasks: up, down, migrate, seed, logs
├── docker-compose.yml           # production (Pi)
├── docker-compose.dev.yml       # local dev overrides
├── .env.example
├── .dockerignore
│
├── config/                      # first-boot seed values only
│   ├── fetch_policy.yaml        # §6.4
│   ├── topics.yaml              # topic labels + initial weights, §10
│   ├── attributes.yaml          # attribute schema, global + topic-local, §7.1
│   ├── gazetteer_seed.yaml      # ~50 hand-seeded terms, §5.6
│   ├── seed_sources.yaml        # 15–25 cold-start sources, §15 phase 0
│   ├── source_tiers.yaml        # domain → tier mapping, §5.2
│   ├── agents.yaml              # agent registry defaults, §11.3
│   └── searxng/
│       └── settings.yml
│
├── packages/
│   └── meridian_core/
│       ├── pyproject.toml
│       └── meridian_core/
│           ├── __init__.py
│           ├── db.py            # engine, session, connection pooling
│           ├── models/          # SQLAlchemy models, one file per concern
│           │   ├── queue.py
│           │   ├── source.py    # sources, chunks, figures
│           │   ├── graph.py     # entities, edges, attributes
│           │   ├── gazetteer.py
│           │   ├── config.py    # topic_config, fetch_policy, agents
│           │   └── runs.py      # runs, steering_log, enrichment_queue
│           ├── schemas/         # pydantic DTOs shared across services
│           ├── config.py        # layered config resolution (§6.4)
│           ├── embeddings.py    # bge-m3 wrapper
│           ├── novelty.py       # cosine gate
│           ├── provenance.py    # tag/edge provenance helpers (§11.12)
│           └── logging.py       # structured logging setup
│
├── services/
│   ├── worker/                  # fast loop — 24/7, no LLM (§6.1)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── worker/
│   │       ├── main.py          # queue loop
│   │       ├── fetch.py         # crawl4ai + httpx, policy resolution
│   │       ├── extract/
│   │       │   ├── html.py      # crawl4ai markdown
│   │       │   ├── document.py  # markitdown (convert_local/stream ONLY)
│   │       │   ├── pdf.py       # native-text detection, page offsets
│   │       │   └── figures.py   # figure extraction + captions
│   │       ├── frontier.py      # links, citations, spaCy NER, TF-IDF
│   │       ├── prefilter.py     # domain blocklist, already-seen
│   │       ├── resolve_doi.py   # unpaywall → openalex → core → preprint
│   │       └── ocr_queue.py     # enqueue only; never inline (§6.6)
│   │
│   ├── orchestrator/            # slow loop — synthesis (§6.2, §11)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── orchestrator/
│   │       ├── main.py          # scheduler + run state machine
│   │       ├── registry.py      # agent selection, routing, fallback
│   │       ├── providers/
│   │       │   ├── base.py      # OpenAI-compatible client
│   │       │   ├── local.py     # llama.cpp health poll + WoL (§11.7)
│   │       │   └── hosted.py
│   │       ├── tasks/
│   │       │   ├── extract_relations.py
│   │       │   ├── tag_attributes.py
│   │       │   ├── coverage.py
│   │       │   ├── analogies.py
│   │       │   ├── gap_analysis.py
│   │       │   ├── draft_report.py  # §11.13, user-triggered drafting jobs
│   │       │   └── reprocess.py     # §11.12
│   │       ├── enrichment.py    # VLM/OCR batches, user-triggered (§6.6)
│   │       ├── validation.py    # server-side write guards (§11.4)
│   │       ├── budget.py        # token/seed caps, cost logging (§11.9)
│   │       └── telegram/        # digest, alerts, commands (§13.3)
│   │
│   └── api/                     # FastAPI — Explore, Admin, MCP (§12.6)
│       ├── Dockerfile
│       ├── pyproject.toml
│       └── api/
│           ├── main.py
│           ├── deps.py          # read-only vs read-write session factory
│           ├── routes/
│           │   ├── explore.py   # /api/explore/*  — read-only role
│           │   └── admin.py     # /api/admin/*    — read-write role
│           ├── mcp/
│           │   ├── server.py
│           │   ├── read_tools.py
│           │   ├── write_tools.py   # scoped tokens only (§11.4)
│           │   └── ops_tools.py     # §13.2
│           ├── search.py        # hybrid: pgvector + tsvector, RRF fusion
│           └── graph.py         # traversal queries, recursive CTEs
│
├── web/                         # Explore + Admin frontend
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── explore/             # canvas, search, node detail, chat, figures
│       ├── admin/               # weights, registry, runs, fetch policy
│       └── lib/
│           ├── graph.ts         # sigma.js + graphology
│           └── api.ts
│
├── migrations/                  # alembic
│   ├── env.py
│   └── versions/
│
├── scripts/
│   ├── seed.py                  # config/*.yaml → DB, idempotent
│   ├── backup.sh                # pg_dump + raw store snapshot → off-device
│   ├── healthcheck.py
│   └── build_and_push.sh        # cross-build arm64 → GHCR
│
└── tests/
    ├── unit/
    ├── integration/             # against ephemeral postgres
    └── fixtures/
```

**Not in the repo:** `/srv/meridian/{pgdata,raw,figures,logs}` on the Pi's NVMe. Bind-mounted, backed up, never in an image.

---

## 3. Container topology

Eight services. Only `cloudflared` reaches the internet inbound.

```
                     ┌─────────────┐
        internet ────┤ cloudflared │  (only exposed service)
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐        ┌─────────┐
                     │     api     │◄───────┤   web   │
                     └──┬───────┬──┘        └─────────┘
                        │       │
         ┌──────────────▼─┐   ┌─▼──────────────┐
         │   postgres     │◄──┤  orchestrator  │──► LAN: llama.cpp
         │ (pgvector)     │   └────────────────┘    WAN: hosted API
         └────────▲───────┘
                  │
         ┌────────┴───────┐
         │     worker     │───► searxng   (internal net)
         └────────────────┘───► crawl4ai  (internal net, loopback-only API)
```

### docker-compose.yml

```yaml
name: meridian

x-common: &common
  restart: unless-stopped
  env_file: .env
  networks: [internal]
  logging:
    driver: json-file
    options: { max-size: "10m", max-file: "3" }

services:
  postgres:
    <<: *common
    image: pgvector/pgvector:pg17          # pin by digest in production
    volumes:
      - /srv/meridian/pgdata:/var/lib/postgresql/data
      - ./scripts/init-roles.sql:/docker-entrypoint-initdb.d/10-roles.sql:ro
    environment:
      POSTGRES_DB: meridian
      POSTGRES_USER: ${PG_USER}
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    command: >
      postgres
      -c shared_buffers=2GB
      -c work_mem=32MB
      -c max_connections=40
      -c synchronous_commit=on
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${PG_USER} -d meridian"]
      interval: 10s
      retries: 5

  searxng:
    <<: *common
    image: searxng/searxng:latest          # pin
    volumes:
      - ./config/searxng:/etc/searxng:ro
    environment:
      SEARXNG_BASE_URL: http://searxng:8080/

  crawl4ai:
    <<: *common
    image: unclecode/crawl4ai:0.9.2        # PIN — see §6.4 security note
    shm_size: 1g
    environment:
      CRAWL4AI_API_TOKEN: ${CRAWL4AI_TOKEN}
      CRAWL4AI_HOOKS_ENABLED: "false"
    # NO ports: — internal network only, never exposed

  worker:
    <<: *common
    build: { context: ., dockerfile: services/worker/Dockerfile }
    depends_on:
      postgres: { condition: service_healthy }
    volumes:
      - /srv/meridian/raw:/data/raw
      - /srv/meridian/figures:/data/figures
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    deploy:
      resources:
        limits: { memory: 4G }

  orchestrator:
    <<: *common
    build: { context: ., dockerfile: services/orchestrator/Dockerfile }
    depends_on:
      postgres: { condition: service_healthy }
    volumes:
      - /srv/meridian/raw:/data/raw:ro
    network_mode: bridge          # needs LAN access to llama.cpp + WoL
    networks: [internal, lan]
    cap_add: [NET_RAW]            # wake-on-LAN magic packets

  api:
    <<: *common
    build: { context: ., dockerfile: services/api/Dockerfile }
    depends_on:
      postgres: { condition: service_healthy }
    volumes:
      - /srv/meridian/raw:/data/raw:ro
      - /srv/meridian/figures:/data/figures:ro
    ports: ["127.0.0.1:8000:8000"]   # loopback only; cloudflared fronts it

  web:
    <<: *common
    build: { context: ./web }
    depends_on: [api]

  cloudflared:
    <<: *common
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN}

networks:
  internal:
    driver: bridge
    internal: true          # no outbound except services that also join lan
  lan:
    driver: bridge
```

> The `internal: true` flag blocks outbound internet from that network. `worker` needs outbound for fetching, so in practice it joins a second non-internal network — adjust when implementing rather than copying literally.

### Notes on specific services

**`crawl4ai`** — pin the version. Its Docker API had critical vulnerabilities through v0.8.7 and became secure-by-default only in v0.9.0 (§6.4). No `ports:` mapping under any circumstances.

**`worker`** — processes untrusted web content. Read-only root filesystem, dropped capabilities, no credentials in its environment beyond the DB URL.

**`orchestrator`** — the only service holding model API keys. Never reachable from the tunnel. Needs LAN access for llama.cpp and `NET_RAW` for Wake-on-LAN.

**`postgres`** — `shared_buffers=2GB` assumes ~4–6GB is available after the Pi's other workloads. Tune against actual free memory; this is the single most impactful setting.

**No local LLM container.** llama.cpp runs natively on the Fedora box (§11.7), reached over the LAN.

---

## 4. Database roles

`scripts/init-roles.sh`, run at first boot:

```bash
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE ROLE meridian_rw LOGIN PASSWORD '${PG_RW_PASSWORD}';
	CREATE ROLE meridian_ro LOGIN PASSWORD '${PG_RO_PASSWORD}';

	GRANT USAGE ON SCHEMA public TO meridian_rw, meridian_ro;
	GRANT CREATE ON SCHEMA public TO meridian_rw;

	ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
	  GRANT ALL ON TABLES TO meridian_rw;
	ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
	  GRANT SELECT ON TABLES TO meridian_ro;

	CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
```

**It must be a shell script, not plain `.sql`.** The Postgres entrypoint runs
`.sql` files through psql with no variable bindings, so `:'rw_password'` is a
syntax error and the container dies mid-init. Passwords arrive as environment
variables (`PG_RW_PASSWORD`, `PG_RO_PASSWORD`).

`ALTER DEFAULT PRIVILEGES` is the line that matters — at first boot no tables
exist yet, so the grants that count are the ones applied to tables Alembic
creates later. It is scoped `FOR ROLE $POSTGRES_USER` because default privileges
only cover objects created by the role that declared them, and migrations run as
that user.

- `worker`, `orchestrator` → `meridian_rw`
- `api` explore routes and `run_readonly_query` → `meridian_ro`
- `api` admin routes → `meridian_rw`

Enforcing read-only at the database, not in application code, is what makes §12.4's escape hatch safe.

---

## 5. Build and deploy

**Do not build on the Pi.** Compiling spaCy, torch and Playwright dependencies on an RK3588 is slow enough to derail iteration.

```bash
# on the Pi
docker compose pull && docker compose up -d
```

See *Architecture* below for the build command. Pin base images by digest.

#### Registry

Four application images live in GHCR:

```
ghcr.io/renj1ete0/meridian-{worker,orchestrator,api,web}:<sha>
```

Postgres, SearXNG, Crawl4AI and cloudflared come from upstream registries.

**Keep packages private.** Nothing secret should be baked into an image, but they will contain extraction prompts and validation logic — no reason to publish those. Private means the Pi must authenticate:

```bash
# on the Pi — read-only token
echo $GHCR_READ_PAT | docker login ghcr.io -u renj1ete0 --password-stdin
```

Two separate PATs: `write:packages` on the Fedora box for pushing, `read:packages` on the Pi. The Pi never pushes, so the write token never goes there.

**Tag by commit SHA, not `latest`.** Pinning the SHA in `docker-compose.yml` means a bad build does not roll out on the next restart, and rollback is a one-line edit. `latest` in an unattended system removes exactly the control you want. **No Watchtower or auto-update** — unattended auto-updates on a box you are not watching is how a breaking change is discovered three days late.

### Architecture: arm64 production, x86_64 development

**Third-party images need no handling.** Postgres/pgvector, SearXNG, Crawl4AI and cloudflared all publish multi-arch manifests; Docker selects by host architecture automatically.

**Do not set `platform:` in compose.** It forces emulation and is almost always a mistake. The only legitimate use is deliberately testing the non-native build.

**Application images: one multi-arch manifest, one tag.**

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f services/worker/Dockerfile \
  -t ghcr.io/renj1ete0/meridian-worker:$(git rev-parse --short HEAD) \
  --push .
```

`docker compose pull` then resolves correctly on both machines with no tag juggling.

**In development this rarely matters** — application services run natively via `uv` and `npm` (§6), so only the multi-arch infra containers run locally.

#### Three friction points

1. **Python wheels.** spaCy, lxml and torch ship arm64 wheels, but any dependency without one falls back to a source build. Under QEMU this is slow; on the RK3588 it is worse. This is the main reason to cross-build on the Fedora box rather than building on the Pi.
2. **Chromium on arm64.** Playwright supports it, but Crawl4AI v0.9.2 shipped a fix for Playwright headless-shell packaging — evidence the path is less trodden. **Smoke-test rendering on the actual Pi**, not under emulation.
3. **Base images.** Use known multi-arch bases (`python:3.12-slim-bookworm`, `node:22-slim`). Verify before pinning by digest — digests are architecture-specific, so pin the *manifest list* digest, not a platform-specific one.

#### Rule

**Build on x86, verify on arm64, never develop under emulation.** QEMU is for confirming an image builds and starts. It is too slow for iteration.

#### Path portability

The data root differs between machines (`/srv/meridian` on the Pi, `./.devdata` locally). Parameterise it rather than hardcoding:

```yaml
volumes:
  - ${DATA_ROOT:-/srv/meridian}/pgdata:/var/lib/postgresql/data
```

### Supervision

Use Docker's `restart: unless-stopped` **or** systemd, not both. If using compose, let systemd manage only the stack itself:

```ini
# /etc/systemd/system/meridian.service
[Unit]
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/meridian/app
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
```

### Makefile targets

```
make dev-up / dev-down    # infra containers only (local development)
make up / down / logs     # full stack (Pi)
make migrate              # alembic upgrade head
make seed                 # config/*.yaml → DB (idempotent)
make snapshot-corpus      # dump current DB → dev fixture (dev only)
make restore-corpus       # load dev fixture (dev only)
make backup               # pg_dump + raw store → off-device
make test
make build-push           # cross-build arm64 → GHCR
```

---

## 6. Local development

Develop on the Fedora box (x86, ample RAM, llama.cpp already present), not the Pi.

### Service map

| Layer | Directory | Surface |
|---|---|---|
| Frontend | `web/` | React + TypeScript + Tailwind on Vite, port 5173 |
| Backend — API | `services/api/` | FastAPI, port 8000. Serves Explore, Admin, MCP |
| Backend — ingestion | `services/worker/` | Headless daemon; no HTTP |
| Backend — synthesis | `services/orchestrator/` | Headless; scheduled, or `--once` |

Only `api` has an HTTP surface. Worker and orchestrator are background processes that communicate through Postgres.

### Pattern: infra in Docker, application code native

Containerising the code you are actively editing kills iteration speed. Run the stateful dependencies in containers and the Python/Node services on the host.

```yaml
# docker-compose.dev.yml — infra only
name: meridian-dev

services:
  postgres:
    image: pgvector/pgvector:pg17
    ports: ["5432:5432"]              # exposed for host-native services
    environment:
      POSTGRES_DB: meridian
      POSTGRES_USER: meridian
      POSTGRES_PASSWORD: dev
    volumes:
      - ./.devdata/pgdata:/var/lib/postgresql/data
      - ./scripts/init-roles.sql:/docker-entrypoint-initdb.d/10-roles.sql:ro

  searxng:
    image: searxng/searxng:latest
    ports: ["8080:8080"]
    volumes: ["./config/searxng:/etc/searxng:ro"]

  crawl4ai:
    image: unclecode/crawl4ai:0.9.2
    ports: ["11235:11235"]
    shm_size: 1g
    environment:
      CRAWL4AI_HOOKS_ENABLED: "false"
```

No `cloudflared`, no `web`, no application services — those run natively.

### Startup

```bash
cp .env.example .env.dev          # point PG_* at localhost

docker compose -f docker-compose.dev.yml up -d
make migrate
make seed                          # config/*.yaml → DB

# terminal 1 — API (hot reload)
uv run uvicorn api.main:app --reload --port 8000

# terminal 2 — ingestion worker
uv run python -m worker.main

# terminal 3 — frontend
cd web && npm run dev              # → http://localhost:5173
```

The orchestrator is **not** run continuously in development:

```bash
uv run python -m orchestrator.main --once --dry-run   # plan only, no writes
uv run python -m orchestrator.main --once             # real synthesis pass
```

`--dry-run` is worth building early: it prints the tool calls the model would make without applying them, which is how you debug extraction and validation without burning tokens or corrupting the graph.

### Frontend proxy

```js
// web/vite.config.ts
server: {
  proxy: { '/api': 'http://localhost:8000' }
}
```

So the frontend calls `/api/explore/...` in both dev and production, with no environment-specific base URL.

### Local model

llama.cpp already runs on this machine. Point the dev agent registry at it:

```
LOCAL_LLM_URL=http://localhost:8080/v1
LOCAL_LLM_HEALTH_URL=http://localhost:8080/health
```

**Run a separate llama.cpp process on its own port for Meridian** (§11.7) rather than sharing the one serving editor tooling — it avoids slot contention and makes request hangs attributable.

### Development corpus — real, not synthetic

**Production always starts empty.** `make seed` loads configuration only — topic weights, attribute schema, gazetteer terms, seed source URLs. No content, no entities, no edges. This is structural, not a convention: there is no fixture-loading path in the production Makefile.

For development, use a **snapshot of a real early crawl** rather than fabricated data. Synthetic fixtures do not resemble actual output — entity messiness, extraction failures, tier distributions and edge density all differ — so UI built against them gets rebuilt later.

```
make snapshot-corpus     # dump current DB → fixtures/corpus_YYYYMMDD.dump
make restore-corpus      # load into the local dev DB
```

The Phase 1 validation run (48h unattended) produces the first such corpus: the acceptance test and the fixture generation are the same activity. Regenerate whenever it drifts from current schema or extraction behaviour.

Keep the dump **out of git** — store it beside the backups on the NVMe with a checksum.

Also worth keeping, and small enough to commit:

- `tests/fixtures/pages/` — a handful of saved HTML, native PDF and scanned PDF samples covering each extraction path. These are unit-test inputs, not a corpus.

### Running against Pi data

To debug production state locally, restore a snapshot rather than pointing at the live database:

```bash
make backup                                    # on the Pi
pg_restore -d meridian_dev meridian_YYYYMMDD.dump
```

Never point local development at the Pi's Postgres — the single-writer discipline (§4) assumes one worker.

---

## 7. Environment

`.env.example`:

```
DATA_ROOT=/srv/meridian

PG_USER=meridian
PG_PASSWORD=
PG_RW_URL=postgresql://meridian_rw:...@postgres:5432/meridian
PG_RO_URL=postgresql://meridian_ro:...@postgres:5432/meridian

CRAWL4AI_TOKEN=
CLOUDFLARE_TUNNEL_TOKEN=
GHCR_READ_PAT=

# orchestrator only
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
LOCAL_LLM_URL=http://onelaptop.local:8080/v1
LOCAL_LLM_HEALTH_URL=http://onelaptop.local:8080/health
LOCAL_LLM_MAC=

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

**Credentials never go in the database** (§11.11) — it is snapshotted off-device for backup, and keys would travel with every snapshot. The agent registry stores the *name* of the env var to read, not its value.

---

## 8. Build order

Matches §15. Each phase should run unattended before starting the next.

| Phase | Deliverable | Done when |
|---|---|---|
| **0** | `meridian_core` models + migrations + `scripts/seed.py` | `make migrate && make seed` yields a clean, **empty** DB ready to crawl — config only, no content |
| **1** | `worker`: queue, fetch, extract, raw retention | Runs 48h unattended without failing. **The result becomes the first dev corpus** (`make snapshot-corpus`) |
| **2** | Embeddings, novelty gate, `api` search + minimal Explore | **Go/no-go — is searching the corpus already useful?** |
| **3** | `api` MCP read tools | An external agent can retrieve usefully |
| **4** | Graph tables, MCP write tools, `orchestrator` validation | The loop closes |
| **5** | Frontier expansion, coverage scoring, `runs` table | Autonomous |
| **6** | Graph UI, annotation, figures | The payoff layer |
| **7** | Attribute audit, analogies, contradictions, temporal, enrichment | Full design |

Phase 2 is the honest checkpoint. If searching the corpus is not already valuable with no LLM involved, later layers will not rescue it.

---

## 9. AGENTS.md

Place at repo root so coding agents pick up conventions:

```markdown
# Meridian — build conventions

## Invariants (do not violate)
- The worker NEVER calls an LLM. Ingestion must run with every model offline.
- All writes validate server-side. Never trust model output for structure.
- Every edge, tag and attribute carries provenance: source chunk, producing
  agent, model, quality_tier, schema_version.
- Re-derive from source chunks, never from prior model output.
- MarkItDown: use convert_local() or convert_stream() only. Never convert()
  on a URL.
- Credentials come from the environment, never from the database.

## Layout
- Anything touching the database lives in packages/meridian_core.
- Services import from meridian_core; they never define their own models.
- Explore routes use the read-only session; admin routes use read-write.

## Style
- Type hints throughout; pydantic for all boundaries.
- Alembic for every schema change — no manual DDL.
- Structured logging; every run logs run_id.
- No `platform:` keys in compose; images are multi-arch.
- Tests: unit for logic, integration against ephemeral postgres.

## When unsure
Check the architecture spec section referenced in the module docstring.
Ask rather than inventing schema.
```

---

## 10. Deliberate omissions

| Omitted | Why |
|---|---|
| Auth service | Cloudflare Access is the auth until roles are needed (§12.6). Route prefixes are already split so it becomes middleware |
| Message broker | The Postgres queue is sufficient at this scale; Redis would be a third store to back up |
| Separate vector DB | pgvector is adequate to ~1M vectors; Qdrant is the documented migration path |
| Local LLM container | Runs natively on the Fedora box; containerising GPU inference adds pain for no benefit |
| Kubernetes | One node |
