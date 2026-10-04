# API and access

One FastAPI service serves the reader (`/api/explore/*`), the control surface
(`/api/admin/*`), the MCP surface (`/mcp`) and `/health`. **The route prefix is the role
boundary**: explore routes get a read-only database session, admin routes get a writable one
behind an identity gate, and nothing under `/api/explore` can open a writable session.

- **Code:** `services/api/api/main.py`, `deps.py`, `access.py`, `cache.py`,
  `routes/explore.py`, `routes/admin.py`, `routes/graph.py`, `routes/gaps.py`,
  `routes/connect.py`, `routes/neighbourhood.py`, `routes/steering_proposals.py`;
  `packages/meridian_core/meridian_core/db.py`
- **Tasks:** `P2-07`, `P3-07`, `P3-08`, `P6-13`, `B-121`

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

**Caching** (`cache.py`, `B-121`). Expensive, slow-moving reads (Gaps) are kept in process
with stale-while-revalidate: within the TTL the kept value is served, and after it the kept
value is still served while one refresh runs in the background.

**Raw files.** `/api/explore/sources/{id}/raw` serves a stored file only when
`MERIDIAN_SERVE_RAW` is on, because serving copies of third-party material is redistribution.
The path comes from the database row, never from the request.

## Operating it

- API routes are listed at `/docs` (FastAPI's OpenAPI page) on a development instance.
- After deploying, check `/health` and that an explore route returns data on the read-only
  role.

## Failure modes and traps

- `uv run pytest` without `.env.dev` exported fails API tests on `PG_RO_URL is not set`.
- The production API has no route to the internet. Anything it calls (an embedding service,
  a model server) must be on `internal` or reachable on `lan` with an nft rule.

## Tests

`tests/integration/test_explore_api.py`, `test_admin*.py`, `test_guest_role.py`;
`tests/unit/test_access.py`, `test_api_cache.py`.
