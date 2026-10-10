# API and access

One FastAPI service serves the reader (`/api/explore/*`), the control surface
(`/api/admin/*`), the MCP surface (`/mcp`) and `/health`. **The route prefix is the role
boundary**: explore routes get a read-only database session, admin routes get a writable one
behind an identity gate, and nothing under `/api/explore` can open a writable session.

- **Code:** `services/api/api/main.py`, `deps.py`, `access.py`, `auth.py`, `cache.py`,
  `tokens.py`, `routes/explore.py`, `routes/admin.py`, `routes/graph.py`, `routes/gaps.py`,
  `routes/connect.py`, `routes/neighbourhood.py`, `routes/steering_proposals.py`,
  `routes/growth.py`, `routes/tokens.py`; `packages/meridian_core/meridian_core/db.py`
- **Tasks:** `P2-07`, `P3-07`, `P3-08`, `P6-13`, `B-121`, `B-145`, `B-146`, `B-201`, `B-202`

## How it works

**Three database roles** (scaffold §4), created by `scripts/init-roles.sh`:

| Role | Used by | Can |
|---|---|---|
| `meridian_rw` | worker, orchestrator, `/api/admin/*` | Read and write |
| `meridian_ro` | `/api/explore/*`, MCP tools | Read; every transaction is also `SET TRANSACTION READ ONLY` |
| `meridian_guest` | `run_readonly_query`, shared read access | `SELECT` on the corpus and graph only |

Read-only is enforced by Postgres, not by the application, so a bug above the database
cannot write through an explore route.

**Identity.** Behind the Cloudflare tunnel, Cloudflare Access authenticates the person and
sends a signed JWT. `access.py` verifies it on every request against the team's published
keys and audience. An assertion that does not verify is refused; there is no
"anonymous but allowed" path. Without `CF_ACCESS_TEAM_DOMAIN` the middleware is not installed
and says so at start-up. **Admin refuses** unless identity is verified or
`MERIDIAN_ADMIN_ALLOW_ANONYMOUS` says the instance is not exposed. `/health` bypasses Access
and reports only liveness and the database connection.

**No CORS.** The web app reaches the API through the same origin (the Vite proxy in
development, the same host in production), so no cross-origin request needs permitting.

<a id="caching"></a>**Caching** (`cache.py`, `B-121`). Expensive, slow-moving reads (Gaps) are
kept in process with stale-while-revalidate: within the TTL the kept value is served, and after
it the kept value is still served while one refresh runs in the background, so no reader waits
but the first after a restart. The value carries `computed_at`, so a page can say how old it is.
It is neither shared between API processes nor persisted: the right size for one API behind
one front door.

**Raw files.** `/api/explore/sources/{id}/raw` serves a stored file only when
`MERIDIAN_SERVE_RAW` is on, because serving copies of third-party material is redistribution.
The path comes from the database row, never from the request.

## Design choices

- **The app is built by a factory** (`create_app`), not at import, so a test or a health check
  can construct it with different routers mounted without monkeypatching a module-level `app`.
- **No CORS, deliberately.** Permissive CORS "just in case" would make the API answerable from
  any page a reader has open, handing away what Access protects (§12.6).
- **The role boundary is one file.** `WriteSession` and `AdminAllowed` come from `deps.py`, so a
  route under `/api/admin` cannot get a session without the gate, and a handler under
  `/api/explore` that wanted to write would have to import across the prefix, visibly in review.
  `session_ro` also issues `SET TRANSACTION READ ONLY`, turning a mistaken write into an error
  at the statement rather than at commit; the role is what matters.
- **Admin is mounted unconditionally and gated per handler.** Mounting it conditionally would
  make a misconfiguration a 404, which reads as "not built yet". The gate is an opt-out
  (`MERIDIAN_ADMIN_ALLOW_ANONYMOUS`), the same shape as MCP's: open-unless-configured fails
  silently and in the wrong direction, and the person who forgets is deploying. Without
  identity or the opt-out, admin answers 503 with a message naming the fix.
- **The write session never commits for a handler.** A dependency that committed on the way out
  would commit whatever was flushed before a handler raised, half-applying a rejected request.
- **MCP is mounted on the same app**: the same corpus, read-only role and provenance, and
  §11.1b's one validation layer for every direction. Its streamable HTTP manager is entered for
  the app's life, since a mounted but unstarted sub-app accepts a connection and fails on the
  first message. It is mounted with `streamable_http_path="/"`, since the default path mounted
  at `/mcp` publishes `/mcp/mcp`, whose symptom ("Not Found" from `initialize`) reads as a
  protocol mismatch. `validate_token_resource` is turned on (the SDK leaves it off until 3.0).
  DNS-rebinding protection matters once behind a tunnel: without it a page on any origin can
  point a hostname at loopback and drive `/mcp` through the reader's own browser. The allowed
  hosts are a deployment fact read from the environment; unset means loopback only, so a tunnel
  added without setting them is refused rather than open.
- **One JSON log record per request, with a run id.** Docker's json-file driver makes stdout
  the only collection point, so uvicorn's plain-text access log is disabled in the Dockerfile and
  replaced; mixed formats would break anything parsing the stream. Under an API a "run" is a
  request, and `bind_run_id` (a per-task ContextVar) keeps concurrent requests' records apart.
- **Engines are disposed on shutdown**, since Postgres's `max_connections` is shared by every
  service and an abandoned pool spends the others' budget until reaped. Pool defaults keep the
  total under it; override per service in the environment. Engines are created lazily, so
  importing `db.py` (tests, Alembic, `--help`) opens no sockets.
- **`detail` is always a string** (`P2-18`). FastAPI returns a string from `HTTPException` and a
  list from validation; every client would otherwise re-pay that or render `[object Object]`.
  The structured form rides alongside as `errors`.
- **`/health` says nothing about the corpus.** It reports liveness and whether the read role
  reaches Postgres, because those fail separately and restarting fixes neither database nor
  credentials. Queue depth or crawl rates would describe the operator's research to anyone who
  can reach it; that detail is in the logs' health line (§12.5).

### Identity

`access.py` (`P3-08`) never trusts the `Cf-Access-Jwt-Assertion` header. A header is a string:
a direct port publish, a second ingress or a later reverse proxy turns "Cloudflare always sets
this" into "anyone can set this", and none looks like a security change when made. So every
assertion is verified against the team's keys and audience, on every request, and there is no
anonymous-but-allowed path for one that does not verify.

- **Team domain and audience: both or neither.** A domain alone would accept a token signed for
  any Access application on the team, including another service's.
- **Not configured means not installed**, with a line at start-up: demanding it on a loopback
  machine would push everyone into disabling it. The MCP token check does not depend on
  deployment shape and fails closed on its own.
- **Keys are fetched by a caching client** that refreshes as Cloudflare rotates them; one fetched
  once would fail weeks later for a reason nobody connects to a deploy.
- **Every failure is the same `None`**, as for MCP tokens: which check failed tells an
  unauthenticated caller how to get closer.
- `/health` bypasses Access because the watchdog cannot complete an SSO flow, and it discloses
  nothing.

The **guest role** (`P3-07`) exists because `meridian_ro` can read `agent_tokens`, which holds
every token hash, and so is the wrong role behind a query tool an external agent reaches.
Without `PG_GUEST_URL` the tools that need it are absent rather than failing at call time.

### Admin routes

`/api/admin/*` (`P6-13`) is the only surface that changes anything.

- **Nothing deletes, except saved views and pending seeds.** Topics archive by status, leaving
  every node, edge and tag they produced, so returning is a status change, not a re-crawl.
  Rejected gazetteer terms stay as tombstones, because the harvest re-reads the same documents
  and a deleted row comes back. A note is withdrawn, not deleted (`B-201`, ADR 0020): it leaves
  every list, panel, export and graph walk, keeps its row and links, and `…/restore` puts it
  back. A saved view holds no evidence and nothing depends on it, so it is deleted.
- **Gazetteer curation** (§5.6). Approving reports what the matcher will do: a row two others
  collide with is withheld from the `EntityRuler`, so "approved" alone could hide a term that
  never matches. The verdict is computed against the whole approved set (the colliding row is
  usually on another page; the table is hundreds of rows). The queue is ordered by
  `occurrence_count`, the curator's own triage, with `term_id` breaking ties so paging is stable.
  Approving clears a rejection, or the row would be approved and still read as thrown away by
  the harvest. Editing comes before deciding, because a harvested term arrives as `concept` with
  no jurisdiction, and yes/no alone would force "accept it filed wrongly" or "discard a real
  term". Bulk decisions are all or none and name every unknown id (`P6-28`).
- **Topics** (`P6-12`, §10). Every change is logged before it is applied, with actor and reason.
  An invalid change is refused with a 422 naming the bound, never clamped. `_steer` applies
  status first (so pausing and re-weighting together cannot set a share on a leaving topic), then
  bounds, then weight (judged against a ceiling raised in the same request). A new topic starts
  at its floor, since it has no sources yet (§10.2's "small repeat of cold start"). The
  add-topic preview (`P6-28`) runs the same `_steer` / `_add` and rolls back, so the arithmetic
  shown is the arithmetic that commits; nothing is logged, because nothing happened.
- **Fetch policy** (`P6-22`) is the one surface whose changes reach other people's servers.
  Only politeness, patience and render mode are editable. The SSRF guards
  (`block_private_addresses`, `block_cloud_metadata`, `allowed_schemes`, `require_https_final`,
  `block_mixed_dns`, `revalidate_each_redirect`) are never behind a form field, nor are
  `respect_robots` and `user_agent`: those are deployment decisions. Editing the global row
  needs `confirm`, since a client-side dialog is a promise, not a check. Edits are validated by
  building a `ResolvedPolicy`, so the same bounds apply (§2.6). Unblocking a domain clears status
  and counter together; either alone looks like the unblock did not work. Forgetting render
  learning (`P1-27`) clears the observation for a site that dropped its JavaScript shell today.
- **Saved views and annotations** are read under `/api/explore` and written here, because the
  prefixes split by mutation (§12.6): on a shared instance (`P3-06`) a guest can open the owner's
  views and notes but cannot add to them. That matters most for notes, the one layer that reads
  as the owner's own thinking. A view's filters are validated against `SearchFilters`, since a
  view that silently drops a filter shows results the reader believes are narrowed.
  Opening a view is its own call, because listing is not returning, and a read that wrote would
  cross the boundary. `produced_by` cannot be sent (extra keys are forbidden), so a request
  claiming authorship is refused at the boundary. Rewriting a corpus-derived entity is a 404:
  the graph is re-derived from chunks (§2.4), and a hand-edit there could not be explained.
- **The budget** (`P4-10`, `P4-13`) is where §16's caps are set. Nothing seeds a default cap:
  limits nobody chose would satisfy "caps before the first autonomous run" by accident. `PUT` on
  the singleton is a partial update: an omitted field is kept, an explicit `null` clears a cap
  (and stops runs). `exclude_unset` keeps those apart, here and in term edits.
- **Cold-start seeds** (`B-07`) stay editable while pending, because `config/seed_sources.yaml`
  is read once, before anyone knows what to put there. It is not a wizard or a gate: the crawl
  has started, and the window is between queuing and fetching. An added seed is
  `seed_source="user"` (consent, so `P4-12` allows its domain at once) but passes
  `check_seed_allowed` like a model's, since a `file:` URL is no safer typed by the operator.
  A seed can be removed only while `pending` and unclaimed: claiming is a lease (`P1-01`), and a
  seed mid-fetch is still `pending`. The 409 says which, because "I removed it" and "it had
  already run" are different beliefs.
- **The agent registry** answers with `unserved_tasks`: a task type no enabled row declares
  defers every run, and nothing about any one row looks wrong. Editing returns the whole
  registry, since disabling one row changes that answer for all. Only `enabled` is writable;
  models, endpoints and task types are deployment configuration.
- **Runs** are listed with counters, not a verdict: "successful" would hide the run that wrote
  nothing.
- **Steering from the map** (`P6-35`) writes through existing steering (a boost with an expiry,
  a search, a saved view), so it is reversible and logged, and refuses in words.
- **Asking the graph** is here because it writes a thread and spends tokens.
- **Possible duplicates** (`B-202`) are decided here: merge (reversible from the same row with
  `…/undo`) or keep apart. See [duplicates.md](duplicates.md).
- **The display time zone** (`B-145`, ADR 0009) is set with `PUT /settings/display-timezone`
  and read by everyone from `/api/explore/settings`. Stored times do not move.
- **MCP tokens** (`B-146`) are issued, listed and revoked under `/api/admin/tokens`, the same
  mechanism as `python -m api.tokens`; the secret is in the issuing response only. See
  [mcp.md](mcp.md).

### Explore routes

- **A hit carries its source inline**, and there is a route for the chunks around a hit, ordered
  by `chunk_index` (document order, unlike `/search`'s fused rank; separate routes keep the two
  kinds of paging apart). A source with no chunks returns an empty list, not 404: metadata-only
  is a resting state (§6.5). A source's record includes `extractor` and the OCR columns, since
  "no text" and "a scan nobody has OCR'd" are different answers.
- **An empty `q` returns an empty result, not 422**: an empty box is a UI's first render.
- **A chunk carries the novelty gate's verdict**: a filtered near-duplicate and a never-crawled
  page look identical from a result set.
- **The node panel shows superseded chunks**, the one place it does. Elsewhere a superseded chunk
  would misquote the page (`P1-32`); here it is the evidence a tag was derived from, and must stay
  followable. The panel carries the latest few notes, fewer than its chunks, so a heavily
  annotated node's attributes are not buried.
- **BibTeX export takes explicit ids**, not a query: a bibliography is what a person kept, and a
  result set depends on a ranking that moves. It is plain text, as a `.bib` file is. The notes
  export is capped by page, not by courtesy, because notes are the one thing crawling cannot
  recover, and §12.5's point is that the material can leave.
- **Raw files** are served only with `MERIDIAN_SERVE_RAW` (`grants.raw_files` is the per-guest
  version beneath it), and the path comes from the row: there is no user-supplied path to check.
- **Notifications** read the same rows as the digest (alerts are recorded before delivery,
  `P5-07`), so a deployment with no bot still sees them; filtered by type, not read state.
- **`/progress`** (`B-09`) makes the first hour of an empty production instance legible rather
  than shipping a demo corpus: a snapshot of a real crawl is third-party content (§14.2), and
  synthetic fixtures would look better than real extraction.
- **Crawl health** (`P6-25`) is a read, so it is here, not under Admin: Admin's gate is closed
  on exactly the half-configured deployments where someone watching an unattended crawl needs it.
- Handlers use `Annotated[...]` parameters, so defaults stay ordinary values and a handler is
  callable from a test.

## Operating it

- API routes are listed at `/docs` (FastAPI's OpenAPI page) on a development instance.
- After deploying, check `/health` and that an explore route returns data on the read-only
  role.

## Failure modes and traps

- `uv run pytest` without `.env.dev` exported fails API tests on `PG_RO_URL is not set`.
- The production API has no route to the internet. Anything it calls (an embedding service,
  a model server) must be on `internal` or reachable on `lan` with an nft rule.

## Tests

`tests/integration/test_explore_api.py`, `test_admin*.py`, `test_guest_role.py`,
`test_annotations.py`, `test_duplicates.py`, `test_display_settings.py`, `test_growth_api.py`;
`tests/unit/test_access.py`, `test_api_cache.py`.
