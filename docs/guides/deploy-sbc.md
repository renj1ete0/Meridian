# Deploying on two ARM boards, with automatic updates

Meridian on two arm64 single-board computers, updated by Watchtower whenever a
build is promoted. Task `P3-12`. The general runbook is
[deployment.md](deployment.md) — its keys table (§1b), smoke run (§4), egress
restriction (§4b) and snapshots (§6) all still apply; this page is what differs.

```
 build machine (x86)                 GHCR (private)                  board 1: the stack
 ───────────────────                 ──────────────                  ──────────────────
 make build-push  ──── pushes ────►  meridian-*:<sha>                postgres, worker, api, web,
 make promote SHA ──── retags ────►  meridian-*:stable  ◄── pulls ── scheduler, crawl4ai, searxng …
                                                        ◄── pulls ── board 2: the embedding model
                                                                      (embedder, port 8100 on the LAN)
```

**Nothing is built on the boards.** Your machine builds both architectures and
pushes them tagged by commit. `make promote SHA=…` points the `stable` tag at a
commit, and Watchtower on each board notices within the hour, pulls, and
restarts what changed. Promoting *is* the release — nothing else changes what
the boards run — and rolling back is promoting the previous commit.

**Why the model gets its own board.** It is the one part that wants a whole
machine: 2.3 GB of weights and every CPU core while it embeds, against about
1 GB for everything else except Postgres and the browser. It talks to the rest
over a single HTTP call, so it moves cleanly.

---

## 0. What you need

| | Board 1 (stack) | Board 2 (embedder) |
|---|---|---|
| OS | 64-bit (`uname -m` says `aarch64`) | same |
| Memory | 16 GB: Postgres 2–4 GB, browser ~2 GB, the rest ~1 GB | 16 GB: the model under load took 7 GB here |
| Disk | NVMe; the corpus and raw store grow (deployment.md §1) | ~3 GB for the weights |
| Software | Docker Engine, compose plugin **≥ 2.24** (for `!override`) | Docker Engine, compose plugin |

Check the compose version on board 1 before anything else: `docker compose
version`. Older versions reject `deploy/split/remote-embedder.yml` with an
unknown-tag error.

**Speed, honestly.** This page's build machine embeds 200–400 passages a
minute with bfloat16 instructions an ARM core does not have; expect tens a
minute on board 2. The crawl can produce far more than that, and backpressure
(`MERIDIAN_WORKER_MAX_EMBED_BACKLOG`, default 20000) will pause it while the
embedder catches up — slower, not broken. If you are moving an existing corpus,
embed it on the fast machine first (§5).

---

## 1. On the build machine: publish

Once, a builder that can emit arm64 (the build script refuses without one):

```bash
docker buildx create --name meridian --driver docker-container --use
docker run --privileged --rm tonistiigi/binfmt --install arm64
echo $GITHUB_TOKEN | docker login ghcr.io -u renj1ete0 --password-stdin   # write:packages
```

Then, for each release — from a clean, committed tree:

```bash
make build-push            # every image, both architectures, tagged by commit
make promote SHA=<commit>  # point `stable` at it; the boards follow within the hour
```

The packages must stay **private**, and each board gets a token that can only
read them (§2, §3). Confirm under github.com → your packages → settings.

---

## 2. Board 2: the embedding node

Copy two files from the repo — the board needs nothing else:

```bash
sudo mkdir -p /srv/meridian/embedder /srv/meridian/models
sudo chown 1001:1001 /srv/meridian/models      # the image runs as uid 1001
cd /srv/meridian/embedder
# copy here: deploy/embedder-node/docker-compose.yml and env.example
cp env.example .env && chmod 600 .env
```

Fill in `.env` (see the comments in it). Two values matter:

- `MERIDIAN_EMBEDDER_TOKEN` — a long random string, **the same on both
  boards**: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
  The compose file refuses to start without it, because the port is on the LAN.
- `GHCR_READ_USER` / `GHCR_READ_PAT` — your GitHub login and a token with
  `read:packages` **only**.

```bash
echo $GHCR_READ_PAT | docker login ghcr.io -u renj1ete0 --password-stdin
docker compose --profile tools run --rm modelfetch    # the weights, once
docker compose up -d
docker compose logs -f embedder                       # wait for the model to load
```

Keep the port to board 1 only:

```bash
sudo ufw allow from <board 1 address> to any port 8100 proto tcp
sudo ufw deny 8100
```

Check it, from board 1:

```bash
curl -s http://<board 2>:8100/health                  # {"status":"ok",...,"loaded":true}
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<board 2>:8100/embed \
  -H 'content-type: application/json' -d '{"texts":["x"]}'        # 401: no token
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<board 2>:8100/embed \
  -H "authorization: Bearer <token>" -H 'content-type: application/json' \
  -d '{"texts":["x"]}'                                           # 200
```

---

## 3. Board 1: the stack

The repo checkout is needed for configuration it mounts (SearXNG settings, the
firewall rules), not for building:

```bash
sudo mkdir -p /srv/meridian/app && sudo chown "$USER" /srv/meridian/app
git clone git@github.com:renj1ete0/Meridian.git /srv/meridian/app
cd /srv/meridian/app
cp .env.example .env && chmod 600 .env
```

Fill in `.env` as deployment.md §2 describes, and for this layout also:

| Variable | Value |
|---|---|
| `MERIDIAN_EMBEDDER_URL` | `http://<board 2 address>:8100` |
| `MERIDIAN_EMBEDDER_TOKEN` | the same string as board 2 |
| `GHCR_READ_USER`, `GHCR_READ_PAT` | your login, a `read:packages`-only token |
| `WATCHTOWER_NOTIFICATION_URL` | optional, e.g. `telegram://<bot token>@telegram/?chats=<chat id>` |

Every command on this board names both compose files. An alias saves mistakes:

```bash
alias mc='docker compose -f docker-compose.yml -f deploy/split/remote-embedder.yml'
```

**The firewall first** (deployment.md §4b), with the one exception this layout
needs — board 1's `lan` network may reach board 2's port, and nothing else on
your LAN:

```bash
sudo cp deploy/egress-restrict.nft /etc/nftables.d/meridian.nft
# in /etc/nftables.d/meridian.nft, inside `set lan_allow { … }`, add:
#     elements = { <board 2 address> . 8100 }
sudo nft -f /etc/nftables.d/meridian.nft
sudo nft list set inet meridian lan_allow      # shows the element
```

Only `api`, `embed`, `scheduler` and `orchestrator` join `lan` in this layout.
The worker does not: it fetches the open web and never calls the embedder.
And `embed` runs remote-only (`MERIDIAN_EMBED_REMOTE_ONLY=1`): on one machine it
loads the model itself when the sidecar is down, which here would be 2.3 GB and
every core beside Postgres. On this board a down board 2 means batches wait.

**First boot:**

```bash
echo $GHCR_READ_PAT | docker login ghcr.io -u renj1ete0 --password-stdin
mc pull
mc run --rm datadirs
mc up -d postgres
mc run --rm tools alembic upgrade head
mc run --rm tools python scripts/seed.py
mc --profile autoupdate up -d
```

No `modelfetch` here — the model lives on board 2.

**Check it** — the four egress checks in deployment.md §4b, plus these two:

```bash
# Must SUCCEED: a caller reaches the embedding node.
mc exec embed python -c "import urllib.request; print(urllib.request.urlopen('http://<board 2>:8100/health', timeout=5).read())"
# Must FAIL: the worker does not.
mc exec worker python -c "import socket; socket.create_connection(('<board 2>', 8100), timeout=5)"
```

Then the smoke run, deployment.md §4.

---

## 4. Day to day

**Shipping a change:** commit → `make build-push` → `make promote SHA=<commit>`.
Within `WATCHTOWER_POLL_INTERVAL` (an hour) each board pulls what changed and
restarts it; with a notification URL set you get a message saying so. To not
wait: `docker restart meridian-watchtower-1` (board 1) or
`meridian-embedder-watchtower-1` (board 2).

**Rolling back:** `make promote SHA=<the previous commit>`. Same path, same hour.

**Migrations run themselves — so promote carefully.** With the `autoupdate`
profile, `migrate` runs `alembic upgrade head` each time a promoted tools image
arrives, then idles; a failure is loud (it exits, retries, never reports
healthy). For this to be safe, a promoted migration must be one the *old* code
survives for the minute before its own containers restart: add columns and
tables, don't rename or drop them in the same release. A migration that isn't,
ship by hand: `mc stop worker scheduler embed api`, promote, wait for `migrate`
to be healthy, `mc --profile autoupdate up -d`.

**What never updates itself:** Postgres, the browser (`crawl4ai`), SearXNG and
cloudflared carry no Watchtower label. Postgres especially is a deliberate,
snapshot-first step (deployment.md §6): `mc pull postgres && mc up -d postgres`.

**Changing the timetable:** `config/schedule.yaml` seeds the database at first
boot only. Later changes are rows in `scheduled_jobs` (see handover.md).

---

## 5. Moving an existing corpus

Production starts empty by design; you do not have to bring anything. If you
want the corpus this machine has built — days of crawling and, above all, of
embedding — carry it with the snapshot tooling in deployment.md §6. Let the
backlog here reach zero first (`embedding_backlog` on the health line): an ARM
board would take weeks to embed what this machine does in hours.

---

## 6. When something is wrong

| Symptom | Cause |
|---|---|
| `exec format error` | An image built for one architecture only. Rebuild with `make build-push` (both). |
| Watchtower never updates, says nothing | No or expired `GHCR_READ_PAT`, or the board was never `docker login`-ed. Private images are invisible to it otherwise. |
| Search `degraded: true`, embed backlog only grows | Board 1 cannot reach board 2: the nft element is missing, `ufw` is blocking board 1, or the URL is wrong. Run the two checks in §3. |
| Every embed request fails with 401 | The tokens differ between the boards (or one has trailing whitespace). |
| `unknown tag !override` | Compose older than 2.24 on board 1. |
| Board 2 `modelfetch` fails with permission denied | `/srv/meridian/models` is not owned by uid 1001. |
| `migrate` restarting | A migration failed; `mc logs migrate` says which. Nothing after it has updated its schema, so fix and promote again. |
| The crawl pauses for long stretches | Backpressure waiting on the embedder (§0). Expected on ARM; lower `MERIDIAN_WORKER_CONCURRENCY` to crawl gently, or accept the duty cycle. |
