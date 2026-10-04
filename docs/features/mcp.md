# MCP surface

The API serves a read-only [Model Context Protocol](https://modelcontextprotocol.io) surface at
`/mcp`. External assistants (Claude Desktop or Claude Code, Gemini CLI, or any MCP client)
connect to it and answer questions from the corpus with citations, using their own model.
Meridian holds the corpus and its provenance; it generates nothing here.

- **Code:** `services/api/api/mcp/server.py` (tools and instructions), `auth.py` (token
  verification), `main.py` (mounting, transport security);
  `packages/meridian_core/meridian_core/tokens.py`, `grants.py`, `readonly_query.py`
- **Tasks:** `P3-01`–`P3-10`, `B-138`, `B-139`
- **Decisions:** [ADR 0003](../adr/0003-external-assistants-over-mcp.md)

## How it works

**Tools** (all on the read-only database role):

| Tool | Returns |
|---|---|
| `search_chunks` | Hybrid search, each hit with its citation and the caveats an assistant must keep |
| `get_source_metadata` | One source: title, publisher, tier, date, URL |
| `list_new_since` | Passages added after a mark, for an assistant keeping up with the corpus |
| `corpus_overview` | Counts and topics |
| `run_readonly_query` | Arbitrary `SELECT`, with a statement timeout and row cap, on the guest role |

The server's **instructions** are written for a model to act on, and the same warnings ride
on every result: a word-matching search that found nothing does not mean the corpus lacks the
topic; a source tier is not a credibility score; a claim must be reported with its citation.

**Tokens** (`tokens.py`). Each token has an explicit list of tools, an expiry, and a revocation
switch. Only a SHA-256 hash is stored (tokens are 256 random bits, so a slow hash would add
latency and no protection). A NULL tool list grants **nothing**, not everything. The transport
verifies the token; each tool then checks that this token is scoped to it.

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

Issuing a token needs no public address. What decides who can use a token is where `/mcp` can
be reached (ADR 0003):

| Assistant runs | URL | Needs |
|---|---|---|
| On the server | `http://localhost:<api-port>/mcp` | Nothing |
| On the LAN or a VPN | `http://<server>:<api-port>/mcp` | The port reachable on that network |
| claude.ai web or mobile | `https://<tunnel-host>/mcp` | The `cloudflared` tunnel |

A command and an Admin screen to issue and revoke tokens, and setup notes per client, are
`B-138`. Until then, tokens are issued with `meridian_core.tokens.issue_token` from a Python
shell.

## Design choices

- **No write tools.** Writing the graph belongs to the orchestrator, behind validation
  (§11.6); no profile carries a write tool.
- **Two checks, not one.** The transport authenticates; the tool authorises. Scope cannot live
  only at the transport, because which tool was asked for is the thing being authorised.
- **DNS-rebinding protection** through `MERIDIAN_MCP_ALLOWED_HOSTS` and
  `MERIDIAN_MCP_ALLOWED_ORIGINS`.

## Known gaps

- **Profiles and tools have drifted.** `grants.PROFILE_TOOLS` names `get_chunk`,
  `export_markdown` and `export_bibtex`, which the server does not define, and no profile
  includes `list_new_since` or `corpus_overview`. To be fixed with a drift test in `B-138`.
- Graph, area, gap and growth tools are planned (`B-139`).

## Tests

`tests/integration/test_mcp.py`, `test_mcp_auth.py`, `test_tokens.py`, `test_grants.py`,
`test_guest_role.py`, `test_readonly_query.py`.
