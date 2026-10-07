# Commands

## Make targets

| Target | What it does |
|---|---|
| `make test` | `make lint`, then the Python suite (needs the dev Postgres up) |
| `make lint` | `ruff check .`, `ruff format --check .`, and the web package's `npm run lint` (oxlint, then `prettier --check`) |
| `npm run format` (in `web/`) | Rewrites the web package with Prettier |
| `make dev-up` / `dev-down` | The dev stack (`docker-compose.dev.yml`): Postgres and services for tests |
| `make local-up` / `local-down` / `local-logs` | The full local stack (`docker-compose.local.yml`) |
| `make up` / `down` / `logs` | The production stack (`docker-compose.yml`) |
| `make migrate` / `revision` | Alembic upgrade / new revision (reads `.env.dev`) |
| `make seed` | Seed configuration tables from `config/*.yaml` (first boot only) |
| `make quickstart` | One command from an empty checkout to a running local stack |
| `make preflight` | Checks a server is ready before deploying |
| `make snapshot-corpus` / `restore-corpus` | Dump and restore the corpus |
| `make backup` | Off-device backup |
| `make bench-search` | Search recall, latency and arm agreement |
| `make leak-check` | The integration suite on a fresh database; prints any rows or settings it left behind ([below](#leak-check)) |
| `make clock-check DAYS=400` | Every test today and `DAYS` ahead on every clock; prints what fails only ahead ([below](#clock-check)) |
| `make build-push` / `promote SHA=…` | Build images, and move the `stable` channel (GHCR, operator only) |

Running `uv run pytest` directly needs the dev environment exported:
`set -a; . ./.env.dev; set +a`.

### Clock check

Tests that pin a date expire when the calendar passes it (`B-128`, `B-143`), and a grep for
fixed dates cannot say which of them are compared with a clock. `make clock-check` answers by
running: the Python and web suites once today and once `DAYS` ahead, against a throwaway
Postgres (`scripts/clockshift/`, its own project on port 21121) whose clock is moved by
libfaketime, so Python's and the database's `now()` agree. It prints the tests that fail only
ahead. Python's clock is moved by `time-machine` (supplied with `uv run --with`, not a project
dependency) and the web's `Date` by a Node preload.

Moving only one clock is not a substitute: with the database left at today, any code comparing
a row's `now()` with Python's clock fails, and those failures say nothing about the calendar.
The liveness tests are filtered out because they compare the clock with a file's real
modification time, which no shift moves. Tests marked `server_timer` are left out too:
libfaketime moves Postgres's timers with its clock, so a statement timeout stops firing. A unit
test fails if a test that waits on `pg_sleep` is not marked. Needs Docker and `web/node_modules`; takes about
eight minutes.

### Leak check

Integration tests run against the developer's own dev database, so a fixture that commits and
does not clean up leaves rows behind, and one that changes configuration and restores it wrongly
re-tunes somebody's crawler. Neither fails a test. `make leak-check` brings up the same
throwaway Postgres as the clock check, seeded, snapshots it (`scripts/clockshift/snapshot.sql`:
every table's row count from the catalogue, so a new table is covered without editing the
check, plus the contents of `topic_config`, `agents`, `budget_config`, the global
`fetch_policy` row and `scheduled_jobs`, leaving out `_at` columns and run bookkeeping), runs
the integration suite once, snapshots again, and prints each changed count or field
(`snapdiff.py`). It exits 1 on any change. About four minutes.

Fixtures that commit use `tests/cleanup.py`: `policy_domains` / `forget_new_policies` remove
the `fetch_policy` rows that queueing created, and `topics_now` / `restore_topics` put every
topic and the steering log back, since `steering.add_topic` renormalises the other topics. Both
remove only what appeared during the test, so the developer's own rows are never touched.
Restore with statements rather than by setting attributes on a row the session already holds:
after a route commits through another connection, the session's copy is stale, and setting it
back to the value it thinks it holds writes nothing.

## Worker passes

Every pass is `python -m worker.<name>`. Inside a container:
`docker compose exec worker python -m worker.<name> …`. The worker's filesystem is read-only, so
to run an ad-hoc script, pipe it in: `docker compose exec -T worker python - < script.py`.

Passes that change data **report by default** and write only with `--apply`. Long-running
ones take `--once` to run a single pass and exit.

| Command | Writes with | What it does |
|---|---|---|
| `worker.main` | — | The crawl loop (the `worker` service) |
| `worker.embed` | always | Embedding backfill (the `embed` service; `--once` for one pass) |
| `worker.embedserver` | — | The embedding service (the `embedder` service) |
| `worker.fetchmodel` | always | Downloads model weights into the shared volume |
| `worker.scheduler` | — | Runs the timetable (the `scheduler` service) |
| `worker.bot` | — | Telegram inbound commands (the `bot` service) |
| `worker.orchestrate` | always (`--dry-run` rolls back) | One synthesis cycle; `--daemon` to run on a schedule |
| `worker.retopic` | `--apply` | Topic labels; `--demote-offtopic` (with `--apply`) junks very off-topic sources |
| `worker.places` | `--apply` | Place tags |
| `worker.novelty` | always | Near-duplicate passages |
| `worker.docdupes` | `--apply` | Copies of earlier sources |
| `worker.edgedupes` | `--apply` | Folds repeated edges left by old merges |
| `worker.areas` | always (`--report` dry) | Map areas; `--name-only` renames the newest build |
| `worker.boilerplate` | always (`--report` dry) | Repeated lines per site |
| `worker.rechunk` | `--apply` | Re-cuts stored sources with furniture removed |
| `worker.reembed` | `--apply` | Re-embeds passages whose embedded text has changed |
| `worker.dockind` | `--apply` | Classifies stored documents by kind; `--all` re-derives |
| `worker.furniture` | `--apply` | Demotes site furniture to junk |
| `worker.retier` | `--apply` | Re-tiers pages whose scholarly tier came only from their domain |
| `worker.retitle` | `--apply` | Cleans stored titles |
| `worker.relanguage` | `--apply` | Reads the language of stored sources that declared none; sends those in another language back for topic labels (`B-153`) |
| `worker.harvest` | always | Acronym harvest |
| `worker.translate` | always | Other-language vocabulary |
| `worker.seedsearch` | always (`--report` dry) | Search seeds |
| `worker.sitemapmine` | `--apply` | Sitemaps of proven hosts |
| `worker.hostscore` | always (`--report` dry) | Host scores |
| `worker.requeue`, `worker.requeue_links`, `worker.requeue_dois` | `--apply` | Re-rank pending queue rows |
| `worker.steerproposals` | always (`--report` dry) | Steering proposals |
| `worker.digest` | always | Digest and alerts |
| `worker.sweep` | `--apply` | Retention sweep (the only pass that deletes files) |

## API commands

Run in the API container: `docker compose exec api python -m api.<name> …`.

| Command | What it does |
|---|---|
| `api.tokens issue <name> [--profile reader\|analyst\|operator] [--days 90]` | Issue an MCP token, printed once with client setup |
| `api.tokens list [--all]` | List tokens (never their secrets) |
| `api.tokens revoke <id>` | Turn a token off |

## Scripts

| Script | What it does |
|---|---|
| `scripts/seed.py` | Seeds configuration tables (`make seed`) |
| `scripts/run_question_set.py` | Runs the held-out question set and writes `eval/runs/<date>.yaml` |
| `scripts/benchmark_search.py` | Search benchmark (`make bench-search`) |
| `scripts/snapshot_corpus.sh`, `restore_corpus.sh` | Corpus dump and restore |
| `scripts/backup.sh` | Off-device backup (run by a systemd timer, not the scheduler) |
| `scripts/build_and_push.sh`, `promote.sh` | Image release |
| `scripts/preflight.sh`, `quickstart.sh` | Server checks and local bootstrap |
| `scripts/init-roles.sh` | Creates the database roles on first Postgres start |
| `scripts/clockshift/` | The clock check and the leak check (`make clock-check`, `make leak-check`) |
