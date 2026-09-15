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

**Set up the digest before you walk away** (`P5-07`). Two days of unattended
crawling with no channel means the only way to know how it went is SSH.

```bash
# .env — both or neither
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_CHAT_ID=<your own chat id>

# see what would be sent, without sending
docker compose run --rm worker python -m worker.digest --no-send

# then on a timer (systemd, or cron)
docker compose run --rm worker python -m worker.digest
```

It sends the health line and alerts on **sustained** conditions only — fetch
success low over a window, nothing fetched for hours, disk above 80%, and the
frontier drained. That last one is the one to care about: a crawl that empties
its queue and idles logs exactly what a healthy one logs.

Without a token it still runs, still evaluates, and still records to
`notifications` — it just delivers nothing.

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
docker compose run --rm worker python -m worker.harvest    # acronym definitions into the gazetteer
docker compose run --rm worker python -m worker.retopic    # topics onto pre-P2-14 sources (--apply to write)
make snapshot-corpus                                       # the run's deliverable
```

The harvest (`P5-02`) reads every document nobody has read yet and files each
`Full Name Here (ACRONYM)` it finds. Nothing it finds is approved: a term needs a
person, or three separate documents defining it the same way. Approved terms
override statistical NER, so a regex with the final say over entity extraction is
not a trade worth making.

Look at what it proposed before approving anything:

```bash
docker compose exec postgres psql -U meridian -d meridian -c \
  "SELECT canonical, aliases, occurrence_count, approved, ambiguous
     FROM gazetteer WHERE source = 'auto_acronym' ORDER BY occurrence_count DESC LIMIT 40"
```

`ambiguous = true` means two documents gave the same acronym different
expansions. The acronym is held out of the matcher on purpose — the resolver
decides it from context — while the full expansion still matches.

Better than psql: **Admin → Gazetteer** in the UI (`P6-13`) shows the same queue
with an Approve / Turn down decision per term, and tells you for each row whether
the matcher will actually load it. That last part is the reason to use the screen
rather than SQL: a term can read `approved` and match nothing in any document,
because another row already claims the same wording, and nothing else in the
system reports it.

**Admin → Topics** is §10's weight vector: what fraction of seeds each topic
draws, a share you can set, and four states — active, maintenance, paused,
archived. Nothing there deletes. Archiving takes a topic out of the pool and
leaves every source, chunk and figure it collected exactly where it is, so
coming back is one click rather than a re-crawl.

Two numbers per row, and they differ whenever something is acting on the topic:
`weight` is what is stored, `share` is what actually gets drawn once floors,
ceilings and boosts apply. The row says which one did it. Every change is
written to `steering_log`, including the weights that moved because you steered
a *different* topic — which is the question you will have in a month.

`/api/admin/*` is the only part of the API that changes anything, and it **fails
closed**. Without a way to identify callers it refuses everything with a 503
naming the fix. On an exposed instance that means configuring Access (§8b);
on a laptop or a LAN-only box, say so explicitly:

```bash
# .env — ONLY on an instance that is not reachable from outside.
# It means "there is nobody to authenticate here", not "skip authentication".
MERIDIAN_ADMIN_ALLOW_ANONYMOUS=true
```

Loading the gazetteer into spaCy needs the optional extra, which the image does
not carry by default (nothing in the fast loop runs NER until `P5-01`):

```bash
uv sync --extra ner && python -m spacy download en_core_web_sm
# or, to match curated terms with no statistical model at all:
MERIDIAN_SPACY_MODEL=blank
```

---

## 6. Backups

```bash
MERIDIAN_BACKUP_ROOT=/mnt/elsewhere/meridian make backup

# then on a timer, on the host (P5-08)
sudo cp deploy/meridian-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meridian-backup.timer
systemctl list-timers meridian-backup     # confirm it is actually scheduled
```

A systemd timer rather than a row in the scheduler, and the reason is worth
knowing: `backup.sh` needs `docker compose exec postgres pg_dump`, so it needs
the Docker socket — and giving the worker container that socket would hand the
process that fetches hostile web pages control of the host's container runtime.
The timetable runs what belongs in the container; the host runs what belongs on
the host.

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

**Hybrid search needs the embedding sidecar** (`P2-17`). It is the worker's own
image with a different command:

```bash
docker compose up -d embedder
docker compose logs embedder | tail -3      # first start loads 2.3GB
curl localhost:21114/api/explore/stats      # sanity
```

Set `MERIDIAN_EMBEDDER_URL=http://embedder:8100` in `.env`. Without it, search
runs on words alone — every response says so, and the UI shows it. That matters
most for broad research questions and least for exact-term lookups.

The distinction the API draws is worth knowing: *"this deployment has no
embedder"* is a choice, and *"the embedding service did not answer"* is an
outage. If you see the second, the sidecar is down — the search still works on
one arm, which is why you would otherwise not notice.

---

## 8. Expose it (not built yet)

**This is the part that lets an assistant on your phone query the corpus.
Do not attempt it yet.** The pieces that exist:

- ✅ `/mcp` — the MCP server with four read tools, mounted on the API
- ✅ `meridian_guest` — a database role that cannot read your token table
- ✅ `meridian_core/tokens.py` — scoped credentials with tool scope and expiry

What is missing, and why it blocks:

- ✅ `P3-03` — the surface **refuses unauthenticated callers by default**, and
  checks the scope per tool. Anonymous access is an explicit opt-out
  (`MERIDIAN_MCP_ALLOW_ANONYMOUS`), which `.env.dev` sets for local use

- ✅ `P3-08` — Access assertions are **verified cryptographically** against
  your team's published keys, with the audience checked, on every request. The
  unsigned `Cf-Access-Authenticated-User-Email` header is never believed. Set
  `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD`; without both, the middleware is
  not installed and says so at startup

- ✅ `P3-09` server half — the MCP surface advertises where to authenticate at
  `/.well-known/oauth-protected-resource/mcp`. A hosted client is handed a URL
  and nothing else, so it has to *discover* the authorization server; without
  this a phone assistant given the URL can only fail
- ✅ Tokens are refused if issued for a **different resource** on the same
  issuer — the same check as an Access assertion's audience

**What is left is configuration on your side.** Follow it in this order:

### 8a. Point a tunnel at the API

`cloudflared` is already in `docker-compose.yml` and needs a token.

1. Cloudflare Zero Trust → **Networks → Tunnels** → create a tunnel, copy the
   token into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`
2. Add a **public hostname** on that tunnel: your hostname → `http://api:8000`
3. `docker compose up -d cloudflared api`

### 8b. Put Access in front of it

4. Zero Trust → **Access → Applications** → add a *self-hosted* application for
   that hostname
5. Add a policy — email-based is enough to start; one-time PIN needs no identity
   provider
6. Copy the application's **AUD tag** (Overview tab)

### 8c. Tell Meridian

```bash
# .env — Access assertion verification (P3-08)
CF_ACCESS_TEAM_DOMAIN=<your-team>.cloudflareaccess.com
CF_ACCESS_AUD=<the AUD tag from step 6>

# The MCP surface. Do NOT set MERIDIAN_MCP_ALLOW_ANONYMOUS here.
MERIDIAN_MCP_ISSUER_URL=https://<your-team>.cloudflareaccess.com
MERIDIAN_MCP_RESOURCE_URL=https://<your-hostname>/mcp

# DNS-rebinding protection defaults to loopback, so the tunnel is refused
# until the real hostname is named — deliberately, so exposure is a decision.
MERIDIAN_MCP_ALLOWED_HOSTS=<your-hostname>
MERIDIAN_MCP_ALLOWED_ORIGINS=https://<your-hostname>
```

`docker compose up -d api` and check the startup log says
`cloudflare access verification enabled`. If it says *not configured*, both
`CF_ACCESS_*` values are not set and **every request is unauthenticated** —
stop and fix it before going further.

### 8d. Verify before you trust it

```bash
# Must FAIL with 401 — no Access assertion
curl -s -o /dev/null -w '%{http_code}\n' https://<your-hostname>/api/explore/stats

# Must SUCCEED — health bypasses Access so the watchdog can reach it
curl -s https://<your-hostname>/health

# Must FAIL — a forged identity header must not be believed
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Cf-Access-Authenticated-User-Email: attacker@example.test' \
  https://<your-hostname>/api/explore/stats
```

### 8e. Connect the assistant

Add `https://<your-hostname>/mcp` as a remote MCP server. It will discover the
authorization endpoint, send you through Cloudflare Access to log in, and then
present the token it receives.

Then issue it a Meridian credential (see the snippet above) — Access decides
*who got in*, the scoped token decides *what they may read*.

### 8f. Optional — the SQL escape hatch

§12.4 offers an agent one more tool: a direct read-only SQL query, for questions
the curated tools cannot answer. It runs as `meridian_guest`, which can see the
corpus and the graph and **not** the table holding your token hashes.

```bash
# .env — both are needed; the role is created NOLOGIN without a password
PG_GUEST_PASSWORD=<a distinct password>
PG_GUEST_URL=postgresql://meridian_guest:<that password>@postgres:5432/meridian
```

Leave them unset and the tool is simply not registered. Worth knowing before you
enable it: every query is logged, deliberately — the queries an agent reaches
for here are the best evidence about which curated tool to build next.

| Still open | Task |
|---|---|
| Mapping an Access identity to a grant | `P3-06`, `P3-10` |
| Per-token rate limiting and an audit log | `P3-11` |

**Do not set `MERIDIAN_MCP_ALLOW_ANONYMOUS` on anything reachable.** It is for a
loopback development machine. With it set on an exposed host, every tool answers
anyone who can reach the port.

Issuing a credential, once the tunnel exists:

```python
# from a shell on the server: docker compose run --rm api python
from meridian_core.db import session
from meridian_core.tokens import issue_token
import asyncio, datetime as dt

async def mint():
    async with session("rw") as s:
        secret, row = await issue_token(
            s,
            agent_id="my-phone",
            allowed_tools=["search_chunks", "get_source_metadata",
                           "list_new_since", "corpus_overview"],
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
        )
        await s.commit()
        print("secret (shown once):", secret)

asyncio.run(mint())
```

The secret is printed once and only its hash is stored. `allowed_tools` has no
default — an unscoped token grants nothing, so name the tools.

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
