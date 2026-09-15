# Setting up Meridian

One document, start to finish: a machine with nothing on it, to a corpus you can
query from your phone.

**Read the status column before following a section.** Some of this works today
and some of it is not built, and the difference is stated rather than implied —
instructions for something that does not exist are worse than no instructions.

| Part | What it gets you | Status |
|---|---|---|
| [1. Decisions](#1-decisions-to-make-first) | The three things that change what you build | — |
| [2. The server](#2-the-server) | The stack running, database migrated and seeded | **works** |
| [3. Lock down egress](#3-lock-down-egress-do-this-before-the-long-run) | The crawler cannot reach your LAN | **works** |
| [4. The smoke run](#4-the-smoke-run) | Proof the containers talk to each other | **works** |
| [5. The 48h run](#5-the-48h-run) | A corpus worth querying | **works** |
| [6. Backups](#6-backups) | The crawl survives the disk | **works** |
| [7. Look at it](#7-look-at-it) | Search, API, and the web UI | **works** |
| [8. Expose it](#8-expose-it-not-built-yet) | Your phone can reach it | **not built** |

---

## 1. Decisions to make first

**Where the data lives.** `DATA_ROOT`, default `/srv/meridian`. It holds
`pgdata/`, `raw/` and `figures/`. The raw store only grows — put it on the disk
you are willing to fill.

**How images get there.** The target is arm64 and your laptop probably is not.
Build on the server (`docker compose build`) for a first deploy; `make
build-push` is the multi-arch path and is not written yet (`P1-37`).

**Whether you are sharing.** If the answer is no, skip part 8 entirely and leave
`PG_GUEST_PASSWORD` blank. Nothing degrades.

---

## 2. The server

The systemd unit expects the checkout at `/srv/meridian/app`.

```bash
sudo mkdir -p /srv/meridian/app && sudo chown "$USER" /srv/meridian/app
git clone <this repo> /srv/meridian/app && cd /srv/meridian/app
cp .env.example .env && chmod 600 .env
```

Fill in `.env`. The ones that are not optional:

| Variable | Note |
|---|---|
| `PG_PASSWORD`, `PG_RW_PASSWORD`, `PG_RO_PASSWORD` | Three distinct passwords, **read once** at first boot |
| `PG_RW_URL`, `PG_RO_URL`, `PG_MIGRATION_URL` | Host is `postgres`, port `5432` — the compose service |
| `DATA_ROOT` | From part 1 |
| `CRAWL4AI_TOKEN` | Any long random string |
| `MERIDIAN_CONTACT_EMAIL` | Unpaywall rejects requests without one, and §14.2 asks the crawler to be contactable |
| `SEARXNG_URL` | `http://searxng:8080`. Unset means `query` rows never get claimed |

> **The three `PG_*_PASSWORD` values are read once.** `init-roles.sh` runs from
> `docker-entrypoint-initdb.d`, which Postgres executes only when the data
> directory is empty. Changing them later changes nothing until you delete
> `pgdata`, and the symptom is an auth failure that looks like a typo.

Bring up the database, migrate, seed:

```bash
docker compose up -d postgres
docker compose logs -f postgres          # wait for "database system is ready"

docker compose run --rm worker alembic upgrade head
docker compose run --rm worker python scripts/seed.py
```

**Check the roles exist**, because a failed `init-roles.sh` leaves a perfectly
working database with one user:

```bash
docker compose exec postgres psql -U meridian -d meridian -c '\du'
```

You want `meridian`, `meridian_rw`, `meridian_ro` and `meridian_guest`. If
`meridian_rw` is missing, stop — fix `.env`, `docker compose down -v`, start
again. This is far cheaper now than after a two-day crawl.

---

## 3. Lock down egress (do this before the long run)

The crawler follows links out of pages it did not write. `netguard` refuses
private addresses in the application and `P1-24` pins the validated address, and
both are code running inside the process that is deliberately fetching
attacker-chosen URLs. This is the defence that survives a bug in either.

```bash
sudo mkdir -p /etc/nftables.d
sudo cp deploy/egress-restrict.nft /etc/nftables.d/meridian.nft
sudo nft -f /etc/nftables.d/meridian.nft
```

Persist it however this host persists nftables. **A rule set that vanishes on
reboot is worse than none, because you will believe it is there.**

### Verify it — all four, every time

A firewall rule that matches nothing behaves exactly like one that works.

```bash
# Must FAIL — your LAN
docker compose exec worker python -c \
  "import socket; socket.create_connection(('192.168.1.1', 80), timeout=5)"

# Must FAIL — cloud metadata, the classic SSRF target
docker compose exec worker python -c \
  "import socket; socket.create_connection(('169.254.169.254', 80), timeout=5)"

# Must SUCCEED — the open web is the point
docker compose exec worker python -c \
  "import socket; socket.create_connection(('example.org', 80), timeout=5)"

# Must SUCCEED — Postgres is on an RFC1918 address itself
docker compose exec worker python -c \
  "import socket; socket.create_connection(('postgres', 5432), timeout=5)"
```

Three passing and one wrong is the configuration that looks fine and is not.

---

## 4. The smoke run

Bounded by task count, not by a timer. The question is "do the containers talk
to each other", not corpus volume.

```bash
docker compose up -d crawl4ai searxng
docker compose ps                        # crawl4ai must reach (healthy) — up to 60s
docker compose run --rm -e MERIDIAN_WORKER_MAX_TASKS=20 worker python -m worker.main
```

Watch for, in order:

1. `task settled` lines with a `task_id` and `url`
2. a non-null `stored` path, and that file existing under `$DATA_ROOT/raw`
3. non-zero `chars` and `chunks` on HTML and PDF pages
4. **non-zero `queued` somewhere** — a run where every line says `queued: 0` is
   the failure that cost `v0.26.0`: the frontier can only narrow, and the logs
   look identical to a healthy run
5. `browser: configured` on the health line, not `absent`
6. `search: configured` — `absent` means `query` rows sit unclaimed for 48h

Then prove the network boundary, which only a real stack can tell you:

```bash
# Must FAIL — the browser has no route to the database, by construction
docker compose exec crawl4ai python -c \
  "import socket; socket.create_connection(('postgres', 5432), timeout=5)"
```

---

## 5. The 48h run

Only after part 4 is clean. Hand the stack to systemd:

```bash
sudo cp deploy/meridian.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meridian
```

Leave `MERIDIAN_WORKER_MAX_TASKS` unset. Do **not** add `Restart=` to the unit —
compose owns per-service restarts, and two supervisors racing to restart one
container is how a crash loop goes invisible.

Check daily:

```bash
docker compose logs --since 24h worker | grep 'fetch health'
du -sh "$DATA_ROOT/raw"
docker compose exec postgres psql -U meridian -d meridian -c \
  "SELECT status, count(*) FROM queue GROUP BY 1 ORDER BY 2 DESC"
```

A `pending` count falling monotonically to zero means the frontier is narrowing
and the run will idle out early. A healthy run keeps finding more than it drains.

Afterwards, three passes that are not part of the crawl loop:

```bash
docker compose run --rm worker python -m worker.embed      # vectors
docker compose run --rm worker python -m worker.novelty    # near-duplicate verdicts
docker compose run --rm worker python -m worker.sweep      # retention report (add --apply to delete)
make snapshot-corpus                                       # the run's deliverable
```

---

## 6. Backups

```bash
MERIDIAN_BACKUP_ROOT=/mnt/elsewhere/meridian make backup
```

Point it at **a different device**. The script warns if it shares a filesystem
with `DATA_ROOT`, because a backup on the disk it protects survives an
accidental delete and nothing else. Run it from a systemd timer.

What it protects is not the database — Postgres rebuilds from migrations and
config re-seeds. It is the crawl: the web moves on, so a page fetched in March
is not re-fetchable, only re-visitable.

---

## 7. Look at it

**The API and the web UI**, on the server or a checkout:

```bash
# API — health, stats, search
docker compose up -d api
curl localhost:21114/health
curl localhost:21114/api/explore/stats
curl "localhost:21114/api/explore/search?q=<a+term+in+your+corpus>&limit=3"

# The UI (development)
cd web && npm install && npm run dev      # http://localhost:21115
```

**Measure the retrieval stack** once there is a corpus:

```bash
make bench-search
```

It reports index recall, latency and how often the two search arms agree — and
refuses to report numbers a small corpus cannot support, which is most of what
it does before `P1-16`.

> **Search is word-matching only right now.** There is no query embedder
> (`P2-17`), so semantically-related passages phrased differently are not found.
> Every response says so, and the UI shows it. This matters most for broad
> research questions, least for exact-term lookups.

---

## 8. Expose it (not built yet)

**This is the part that lets an assistant on your phone query the corpus.
Do not attempt it yet.** The pieces that exist:

- ✅ `/mcp` — the MCP server with four read tools, mounted on the API
- ✅ `meridian_guest` — a database role that cannot read your token table
- ✅ `meridian_core/tokens.py` — scoped credentials with tool scope and expiry

What is missing, and why it blocks:

| Missing | Task | Why it blocks |
|---|---|---|
| The token verifier wired into `/mcp` | `P3-03` | **`/mcp` authenticates nobody today.** It is safe only because it is published to loopback |
| Cloudflare Tunnel + Access | `P3-05` | Nothing reaches the machine from outside |
| Access JWT verification | `P3-08` | Without it the app trusts a header, and a header is a string anyone can send |
| OAuth flow | `P3-09` | A phone app pastes a URL — there is nowhere to put a service-token header |

**Until all four land, `api` must stay on `internal` and published to loopback.**
Putting the current `/mcp` behind a tunnel would expose an unauthenticated read
surface over the corpus to the internet.

When they do land, the shape is: Cloudflare Access authenticates the person via
OAuth; Meridian's scoped token decides what that person may read. Two layers,
because Access is all-or-nothing at the hostname — it says *who got in*, never
*what they may read*.

---

## Traps that have already cost a session

- **`DOCKER_HOST`** — a rootless Docker needs
  `DOCKER_HOST=unix:///var/run/docker.sock` in `.env.dev`, or compose silently
  picks the wrong file and the error names the wrong problem.
- **`MERIDIAN_RAW_ROOT`** — set it, or the worker writes to `/data/raw`, which
  is the container's mount point. A corpus can end up spanning two raw stores
  (`P1-45`); `python -m worker.sweep` tells you when it has.
- **`make test`, never bare `pytest`** — the Makefile exports `.env.dev`, and
  subprocess tests fail without it in a way that reads as a code failure.
- **A green test run under a second tested almost nothing** — integration tests
  skip without Postgres. Read the skip count, not the colour.
- **`pdftotext` and `pdfinfo` must be on PATH** (`poppler-utils`). Their absence
  raises rather than degrading, which is deliberate.

More detail on any of this: [deployment.md](deployment.md) for the server,
[handover.md](handover.md) for how the parts fit and what has already gone
wrong, [connectors.md](connectors.md) for adding sources.
