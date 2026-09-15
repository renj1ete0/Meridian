# External acquisition — consigning what the crawler cannot fetch

**Status: specification. Nothing below is built.** Task IDs at the end.

A URL the crawler refuses or cannot retrieve is currently the end of the line.
This specifies the handoff that makes it a question instead: Meridian publishes
the URLs it failed on, an external actor retrieves them by whatever means it
has, and hands the content back to be processed exactly as if the crawler had
fetched it — except that the corpus records, permanently, that it did not.

The external actor is a **black box** on purpose. It may be another container on
the same host, a service elsewhere, or a person with a browser and a file
upload. Meridian specifies the contract and knows nothing else about it.

---

## 1. Why this is not just "retry harder"

Three classes of thing sit behind the failures worth consigning, and none of
them are fixed by asking again:

- **A bot wall.** The page exists and is public; the origin will not serve it to
  a datacentre IP, a non-browser client, or an unfamiliar user agent.
- **A session.** The content is behind a login the operator legitimately holds —
  an institutional subscription, a paid account, a library proxy.
- **A format or a size** Meridian deliberately refuses, that something else can
  reduce to text.

The distinction that matters is between *cannot* and *must not*. This pipeline
exists for the first. §3 is about keeping it out of the second.

---

## 2. The shape

```
queue row abandons
   │
   ▼
eligibility (§3) ──no──► stays abandoned. End.
   │ yes
   ▼
consignment created, status `offered`
   │
   ▼
GET /api/acquisition/consignments     ← external actor claims a batch (lease)
   │                                    status `claimed`
   ▼
          [ black box: fetches, scrapes, converts, or a person saves a file ]
   │
   ▼
POST /api/acquisition/consignments/{id}   ← content, or "nothing", or "cannot"
   │
   ▼
admission (§5): size, type, checksum, netguard-equivalent, injection screen
   │
   ▼
the ordinary path: rawstore → extract → chunk → upsert_source → frontier
   │                                            (acquired_via = 'consignment')
   ▼
queue row settles `fetched`, then the normal pipeline takes it to `done`
```

The last two steps are the point of the design: **nothing downstream of
admission is new.** Consigned content is stored, extracted, chunked, embedded,
novelty-judged and frontier-expanded by the same code as everything else. A
second pipeline would drift from the first and would eventually differ in a way
nobody chose.

---

## 3. Eligibility — a narrow allowlist, not "everything that failed"

`queue_disposition()` already returns `abandon` for eight outcomes, and they are
not interchangeable. Eligibility is an explicit allowlist and the default is
**not eligible**.

| Outcome | Eligible | Why |
|---|---|---|
| `http_error` 401 / 402 / 403 / 451 | **yes** | A refusal to serve *this client*. The content exists |
| `content_type_rejected` | **yes** | Meridian will not read it; something else may reduce it to text |
| `parse_error` | **yes** | The bytes arrived and could not be made sense of |
| `too_many_redirects` | **yes** | Often a session or consent wall a browser resolves |
| `too_large` | **operator opt-in** | The consigned copy will be large too. Needs a size decision, not a default |
| `http_error` 404 / 410 | no | A correct answer about a URL that is gone |
| `http_error` 5xx / 429 | no | Already retried; the origin is having a bad minute |
| `timeout`, `connection_error` | no | Same — these retry, they do not abandon |
| `decompression_bomb` | no | Someone was hostile. Do not hand it on |
| `blocked` | no | Policy said no. Consigning it is a way around the operator's own decision |
| **`robots_denied`** | **never** | See below |
| **`unsafe_target`** | **never** | See below |

### 3.1 The two that are never eligible, and why the line is absolute

**`robots_denied`.** The site asked not to be crawled. Routing that request
through a third party so the refusal does not apply is not a technical
workaround, it is the same crawl with the conduct removed. §14.2 commits this
system to identifying itself and honouring robots; a consignment pipeline that
can launder a robots refusal makes that commitment decorative. This must be
unreachable in code, not merely absent from a config default — the eligibility
check refuses it before anything else runs, and a test asserts the refusal.

**`unsafe_target`.** `netguard` refused this address because it resolved to a
private range, a cloud metadata endpoint, or a scheme this crawler does not
speak. Publishing it in a consignment feed asks an external service to fetch
the operator's own LAN and post the result back — an SSRF with a cooperating
victim. `P1-20` and `P1-24` exist to make this impossible; consignment must not
reopen it.

A single function owns both, and it lives in `meridian_core` rather than in the
API so the worker, Admin and any future surface answer the question identically.

### 3.2 What else gates a consignment

- **Per-domain opt-in.** `fetch_policy.consignment_allowed`, default false.
  Consignment is a relationship with a source, not a blanket capability, and
  the operator decides it per domain the way they decide every other fetch
  policy.
- **An attempt cap.** `queue.consignment_attempts`, capped like `max_retries`.
  Content that comes back and fails admission twice is not consigned a third
  time, or a bad extractor becomes an infinite loop with an external bill.
- **A ceiling on open consignments**, so a drained crawl cannot publish ten
  thousand URLs at once to a service that charges per fetch.

---

## 4. The API

A third surface. `/api/explore/*` is read-only and `/api/admin/*` is the
operator's; this is neither — it is written by a non-human client that must not
have either surface's reach. `/api/acquisition/*` gets its own scoped token
(§11.4) whose `allowed_tools` cover exactly these three routes.

Human-usable is a requirement, not a nicety: the fallback for a source nothing
automates is a person with a browser. Plain REST, plain JSON, and an upload that
works from `curl` or a file picker.

### 4.1 Claim a batch

```http
GET /api/acquisition/consignments?limit=20&domain=example.org
Authorization: Bearer <consignment token>
```

```json
{
  "consignments": [
    {
      "consignment_id": "c_01J...",
      "url": "https://example.org/report/2026",
      "reason": "http_error",
      "status_code": 403,
      "media_type_expected": "text/html",
      "topic": "…",
      "attempted_at": "2026-09-14T22:10:04Z",
      "attempts": 3,
      "lease_expires_at": "2026-09-15T00:10:04Z"
    }
  ]
}
```

A lease, not a status flip — the same model as the queue, for the same reason
(`P1-01`). An actor that dies mid-job releases its work by expiry rather than
stranding it. **Do not invent a second lease mechanism**; reuse
`claim_next()`'s.

What the payload deliberately does **not** contain: any credential, any internal
hostname, any cookie, and any tier or priority the external actor could use to
influence where the result lands.

### 4.2 Hand content back

```http
POST /api/acquisition/consignments/{id}
Content-Type: multipart/form-data
```

| Part | Meaning |
|---|---|
| `outcome` | `supplied` · `unavailable` · `failed` |
| `content` | The bytes, when `supplied` |
| `media_type` | What they are |
| `final_url` | Where it was actually retrieved from, after redirects |
| `note` | Free text, for a person to say what they did |

**Three outcomes, and they must stay three.** This trap has now cost this
codebase two releases — the DOI chain collapsed "nobody answered" into "there is
no copy" and lost papers (`v0.28.0`), and the same shape is here:

| `outcome` | Meaning | Queue settles |
|---|---|---|
| `supplied` | Here is the content | `fetched` → the ordinary pipeline |
| `unavailable` | I looked. It genuinely is not obtainable | `done` — an answer |
| `failed` | I could not answer | back to `abandoned`, re-consignable until the cap |

Idempotent on `consignment_id`: a retried upload after a dropped connection must
not produce two sources.

### 4.3 Status

```http
GET /api/acquisition/consignments/{id}
```

So a person can see whether what they uploaded was admitted, and why not if it
was not. An upload that is silently rejected teaches nobody anything.

---

## 5. Admission — the returned bytes are the least trusted input in the system

Everything else in the corpus was fetched by code that validated the address,
pinned it, checked the hop chain and capped the response. This content was
fetched by something else, and arrives over a channel that says it is the
answer. Treat the claim as untrusted, not the network path.

Admission, in order, before anything is stored:

1. **The URL is not the actor's choice.** Content is attributed to the URL that
   was consigned. `final_url` is *recorded*, and if it is off-origin the
   consignment is admitted against the original URL or refused — never silently
   re-pointed. Otherwise the feed becomes a way to write arbitrary content into
   the corpus under a trusted domain's name.
2. **Re-check eligibility.** The queue row may have changed since the offer;
   the domain may have been blocked in the meantime.
3. **Size cap** — the API's own, independent of `too_large`.
4. **Media type** against the allowlist, taken from the bytes as well as the
   declared header.
5. **Checksum**, recorded like any other fetch.
6. **The injection screen** (`P1-23`), unconditionally. This is the single most
   likely place in the whole system for prompt injection to arrive, because it
   is the only ingress where an external party chooses the bytes.
7. Only then: `rawstore.store()` and the ordinary extract/chunk path.

A consigned source may **never** be assigned a tier higher than the crawler
would have assigned the same domain, and may never overwrite a crawler-fetched
source at a higher tier — the §11.12 "quality only moves up automatically"
invariant, applied to acquisition.

---

## 6. Provenance

The corpus must be able to answer "did we fetch this, or did someone hand it to
us" for every row, forever, without a join through a log that gets pruned.

- `sources.acquired_via` — `crawler` · `consignment`. Not nullable, defaulted to
  `crawler` on backfill.
- `sources.acquired_by` — which consignment token supplied it. A subscription
  copy and a scraped copy are different things and the difference outlives the
  agent that produced either.
- A new `fetch_outcome` value, `external_supplied`, so `fetch_attempts` records
  the consignment as an attempt rather than showing a gap between a 403 and a
  source that exists.
- `consignments` as its own table: the lease, the actor, the outcome, the
  timings and the admission verdict. The queue row is about *whether to fetch a
  URL*; a consignment is about *who was asked and what they said*, and folding
  the second into the first is how the queue ends up with columns that are null
  for 99% of rows.

**Every extraction downstream inherits this.** A chunk from a consigned source
is still a chunk, and §2 principle 3's provenance chain — edge → chunk → source
→ raw file — gains one link that says the file arrived rather than being
fetched. Anything that later cites it can therefore say so.

---

## 7. What can go wrong, and what is done about it

| Risk | Mitigation |
|---|---|
| Robots laundering | §3.1. Refused in `meridian_core`, with a test |
| SSRF through a cooperating third party | §3.1. `unsafe_target` never eligible |
| Corpus poisoning via `final_url` | §5.1. Content is attributed to the consigned URL or refused |
| Prompt injection | §5.6. The screen runs unconditionally on this path |
| Infinite consignment loop | §3.2. `consignment_attempts` cap |
| Unbounded external cost | §3.2. Ceiling on open consignments; per-domain opt-in |
| The black box stops answering | Lease expiry returns the row. A health line word: `consignment: configured \| stale \| absent` |
| Silent success with garbage content | Admission verdict is recorded and readable (§4.3); a consignment whose content extracted to zero chunks is `failed`, not `supplied` |

The health line entry matters for the same reason `browser:` and `search:` do.
An external actor that quietly stopped collecting its feed three weeks ago looks
exactly like a crawl with no eligible failures, and one of those is fine.

---

## 8. Open questions

1. **Does the operator want per-domain opt-in, or a global default with a
   blocklist?** Specified as opt-in above because it is the safer default and
   because consignment usually reflects a real relationship with a source. It is
   also more work per domain. ⚑ human
2. **Is `too_large` ever worth consigning?** The returned copy is large too, and
   the size limit exists for a reason. Left as opt-in rather than decided.
3. **Should a person's upload carry a different `acquired_by` than a
   container's?** Probably — a hand-checked copy is better evidence than an
   automated one, and the tier rules in §5 might eventually want to know. Not
   specified; `acquired_by` is free enough to carry it later.
4. **Does the feed expose `topic`?** It is included above so an external actor
   can prioritise, but it is also the only field that leaks anything about what
   this system is *for*. Drop it if the black box is not trusted with that.

---

## 9. Tasks

| ID | Work | Depends on |
|---|---|---|
| `P1-38` | `consignment_eligible()` in `meridian_core` — the §3 allowlist, the two absolute refusals, and the tests that pin them. No API, no table | nothing |
| `P1-39` | Schema: `consignments` table, `sources.acquired_via` / `acquired_by`, `queue.consignment_attempts`, `fetch_policy.consignment_allowed`, `external_supplied` on `FETCH_OUTCOME`. Hand-written CHECK migration (`P0-21`) | `P1-38` |
| `P1-40` | Admission (§5) as a function on the worker's side of the boundary, reusing rawstore/extract/chunk unchanged | `P1-39` |
| `P1-41` | `/api/acquisition/*` — the three routes, the lease, the scoped token | `P1-40`, and the API service existing at all (`P2-07`) |
| `P1-42` | Health line word, and Admin visibility of open consignments | `P1-41` |

`P1-38` is deliberately first and deliberately standalone. The eligibility rule
is the part of this with a security argument behind it, it needs neither the
table nor the API, and it is the piece worth having correct and tested before
anything can call it.
