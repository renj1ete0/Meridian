# Shared read access — the corpus as something other people's models can read

**Status: specification. Nothing below is built.** It refines `P3-01`–`P3-05`
rather than replacing them, and adds the part those tasks do not cover: granting
access to **people who are not the operator**.

Two questions, deliberately answered by two different systems:

| Question | Answered by | Failure if you conflate them |
|---|---|---|
| *Who is at the door?* | Cloudflare Access — identity, SSO, service tokens | An application bug becomes an open door |
| *What may they read?* | Meridian — scoped tokens, a read-only database role | Anyone Cloudflare lets in reads everything |

Neither is sufficient alone, and the split is the design. Cloudflare decides
whether a request reaches the machine; Meridian decides what it may see once it
does. Revoking in one place leaves the other standing.

---

## 1. What already exists

More than it looks like:

- **`meridian_ro`**, a real Postgres role with SELECT and nothing else, created
  by `scripts/init-roles.sh` at first boot. The read surface's guarantee is
  therefore enforced by the database, not by the application remembering to be
  careful. `P0-18` is the reminder of why that matters: default privileges that
  silently denied reads were invisible to everything except a real Postgres.
- **`agent_tokens`** — `allowed_tools[]`, `rate_limit`, `expires_at`, `revoked`,
  hashed secret. §11.4's model, already in the schema.
- **`cloudflared`** in compose, and `api` published on loopback only. Nothing is
  exposed to the internet except through the tunnel — by topology, not config.

What does not exist: the API itself, the MCP server, and any notion of a
*person* who is not the operator.

---

## 2. The read pipeline

```
someone else's LLM  ──MCP──►  Cloudflare Access  ──tunnel──►  api (loopback)
                                     │                          │
                          identity + service tokens             │
                                                                ▼
                                                     verify Access JWT (§4.2)
                                                                │
                                                                ▼
                                                     resolve Meridian token
                                                     → allowed_tools, limits
                                                                │
                                                                ▼
                                                     MCP tools, all reads
                                                                │
                                                                ▼
                                                     PG_RO_URL — meridian_ro
                                                     SELECT and nothing else
```

Four gates, each of which is sufficient to stop a write on its own. That is the
point: a mistake in the tool layer is caught by the role, and a mistake in the
role is caught by the tool layer.

### 2.1 The tools

§11.6's read set, and only it: `search_chunks`, `get_source_metadata`,
`list_new_since`, and later `query_graph`, `get_node`, `get_neighbours`,
`coverage_report`, `list_contested`. No write tool is registered on a read
profile — not disabled, **not registered**, so there is nothing to bypass.

Every result carries provenance, because a retrieval surface that returns text
without its source is a RAG endpoint and this system's entire premise is that
provenance is not optional. A `search_chunks` hit returns the chunk, its source,
the source's tier, its date, and whether the chunk is marked a duplicate.

### 2.2 `run_readonly_query` needs a narrower role than `meridian_ro`

`P3-04` puts an escape hatch behind the read-only role with a statement timeout
and a row cap. That is right for the operator and **wrong for a guest**, because
`meridian_ro` can SELECT from every table — including `agent_tokens`, whose
`token_hash` column is the one secret in the schema, and `fetch_policy`, which
carries operational detail about how this crawler behaves.

So: a third role, `meridian_guest`, granted SELECT on the corpus and graph tables
and nothing else. Whether `run_readonly_query` is offered to guests at all is
§7's first open question; if it is, it runs as this role.

This is worth doing even if the answer is "no guests ever get SQL", because the
same grant list is what a future read-only replica or export would use.

---

## 3. Granting access to another person

The unit of sharing is a **grant**, not a token. A person may hold several
tokens (a browser session, an MCP client on a laptop, one on a server) and
revoking their access must revoke all of them at once, which a per-token model
cannot do.

```
grants
  grant_id, subject, subject_kind, profile,
  topics[], max_source_tier, raw_files,
  granted_at, expires_at, revoked, note
```

| Field | Why |
|---|---|
| `subject` | The Cloudflare Access identity — an email address, or a service-token client id |
| `subject_kind` | `person` · `service`. A person arrives via SSO; a machine via a service token, and they are audited differently |
| `profile` | `reader` · `analyst` · `operator`. A named set of tools, not a free-form list — a free-form list per person is how someone ends up with a write tool nobody remembers granting |
| `topics[]` | Empty means all. Sharing one topic's corpus without sharing the rest is the common case |
| `max_source_tier` | So a grant can exclude `informal` material without excluding the person |
| `raw_files` | Default **false**. See §5 |
| `expires_at` | Not nullable for `person` grants. An access grant with no end is a grant nobody revisits |

`agent_tokens` stays as it is and gains `grant_id`: the grant says who and what,
the token is one credential under it. Revoking the grant revokes every token
beneath it in one statement.

---

## 4. Cloudflare Access — the two kinds of visitor

### 4.1 A person

Cloudflare Access policy on the hostname, with an identity provider (Google,
GitHub, or one-time PIN to an allowlisted address — the last needs no IdP setup
and is enough to start). Access authenticates them and passes a signed JWT.

Add each person to the Access policy *and* create their grant. Two steps on
purpose: Cloudflare knowing who someone is has never been the same thing as
Meridian deciding what they may read.

### 4.2 A machine — the MCP client

An MCP client is not a browser and cannot complete an SSO redirect. Cloudflare
Access **service tokens** are the mechanism: a client id and secret sent as
`CF-Access-Client-Id` / `CF-Access-Client-Secret`, with an Access policy that
accepts that service token for this hostname.

So a third party's model presents two credentials:

| Credential | Issued by | Answers |
|---|---|---|
| Access service token | Cloudflare | May this request reach the machine? |
| Meridian bearer token | Meridian | What may it read once there? |

Deliberately two. A leaked Meridian token is useless without the Cloudflare
half, and a leaked service token reaches an API that will not answer it.

### 4.3 Verify the assertion — never trust the header

`Cf-Access-Jwt-Assertion` must be **cryptographically verified** against the
team's public keys, with the audience tag checked, on every request. A header is
a string, and an application that trusts it is one misconfiguration — a direct
port publish, a second ingress, a future reverse proxy — away from letting
anyone assert any identity by typing it.

`/health` is the exception and needs an Access **bypass** policy, or the
watchdog cannot reach it. Keep it saying nothing but whether the process is
alive; `P1-26` already established that the interesting health detail belongs on
the log line, not on an endpoint anyone can reach.

---

## 5. Two things a guest must not get by default

**Raw files.** The raw store holds copies of third-party material, kept under
§14.2 as a research archive with provenance. Serving those files to other people
is redistribution, and it is a different act from sharing what the corpus
*extracted*. `grants.raw_files` defaults to false; a guest gets chunks,
metadata, and the source URL — which is a citation, and is what a reader
actually needs.

**The operator's annotations.** §12.5 makes annotations first-class nodes and
predicts they become the highest-quality layer in the system, because they
reflect the operator's own thinking. They are also the most personal thing in
it. Guest profiles exclude annotation nodes unless a grant says otherwise, and
that should be a deliberate act rather than a default.

---

## 6. Rate limits, cost and audit

- **Rate limit per token**, from `agent_tokens.rate_limit`, enforced in the API.
  A guest's model in a retry loop is otherwise indistinguishable from a crawl.
- **Row and statement caps** on every tool, not just the SQL hatch.
- **Audit by grant, not by token.** One row per call: grant, tool, arguments,
  row count, duration. The question worth answering later is "what has this
  person's model been reading", and a per-token log cannot answer it once they
  have three clients.
- **Async for anything slow** (§11.5): `submit_job` / `get_job_status` /
  `get_job_result`. An MCP call that blocks on a heavy query times out at the
  client and leaves the work running with nobody to hand it to.

---

## 7. Open questions

1. **Does a guest get `run_readonly_query` at all?** It is the most useful tool
   for an unfamiliar model and the hardest to bound. §2.2's `meridian_guest`
   role makes it *safe* without making it *cheap* — a badly shaped query still
   costs the Pi. Leaning no, with the curated tools as the answer. ⚑ human
2. **Topic scoping: filter, or separate?** Filtering every tool by
   `grants.topics[]` is one predicate per query and is easy to get subtly wrong
   in a join. The alternative is a per-grant view schema. The second is more
   work and fails closed.
3. **Should a grant be able to see *absence*?** `coverage_report` and
   `list_contested` are the most valuable read tools, and they describe the
   shape of the operator's attention rather than the corpus. Possibly operator-
   and analyst-only.
4. **What happens to a guest's access when a source is retracted or a domain is
   blocked?** Nothing, currently — a chunk stays readable after its source is
   demoted. Probably correct, but it should be a decision.

---

## 8. Tasks

Refines the existing `P3-01`–`P3-05`; these are the additions.

| ID | Work | Depends on |
|---|---|---|
| `P3-06` | `grants` table, `agent_tokens.grant_id`, the three profiles as data. Revoking a grant revokes every token under it, with a test that proves it | `P3-03` |
| `P3-07` | `meridian_guest` role in `init-roles.sh` — SELECT on corpus and graph tables, nothing on `agent_tokens` or `fetch_policy`. A rejection test per excluded table, against a real Postgres | nothing |
| `P3-08` | Access JWT verification middleware: fetch team keys, verify signature and audience, map identity → grant. Reject an unverifiable assertion; never fall back to the header | `P3-05` |
| `P3-09` | Service-token path for MCP clients, and the operator runbook for issuing one — the Cloudflare side and the Meridian side are two steps and both are easy to half-do | `P3-08` |
| `P3-10` | Grant scoping applied in the tool layer: `topics[]`, `max_source_tier`, `raw_files`, annotation exclusion. Tests that a scoped grant cannot see what it was not granted | `P3-06`, `P3-02` |
| `P3-11` | Per-grant audit log and per-token rate limiting | `P3-06` |

`P3-07` is the one to do first and can be done now — it needs no API, no MCP
server and no Cloudflare account. It is also the gate that makes every later
mistake survivable, which is the same argument that put `P1-38` first in
[external-acquisition.md](external-acquisition.md).
