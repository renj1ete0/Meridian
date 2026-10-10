# MCP surface

The API serves a read-only [Model Context Protocol](https://modelcontextprotocol.io) surface at
`/mcp`. External assistants (Claude Desktop or Claude Code, Gemini CLI, or any MCP client)
connect to it and answer questions from the corpus with citations, using their own model.
Meridian holds the corpus and its provenance; it generates nothing here.

- **Code:** `services/api/api/mcp/server.py` (tools and instructions); in `services/api/api/`,
  `auth.py` (token verification), `tokens.py` (the command), `main.py` (mounting, transport
  security), `routes/tokens.py` (the Admin screen's API); `web/src/admin/AssistantAccessPanel.tsx`;
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
- **The warnings ride on every result**, not only in the connection-time instructions: an
  assistant summarising one tool call will not go back and re-read them. A degraded search's
  wording tells the model to retry with other words, since a flag alone will not.
- **A tool that would always fail is not registered.** `run_readonly_query` exists only when
  a guest connection is configured: the tool list is the model's whole view of what it can
  do, and it would spend a turn finding out.
- **The tool docstrings are the descriptions clients read**, so they are written to the
  model, and changing one changes what assistants do.

### What reaches a model

`search_chunks` returns cleared material only (`P4-14`, §11.8): this is the path from a fetched
page into someone's prompt, the one screening exists to guard, and the model cannot ask
otherwise. The operator's own search does not set it, so they can see what was quarantined and
a false positive stays visible.

Results come back twice. `results` is structured, for a client that renders citations. `framed`
is the same text inside a random fence (`P4-06`), to paste into a prompt: a model on the other
end holds tools, and a client that concatenated `results` itself would put scraped text in
instruction position.

`list_new_since` walks the corpus by `chunk_id` (§6.3's mark, §11.1a): ids are monotonic, so
"everything after N" cannot skip a row that arrived mid-read or return one twice, and the path
needs no query and no embedder.

**It hands over what search would, and nothing else** (`B-188`). Until `v0.166.1` it filtered only
copies and superseded passages, so an assistant walking forward was given quarantined and
unscreened text that `search_chunks` refused (§2.5: only `cleared` reaches a model), plus
junk-tier passages. Measured on the live corpus: 7,005 quarantined and about 78,900 unscreened
passages were reachable. Both paths now take their predicate from one definition,
`readable_passage_conditions()` in `meridian_core.search`, and a test reads the server's source
and fails if a tool selects passages without it. One consequence of walking by id: a passage
still unscreened when the walker passes it is not handed over later, when its source clears.
The walker sees what was readable as it went by; a search finds the rest.

`corpus_overview` is §12.5's "absence is visible" as the first
thing an assistant can check: thirty passages and thirty thousand support very different claims.

### Authentication

- **Anonymous access is an opt-out, not a default.** "Open unless configured" is one forgotten
  variable away from publishing the corpus, and the person who forgets is deploying, not reading
  the code. Development sets the opt-out in `.env.dev`, where it is visible and local.
- **Two checks.** §11.4's point of a scope is that a session holds read tools while only the
  orchestrator's profile would carry writes; once a request is authenticated, which tool was
  asked for is the only thing that separates them, so the tool checks.
- The verifier reports `allowed_tools` as the SDK's scopes, so the SDK and `require_tool` read
  one source. It stamps this server as each token's resource, which turns on
  `validate_token_resource`: an otherwise valid token minted for another service on the same
  issuer is refused, as an Access assertion is checked against its audience (`P3-08`).
- A refusal is a `ToolError`, which the client receives with its message; any other exception
  reads as "error executing tool". The message names the missing tool, which the caller may
  ask its operator to grant, and says nothing about the credential.

### Tokens: design

<a id="tokens-design"></a>

- **Secrets are never compared.** The presented token is hashed and the hash looked up, so no
  branch's duration depends on how much of a token was right, and a log line that included a
  row would include a hash. Issuing logs the agent, never the secret or the hash.
- **SHA-256, not bcrypt.** Slow hashes defend low-entropy human choices. These are 256 bits of
  `secrets.token_urlsafe`; a slow hash would add latency to every request and no protection.
  The scheme is named in the stored value, so changing it is a migration.
- **A NULL tool list grants nothing**, the same argument as the guest role's lack of default
  privileges (`P3-07`): forgetting to grant gives a caller who says it cannot, which gets fixed;
  forgetting to restrict gives one who can do everything and does not mention it. `issue_token`
  takes `allowed_tools` with no default for the same reason.
- **Every rejection is the same `None`.** Telling an unauthenticated caller whether a token is
  unknown, revoked or expired confirms that it exists or existed. The reason is logged instead.

### Grants: design

<a id="grants-design"></a>

- **The unit is a person.** Someone given access holds several tokens (a browser, a laptop
  client, a server), and revoking access revokes them all in one transaction; chasing them one
  at a time is how the missed one stays working. Revoked tokens are marked, not deleted, so the
  audit log can still say whose credential made a call.
- **A profile, never a tool list** (§3). A free-form list is how someone ends up holding a tool
  nobody remembers granting. Profiles are defined in code, so adding a tool is reviewed.
  `operator` is not "everything": a grant is for reading someone else's corpus (§2.1).
- **Annotations only with `operator`, and no column for it.** Annotations are the operator's own
  thinking, the most personal layer (§12.5). A per-grant flag would be a checkbox someone ticks
  while setting up a colleague.
- **Filters intersect.** A guest may narrow further, and nothing they send widens the grant.
  `cleared_only` is forced on, as for the MCP surface.
- **Raw files need the grant and the deployment** (`MERIDIAN_SERVE_RAW`). The raw store is a
  research archive of third-party material (§14.2); serving it is redistribution, unlike sharing
  passages, metadata and the URL, which is a citation. A grant cannot overrule the operator's
  decision about their instance.
- **The audit is per grant and records refusals.** "What has this person's model been reading"
  cannot be answered per token once they hold three clients, and repeated refusals are the more
  interesting signal. Writing it never raises: a failed audit row is a smaller problem than a
  guest's working query failing.
- **Rate limits are per token.** What is limited is a client in a retry loop, which looks like a
  crawl (§6); limiting the grant would let one laptop silence the same person's phone.
  `limit=None` means no limit, unlike a budget: a throttle on something already authorised is a
  decision made at issue, while a budget with no cap is a decision nobody made. Refused calls do
  not count against it, or one misconfiguration becomes two.

### Read-only SQL: design

<a id="read-only-sql-design"></a>

§12.4: "Watch which queries the agent writes there — those are the next curated tools." That is
why every query is logged, successful or not.

- **The role is the enforcement.** `meridian_guest` (`P3-07`) cannot read `agent_tokens`, whose
  hash is the schema's one secret, or `fetch_policy`; arbitrary SQL cannot talk past a privilege
  it does not hold.
- **The timeout is the second line**: the role does nothing about a legal cartesian join across
  the corpus. It is `SET LOCAL` per transaction, so a bad query costs seconds and the setting
  cannot leak onto a pooled connection. It surfaces as `DBAPIError`, the parent of
  `DatabaseError`; catching the narrower type let the failure this design creates on purpose
  escape as a stack trace.
- **Textual checks are a courtesy**: a write verb or several statements are refused with a clear
  message rather than a Postgres permission error. "No semicolon except a trailing one" is blunt,
  since handling literals would make it a parser, and refuses a legal query far less often than a
  stacked one. A timeout and a missing privilege are translated, since neither reads as "you
  asked for something you may not have".
- **The session is passed in**, a guest session the caller owns, so a test cannot accidentally
  run on a privileged one.

## Tests

`tests/integration/test_mcp.py`, `test_mcp_auth.py`, `test_tokens.py`, `test_grants.py`,
`test_guest_role.py`, `test_readonly_query.py`, `test_admin_tokens.py` (the screen's API:
the secret returned once and only its hash stored, never-expiring tokens flagged, revoke, and
holder names refused). Web: `web/tests/admin-access.test.tsx`.
