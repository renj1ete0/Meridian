# MCP surface

The API serves a read-only [Model Context Protocol](https://modelcontextprotocol.io) surface at
`/mcp`. External assistants (Claude Desktop or Claude Code, Gemini CLI, or any MCP client)
connect to it and answer questions from the corpus with citations, using their own model.
Meridian holds the corpus and its provenance; it generates nothing here.

- **Code:** `services/api/api/mcp/server.py` (tools and instructions), `auth.py` (token
  verification), `main.py` (mounting, transport security), `routes/tokens.py` (the Admin
  screen's API), `web/src/admin/AssistantAccessPanel.tsx`;
  `packages/meridian_core/meridian_core/tokens.py`, `grants.py`, `readonly_query.py`
- **Tasks:** `P3-01`–`P3-10`, `B-138`, `B-139`, `B-146`
- **Decisions:** [ADR 0003](../adr/0003-external-assistants-over-mcp.md),
  [ADR 0011](../adr/0011-tokens-may-be-set-not-to-expire.md)

## How it works

**Tools** (all on the read-only database role):

| Tool | Returns |
|---|---|
| `search_chunks` | Hybrid search, each hit with its citation and the caveats an assistant must keep |
| `get_source_metadata` | One source: title, publisher, tier, date, URL |
| `list_new_since` | Passages added after a mark, for an assistant keeping up with the corpus |
| `corpus_overview` | Counts and topics |
| `run_readonly_query` | Arbitrary `SELECT`, with a statement timeout and row cap, on the guest role |
| `find_nodes` | Graph nodes by name or alias, to get an `entity_id` |
| `get_node` | A node's attributes, evidence and contested pairs, and its neighbours ranked by support |
| `find_route` | How two subjects connect, hop by hop, each `cited` or `similar`, with the claims-only answer beside it |
| `term_neighbourhood` | What passages state a link to, and what only reads alike, for a term |
| `list_areas`, `get_area` | The corpus map: one level of areas, or one area's stats and typical passages |
| `list_gaps` | What the corpus cannot answer yet, with reasons |
| `list_contested` | Claims sources disagree about, both sides kept |
| `corpus_growth` | How the corpus grew, day by day, for 7 days, 30 days or all time, by topic |

Each site tool calls the function its page calls, so an assistant and the site cannot
disagree; a test compares them. `search_chunks` embeds the query with the same service as
Find (`B-139`), so assistants get hybrid search where the site does.

The server's **instructions** are written for a model to act on, and the same warnings ride
on every result: a word-matching search that found nothing does not mean the corpus lacks the
topic; a source tier is not a credibility score; a claim must be reported with its citation.

**Tokens** (`tokens.py`). Each token has an explicit list of tools, an expiry, and a revocation
switch. Only a SHA-256 hash is stored (tokens are 256 random bits, so a slow hash would add
latency and no protection). A NULL tool list grants **nothing**, not everything. The transport
verifies the token; each tool then checks that this token is scoped to it.

**Profiles** are fixed sets of tools, held to the tools the server defines by a test
(`B-138`): `reader` (search, source details, what is new, overview), `analyst` and `operator`
(both add read-only SQL).

**Grants** (`grants.py`, `P3-06`). Access for someone other than the operator is granted to a
*person*, with a named profile (`reader`, `analyst`, `operator`), never a free-form tool list.
Revoking the person revokes all their tokens. Raw files and topics outside the grant are off
by default.

**Anonymous access** is an explicit opt-out (`MERIDIAN_MCP_ALLOW_ANONYMOUS`) for a loopback
development machine only. Without it, and without a token, every tool refuses.

**Read-only SQL** (`P3-04`). The guest role can `SELECT` the corpus and the graph, and nothing
else (no token hashes, no fetch policy). The statement timeout is what makes it safe to
expose. Every query is logged, because the queries assistants reach for show which curated
tools to build next.

## Connecting an assistant

Step by step in [guides/connecting-an-assistant.md](../guides/connecting-an-assistant.md).
In short:

- `web` proxies `/mcp` to the API (unbuffered, for server-sent events). The tunnel reaches it,
  and `deploy/lan/publish-web.yml` publishes it on the server or the LAN.
- **Admin → Assistant access** issues a token per device, scoped to a profile, 90 days by
  default or never (flagged "no expiry", ADR 0011). The secret is shown once, with setup for
  Claude Code, Gemini CLI and other clients, built from the address the browser is on. The
  table lists tokens (never secrets) and revokes them. It warns harder when that address is
  public. Behind it: `GET/POST /api/admin/tokens`, `POST /api/admin/tokens/{id}/revoke`.
- `python -m api.tokens issue|list|revoke` (in the API container) does the same from a shell.
- Tokens verify with no further configuration. `MERIDIAN_MCP_ISSUER_URL` and
  `MERIDIAN_MCP_RESOURCE_URL` default to local placeholders, and only matter for clients that
  discover authentication through OAuth metadata (claude.ai through the tunnel).
- The transport answers only the host names in `MERIDIAN_MCP_ALLOWED_HOSTS` (localhost when
  unset).

## Design choices

- **No write tools.** Writing the graph belongs to the orchestrator, behind validation
  (§11.6); no profile carries a write tool.
- **Two checks, not one.** The transport authenticates; the tool authorises. Scope cannot live
  only at the transport, because which tool was asked for is the thing being authorised.
- **DNS-rebinding protection** through `MERIDIAN_MCP_ALLOWED_HOSTS` and
  `MERIDIAN_MCP_ALLOWED_ORIGINS`.

## Tests

`tests/integration/test_mcp.py`, `test_mcp_auth.py`, `test_tokens.py`, `test_grants.py`,
`test_guest_role.py`, `test_readonly_query.py`, `test_admin_tokens.py` (the screen's API:
the secret returned once and only its hash stored, never-expiring tokens flagged, revoke, and
holder names refused). Web: `web/tests/admin-access.test.tsx`.
