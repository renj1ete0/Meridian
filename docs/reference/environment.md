# Environment variables

Every variable the code reads, grouped by what it configures. Defaults are the code's own.
`.env.example` is the operator-facing template and says which keys a deployment needs.
Credentials always come from the environment and never from the database (§11.11).

A test (`tests/unit/test_compose_topology.py`) fails if compose sets a variable that nothing
reads. When you add a variable, add it here and to `.env.example`.

## Database

| Variable | Default | Meaning |
|---|---|---|
| `PG_RW_URL` | — (required) | Read-write role: worker, orchestrator, `/api/admin/*` |
| `PG_RO_URL` | — (required by the API) | Read-only role: `/api/explore/*`, MCP tools |
| `PG_MIGRATION_URL` | — (required by `make migrate`) | The database owner, which migrations run as (`migrations/env.py`); never the read-write role |
| `PG_GUEST_URL` | unset | Guest role for shared read access (`P3-07`); unset disables guests |
| `MERIDIAN_DB_POOL_SIZE` | 5 | Connections per engine |
| `MERIDIAN_DB_MAX_OVERFLOW` | 5 | Extra connections under load |
| `MERIDIAN_DB_POOL_TIMEOUT` | 30 | Seconds to wait for a pooled connection |
| `MERIDIAN_DB_POOL_RECYCLE` | 1800 | Seconds before a connection is replaced |
| `MERIDIAN_DB_ECHO` | off | Log every SQL statement (debugging only) |
| `PG_SHARED_BUFFERS`, `PG_EFFECTIVE_CACHE_SIZE`, `PG_MAINTENANCE_WORK_MEM`, `PG_WORK_MEM`, `PG_RANDOM_PAGE_COST` | 2GB, 6GB, 512MB, 32MB, 1.1 | Postgres server settings, read by compose (`B-132`); see `.env.example` for sizing |
| `PG_SHM_SIZE` | 1g | Postgres's shared memory, read by compose (`B-144`); must stay above `PG_MAINTENANCE_WORK_MEM` for parallel index builds |

## Crawl worker

| Variable | Default | Meaning |
|---|---|---|
| `MERIDIAN_WORKER_CONCURRENCY` | 4 | Fetch lanes per worker process |
| `MERIDIAN_WORKER_ID` | host-derived | Name recorded on claims |
| `MERIDIAN_WORKER_TOPICS` | all | Comma-separated topics this worker claims for |
| `MERIDIAN_WORKER_MAX_TASKS` | 0 (no limit) | Stop after this many tasks (bounded runs) |
| `MERIDIAN_WORKER_IDLE_SLEEP_S` | 5 | Sleep when the queue is empty |
| `MERIDIAN_WORKER_LEASE_SECONDS` | 900 | How long a claim holds before it expires |
| `MERIDIAN_WORKER_HOUSEKEEPING_S` | 3600 | Interval for in-loop housekeeping (host scores, pruning) |
| `MERIDIAN_WORKER_DIRECTED_EVERY` | 2 | One claim in N is reserved for directed work (search results, seeds, cited papers) |
| `MERIDIAN_WORKER_MAX_EMBED_BACKLOG` | 20000 | Pause fetching while this many valuable passages wait for a vector |
| `MERIDIAN_ATTEMPT_RETENTION_DAYS` | 30 | How long fetch-attempt rows are kept |
| `MERIDIAN_LIVENESS_PATH` | under `/tmp` | Heartbeat file the healthcheck reads |
| `MERIDIAN_RAW_ROOT` | `/data/raw` | Where raw files are stored |
| `MERIDIAN_CONTACT_EMAIL` | unset | Contact address sent to APIs that ask for one (Unpaywall, Wikimedia) |

## Search, browser and paper resolution

| Variable | Default | Meaning |
|---|---|---|
| `SEARXNG_URL` | unset | The SearXNG instance; unset disables search |
| `MERIDIAN_SEARCH_TIMEOUT_S` | 30 | Per-query timeout |
| `MERIDIAN_SEARCH_MAX_RESULTS` | 50 | Results taken per query |
| `MERIDIAN_SEARCH_MIN_INTERVAL_S` | 15 | Minimum spacing between queries, so engines do not throttle us |
| `CRAWL4AI_URL`, `CRAWL4AI_TOKEN` | unset | The browser service for pages that need JavaScript |
| `CORE_API_KEY` | unset | CORE paper lookups; skipped without it |
| `SEMANTIC_SCHOLAR_API_KEY` | unset | Higher rate limits for Semantic Scholar |
| `MERIDIAN_DOI_TIMEOUT_S` | 20 | Per-provider timeout when resolving a DOI |

## Embedding

| Variable | Default | Meaning |
|---|---|---|
| `MERIDIAN_EMBEDDER_URL` | unset | The embedding service; unset means lexical-only search |
| `MERIDIAN_EMBEDDER_TOKEN` | unset | Shared secret when the service is on another machine |
| `MERIDIAN_EMBEDDER_TIMEOUT_S` | 30 | Query timeout (batch calls scale it by size) |
| `MERIDIAN_EMBEDDER_HOST`, `MERIDIAN_EMBEDDER_PORT` | `0.0.0.0`, 8100 | Where the service listens |
| `MERIDIAN_EMBEDDER_WAIT_S` | 180 | How long the backfill waits for the service at start-up |
| `MERIDIAN_EMBED_MODEL` | `BAAI/bge-m3` | Model name; client and service must agree |
| `MERIDIAN_EMBED_DEVICE` | auto | `cpu`, `cuda`, `cuda:N`, `mps` |
| `MERIDIAN_EMBED_DTYPE` | by hardware | `float32` or `bfloat16` |
| `MERIDIAN_EMBED_BATCH_SIZE` | by hardware | Passages per model call; overrides automatic sizing |
| `MERIDIAN_EMBED_MAX_TOKENS` | 1024 | Truncation length |
| `MERIDIAN_EMBED_CACHE` | library default | Where model weights are cached |
| `MERIDIAN_EMBED_CHUNK_BATCH` | 256 | Passages the backfill draws per batch (split into requests of 256) |
| `MERIDIAN_EMBED_IDLE_SLEEP_S` | 60 | Backfill sleep when nothing waits |
| `MERIDIAN_EMBED_REMOTE_ONLY` | off | Never load the model in the backfill process |
| `HF_HUB_OFFLINE` | set in compose | Stops the model library probing the network from isolated containers |

## Judgement passes

| Variable | Default | Meaning |
|---|---|---|
| `MERIDIAN_NOVELTY_THRESHOLD` | 0.95 | Cosine above which a passage is a near-duplicate |
| `MERIDIAN_NOVELTY_SOURCE_FRACTION` | 0.9 | Share of a source's passages that must be duplicates for the source to be demoted |
| `MERIDIAN_NOVELTY_BATCH` | 256 | Passages per novelty batch |
| `MERIDIAN_SPACY_MODEL` | unset | spaCy model for the gazetteer ruler (optional extra) |

## Models, synthesis and the Ask panel

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Named by the hosted agent rows |
| `LOCAL_LLM_URL` | unset | Endpoint of the local OpenAI-compatible row |
| `LOCAL_CHAT_LLM_URL`, `LOCAL_CHAT_MODEL` | unset | Endpoint and model of the Ask panel's local row |
| `HOSTED_LLM_URL`, `HOSTED_LLM_MODEL`, `HOSTED_LLM_API_KEY` | unset | The hosted OpenAI-compatible row (DeepSeek, OpenRouter, …): endpoint ending in `/v1`, model name, key |
| `MERIDIAN_RELAY_DIR` | unset | Folder where relay prompts and answers are exchanged |
| `MERIDIAN_CHAT_DAILY_TOKENS` | 200000 | Daily token cap for the Ask panel (UTC day) |
| `MERIDIAN_MODEL_EFFORT` | `high` | Reasoning effort sent to Anthropic models: `low`, `medium`, `high`, `xhigh`, `max` (ADR 0004) |
| `MERIDIAN_SYNTHESIS_ON_TOPIC_ONLY` | off | Synthesis reads only passages labelled on a topic |

Which variable a model row reads is set on the row (`api_key_env_var`, `${VAR}` in `endpoint`
or `model`); see [features/synthesis.md](../features/synthesis.md).

## API, access and MCP

| Variable | Default | Meaning |
|---|---|---|
| `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` | unset | Verify Cloudflare Access assertions; unset disables the middleware |
| `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` | off | Allow `/api/admin/*` without verified identity (local use only) |
| `MERIDIAN_MCP_ALLOW_ANONYMOUS` | off | Allow MCP tools without a token (local use only) |
| `MERIDIAN_MCP_ISSUER_URL`, `MERIDIAN_MCP_RESOURCE_URL` | `http://localhost`, `…/mcp` | Name this server in OAuth metadata; set to the public URL behind the tunnel. Tokens verify either way (`B-138`) |
| `WEB_BIND`, `WEB_PORT` | `127.0.0.1`, 8080 | Where `deploy/lan/publish-web.yml` publishes the web front door (and `/mcp`) |
| `MERIDIAN_MCP_ALLOWED_HOSTS`, `MERIDIAN_MCP_ALLOWED_ORIGINS` | unset | DNS-rebinding protection lists for the MCP transport |
| `MERIDIAN_SERVE_RAW` | off | Serve stored raw files to readers (redistribution: a decision, not a default) |
| `MERIDIAN_VERSION` | from the image | Reported on `/health` |
| `MERIDIAN_EVAL_RUNS_DIR` | `eval/runs` | Where Gaps reads question-set runs |

## Operations

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | unset | Digest, alerts and inbound commands; unset keeps them in-app only |
| `MERIDIAN_ALERT_COOLDOWN_H` | 6 | Hours an alert stays quiet after firing |
| `MERIDIAN_SCHEDULER_ID` | host-derived | Name recorded on scheduled-job claims |
| `MERIDIAN_LOG_LEVEL` | `INFO` | Log level for every service |
| `CLOUDFLARE_TUNNEL_TOKEN` | unset | The public tunnel (compose) |

## Compose only

Read by the compose files, not by the code. `.env.example` says which a deployment sets.

| Variable | Default | Meaning |
|---|---|---|
| `PG_USER`, `PG_PASSWORD`, `PG_RW_PASSWORD`, `PG_RO_PASSWORD`, `PG_GUEST_PASSWORD` | — | Postgres owner and role passwords; `scripts/init-roles.sh` creates the roles on first start |
| `DATA_ROOT` | `/srv/meridian` (`./.localdata` locally) | Host folder for raw files, model weights, relay files and question-set runs |
| `MERIDIAN_REGISTRY`, `MERIDIAN_TAG` | the project's GHCR namespace, `stable` | Where images are pulled from, and which channel (ADR 0008) |
| `MERIDIAN_POSTGRES_TAG`, `MERIDIAN_CRAWL4AI_TAG` | `stable` | Channels of the Postgres and browser images |
| `GHCR_READ_USER`, `GHCR_READ_PAT` | unset | Read-only registry login for the `autoupdate` profile's watchtower |
| `WATCHTOWER_POLL_INTERVAL`, `WATCHTOWER_NOTIFICATION_URL` | 3600, unset | How often watchtower checks for new images, and where it reports |
| `TZ` | set in compose | The watchtower container's zone. The display zone is a database setting, not this (ADR 0009) |
| `MODELS_DIR`, `EMBEDDER_BIND`, `EMBEDDER_MEMORY` | `/srv/meridian/models`, `0.0.0.0`, 8G | The separate embedder node (`deploy/embedder-node/`): weights folder, listen address, memory limit |
