# Deploying the stack

The runbook for putting Meridian on the server it is meant to live on, and for
the two runs that close phase 1: the bounded **smoke run** (does the stack come
up and talk to itself) and `P1-16`, the **48h unattended run** whose output
becomes the dev corpus.

Read [handover §2](handover.md) first if you have only ever run this from a
checkout. The traps there are about the dev machine; the ones here are about the
server.

> **Note on task IDs.** `P0-15` is the held-out question set — a writing task,
> nothing to deploy. The deploy is what stands between here and `P1-16`, and
> `P0-15` has to be written before `P1-16`'s corpus is judged by `P2-09`. Both
> are yours; neither is code.

---

## 0. What you are deploying

Eight services, two of which do not start on their own schedule
(`orchestrator`, `cloudflared`). `orchestrator` is the only one with nothing
behind it — phase 4 is unbuilt, so it has no image and nothing to run. For the
smoke run only four matter:

| Service | Network | Why it is in the smoke run |
|---|---|---|
| `postgres` | `internal` | The queue, the corpus, and the only stateful thing here |
| `worker` | `internal` + `egress` | The thing being tested |
| `crawl4ai` | `egress` | The worker `depends_on` it as `service_healthy` and **will not start without it** |
| `searxng` | `egress` | `service_started` only — a missing one costs `query` rows, not the crawl |

The network split is a security boundary, not organisation. `crawl4ai` drives a
real browser against pages this crawler found by following links, so it is on
`egress` alone with no route to Postgres and no `env_file`. If you find yourself
"simplifying" compose by giving `x-common` a shared `env_file` or `networks`,
`tests/unit/test_compose_topology.py` will fail, and it is right to.

---

## 1. Before you touch the server

**Decide where the data lives.** `DATA_ROOT` (default `/srv/meridian`) holds
`pgdata/`, `raw/` and `figures/`. The raw store only grows unless somebody
spends the sweep: `python -m worker.sweep` reports what §5.4 would reclaim and
deletes only with `--apply` (`P1-31`). A 48h run is the first time that will be
visible. Put it on the disk you are willing to fill, and check `df` before and
after.

**Decide how the images get there.** The server is arm64 and your machine
probably is not, so there are two routes:

- **Build on the server.** `docker compose build` over an SSH session. Slowest,
  needs no registry, and is the right call for a first smoke run.
- **Build and push multi-arch.** What `make build-push` is *for*. See §6 — the
  script it calls is still missing (`P1-37`).

---

## 2. Put the repo and the environment on the server

The systemd unit expects the checkout at `/srv/meridian/app`, so put it there
rather than in a home directory:

```bash
sudo mkdir -p /srv/meridian/app
sudo chown "$USER" /srv/meridian/app
git clone <this repo> /srv/meridian/app
cd /srv/meridian/app
```

Then the environment. `.env` is the only place credentials live (§11.11) and it
is not in git:

```bash
cp .env.example .env
chmod 600 .env
```

Fill in, at minimum:

| Variable | Note |
|---|---|
| `PG_PASSWORD`, `PG_RW_PASSWORD`, `PG_RO_PASSWORD` | Three distinct passwords. `init-roles.sh` reads them at first boot **only** |
| `PG_RW_URL`, `PG_RO_URL`, `PG_MIGRATION_URL` | Host is `postgres`, port `5432` — the compose service, not `localhost:21111` |
| `DATA_ROOT` | Where the corpus lands |
| `CRAWL4AI_TOKEN` | Any long random string; it is shared between the two containers and nothing else |
| `MERIDIAN_CONTACT_EMAIL` | Not optional in practice: Unpaywall rejects requests without one, and §14.2 asks the crawler to be contactable |
| `SEARXNG_URL` | `http://searxng:8080`. Unset means the worker does not claim `query` rows at all — which is correct behaviour and not what you want on a first run |

Leave `SEMANTIC_SCHOLAR_API_KEY` blank unless you have done `P1-35`. Blank costs
retries, not correctness.

**The three `PG_*_PASSWORD` values are read once.** `init-roles.sh` runs from
`docker-entrypoint-initdb.d`, which Postgres executes only when the data
directory is empty. Changing them later changes nothing until you delete
`pgdata`, and the symptom is an authentication failure that looks like a typo in
`.env`.

---

## 3. First boot

```bash
docker compose up -d postgres
docker compose logs -f postgres     # wait for "database system is ready"
```

Then migrate and seed **from the server**, not from your machine — the URLs in
`.env` name `postgres`, which only resolves inside the compose network:

```bash
docker compose run --rm worker alembic upgrade head
docker compose run --rm worker python scripts/seed.py
```

`make migrate` and `make seed` are the local equivalents and read `.env.dev`;
on the server the environment comes from the container, which is why these go
through `compose run` instead.

Sanity-check that the roles actually exist, because a failed `init-roles.sh`
leaves a perfectly working database with one user:

```bash
docker compose exec postgres psql -U meridian -d meridian -c '\du'
```

You want `meridian`, `meridian_rw` and `meridian_ro`. If `meridian_rw` is
missing, the init script did not run — stop, fix `.env`, `docker compose down -v`
and start over. This is much cheaper now than after a 48h run.

---

## 4. The smoke run

The point is "do the containers come up and talk to each other", **not** corpus
volume. Bound it by task count, not by a timer:

```bash
docker compose up -d crawl4ai searxng
docker compose ps                      # crawl4ai must reach (healthy)
```

`crawl4ai` takes up to a minute to report healthy — it warms a browser pool, and
its `start_period` is 60s. The worker will not start until it does.

Then one bounded worker, in the foreground, so you watch it rather than find it:

```bash
docker compose run --rm -e MERIDIAN_WORKER_MAX_TASKS=20 worker python -m worker.main
```

What you are checking, in order:

1. **It claims.** `task settled` lines appear with a `task_id` and a `url`.
2. **It stored.** Those lines carry a non-null `stored` path, and that path
   exists under `$DATA_ROOT/raw`.
3. **It extracted and chunked.** `chars` and `chunks` are non-zero on HTML and
   PDF pages. Zero everywhere means `poppler-utils` or the converter allowlist,
   not the crawl.
4. **It widened.** `queued` is non-zero somewhere. A run where every line says
   `queued: 0` is the failure mode that cost `v0.26.0` — the frontier can only
   narrow, and the logs look identical to a healthy run.
5. **The browser is real.** The health line says `browser: configured`, not
   `absent` or `unreachable`. `absent` means `CRAWL4AI_URL` never reached the
   worker; the fetcher degrades to static and says nothing else about it.
6. **Search is real.** Same line: `search: configured`. `absent` means `query`
   rows will sit unclaimed for the whole 48 hours.

Then confirm the boundary actually holds, which is the one thing only a real
stack can tell you:

```bash
# Must FAIL: the browser has no route to the database, by construction.
docker compose exec crawl4ai python -c \
  "import socket; socket.create_connection(('postgres', 5432), timeout=5)"

# Must FAIL: postgres is on `internal`, which has no default route.
docker compose exec postgres getent hosts example.org
```

Both failing is the `P1-22` topology working. Either succeeding means the
compose file on the server is not the one in the repo.

---

## 4b. Egress restriction (`P1-25`) — do this before the long run

`netguard` refuses private addresses in the application and `P1-24` pins the
validated address so a name cannot resolve to one thing and connect to another.
Both are correct. Both are also code running inside the process that is
deliberately fetching attacker-chosen URLs, and one mistake from failing open.

This is the defence that survives that mistake, and it is the one thing on this
page that is genuinely worth doing *before* leaving the crawler running for two
days on a machine that can see your LAN.

**`internal: true` is not this.** It stops a container reaching the internet. It
does nothing about the worker — which must have a default route — reaching
`192.168.0.0/16`.

```bash
sudo mkdir -p /etc/nftables.d
sudo cp deploy/egress-restrict.nft /etc/nftables.d/meridian.nft
sudo nft -f /etc/nftables.d/meridian.nft
```

Persist it the way this host persists nftables — on most systemd distributions
that is an `include` line in `/etc/sysconfig/nftables.conf` or
`/etc/nftables.conf`. A rule set that vanishes on reboot is worse than none,
because you will believe it is there.

### Verify it. Every time.

This is not optional ceremony. A firewall rule that matches nothing behaves
exactly like a rule that is working: no error, no log line, no difference until
the day it mattered. The subnets are pinned in `docker-compose.yml` precisely so
these rules have a stable target, and `tests/unit/test_compose_topology.py`
fails if that pinning is removed — but neither of those proves the rules loaded.

```bash
# Must FAIL (and should hang until timeout, not refuse instantly — a drop,
# not a reject). Point it at something real on your LAN.
docker compose exec worker python -c \
  "import socket; socket.create_connection(('192.168.1.1', 80), timeout=5)"

# Must FAIL: the cloud metadata endpoint, which is the classic SSRF target.
docker compose exec worker python -c \
  "import socket; socket.create_connection(('169.254.169.254', 80), timeout=5)"

# Must SUCCEED: the open web is the entire point.
docker compose exec worker python -c \
  "import socket; socket.create_connection(('example.org', 80), timeout=5)"

# Must SUCCEED: the worker still has to reach Postgres, which lives on an
# RFC1918 address itself. If this fails, the rules are blocking the stack from
# itself — check the supernet accept rule comes before the drop.
docker compose exec worker python -c \
  "import socket; socket.create_connection(('postgres', 5432), timeout=5)"
```

All four have to behave as stated. Three passing and one wrong is the
configuration that looks fine and is not.

### What is not closed

`P1-25` names two options — no LAN route, or an egress proxy that refuses
private destinations — and this is the first. The proxy is **not** a drop-in
alternative: a forward proxy resolves the hostname itself, which takes DNS away
from the worker and undoes `P1-24`'s address pinning. Choosing it means
deciding that the proxy's destination ACL replaces pinning, which is a real
design decision and not a deployment one. Recorded on the task.

---

## 5. The 48h run (`P1-16`)

Only after §4 is clean. Hand the stack to systemd so it survives a reboot and
so nothing depends on your SSH session:

```bash
sudo cp deploy/meridian.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meridian
```

Leave `MERIDIAN_WORKER_MAX_TASKS` unset — the unbounded loop is the point — and
let it run. `restart: unless-stopped` in compose owns per-service restarts; the
unit owns only the stack. Do not add `Restart=` to the unit: two supervisors
racing to restart one container is how a crash loop goes invisible.

**Watch these, roughly daily:**

```bash
docker compose logs --since 24h worker | grep 'fetch health'
du -sh "$DATA_ROOT/raw"
docker compose exec postgres psql -U meridian -d meridian -c \
  "SELECT status, count(*) FROM queue GROUP BY status ORDER BY 2 DESC"
```

The queue breakdown is the one that matters most. A `pending` count that falls
monotonically towards zero means the frontier is narrowing and the run will idle
out early; a healthy run keeps finding more than it drains.

`du` is the other. Nothing deletes from the raw store, and this run is the first
time that has ever been true at volume.

**Afterwards:** `P1-16` says `make snapshot-corpus`. It does not work yet — see
below.

---

## 6. Four Makefile targets point at scripts that do not exist

`scripts/` contains `init-roles.sh` and `seed.py`. The Makefile also declares:

| Target | Calls | Status |
|---|---|---|
| `make snapshot-corpus` | `./scripts/snapshot_corpus.sh` | built (`P1-36`) — and it is `P1-16`'s stated deliverable |
| `make restore-corpus` | `./scripts/restore_corpus.sh` | built |
| `make backup` | `./scripts/backup.sh` | built; `deploy/meridian-backup.timer` runs it (`P5-08`) |
| `make build-push` | `./scripts/build_and_push.sh` | **still missing** (`P1-37`) |

`snapshot-corpus` was the urgent one, because it is how the 48h run becomes the
dev corpus everything after phase 2 is built against and discovering it was a
stub *after* the run would be a wasted 48 hours. It exists now. The manual
equivalent, if you ever need it, is a `pg_dump` plus a tar of the raw store:

```bash
docker compose exec -T postgres pg_dump -U meridian -Fc meridian > corpus.dump
tar -C "$DATA_ROOT" -czf raw.tar.gz raw/
```

Keep both or neither — a dump whose `sources.raw_path` values point at files you
did not keep is not a corpus, it is a catalogue.
