# The document connector

The crawl finds papers it cannot retrieve: a reference list names a DOI, and no provider has
a legal open-access copy. The document connector is a small authenticated HTTP service that
lists those papers, and accepts documents back — the paper for a DOI, or any other document
with a name and a description. A handed-in document enters the corpus exactly as a crawled
one would: the worker extracts it, chunks it, and stores it as a `sources` row with its raw
file, and every later pass (embedding, topics, synthesis) treats it like any other source.

It is meant for a person or a tool of theirs (a reference manager, a library proxy run by
hand, another agent) that can obtain documents the crawl legally cannot.

- **Code:** `services/api/api/connector.py` (the app), `packages/meridian_core/meridian_core/uploads.py`
  (validation, the row, the wanted list), `services/worker/worker/main.py` (`_process_upload`),
  `packages/meridian_core/meridian_core/doi.py` (the shared DOI normaliser)
- **Tasks:** `B-264`

## How it works

```
GET  /connect/v1/wanted      ◄── queue: doi rows settled "no_open_access_copy"
                                  minus DOIs the corpus already holds with text
POST /connect/v1/documents   ──► inbox file + uploads row + queue row (task_type upload)
                                       │
                         worker claims │  read inbox file → extract → _keep → sources + chunks
                                       ▼
GET  /connect/v1/documents/{id}  ◄── uploads.status: queued → done (source_id) | failed (error)
```

### The service

`connector` in `docker-compose.yml` is the API image running `uvicorn api.connector:app`, a
separate FastAPI app that mounts only `/connect/v1/*` and `/health`. Explore, Admin and MCP
are not on its port. It is behind the compose profile `connector`, so it runs only when the
operator enables it (`COMPOSE_PROFILES=connector`), and is published on
`${CONNECTOR_BIND:-127.0.0.1}:${CONNECTOR_PORT:-22401}`.

Like every long-running service it waits at start for the database to reach this release's
migration (`meridian_core.schemaready`).

### Authentication

Every `/connect/` request needs `Authorization: Bearer <MERIDIAN_CONNECTOR_TOKEN>`. The check
runs in a middleware **before routing**, so a request without the token is refused before any
of its body is read or spooled.

| Situation | Answer |
|---|---|
| `MERIDIAN_CONNECTOR_TOKEN` unset, empty, or shorter than 16 characters | `503`, naming the variable. Fails closed: an unconfigured connector is a closed one |
| Missing header, wrong scheme, wrong token | `401` with `WWW-Authenticate: Bearer` |
| An upload with no `Content-Length` | `411` |
| An upload whose `Content-Length` exceeds the cap plus 1 MB of form overhead | `413`, before the body is read |

The token is compared in constant time (`hmac.compare_digest`) and is never logged. `/health`
needs no token: it reports only `{"status": "ok", "database": true}`. The OpenAPI schema is at
`/connect/v1/openapi.json`, behind the token; there are no browsable docs pages.

Generate a token with `openssl rand -hex 32`.

### Endpoints

#### `GET /connect/v1/wanted`

Papers Meridian found and could not retrieve, oldest first.

| Parameter | Default | Meaning |
|---|---|---|
| `kind` | `doi` | Only `doi` exists today |
| `limit` | 100 | 1–1000 |
| `after` | none | The `next` cursor from the previous page |

```bash
curl -s -H "Authorization: Bearer $MERIDIAN_CONNECTOR_TOKEN" \
  "http://127.0.0.1:22401/connect/v1/wanted?kind=doi&limit=2"
```

```json
{
  "kind": "doi",
  "items": [
    {
      "doi": "10.1234/example.5678",
      "title": null,
      "topic": "some-topic",
      "cited_from_source_id": 4211,
      "cited_from_url": "https://example.org/a-page-that-cited-it",
      "first_seen": "2026-10-30T02:14:09.512000+00:00",
      "last_tried": "2026-10-30T02:15:40.120000+00:00",
      "reason": "no_open_access_copy"
    }
  ],
  "next": "88123"
}
```

A DOI is listed when a `doi` queue row settled `done` with `error = 'no_open_access_copy'`
(every provider answered, none had a copy), and the corpus still lacks it: no source with
that DOI holds text, and no upload for it is queued or done. A DOI with several queue rows is
listed once, with its earliest `first_seen` and latest `last_tried`. `next` is null on the
last page. Pagination is a keyset on the first queue row's id, so it is stable while new
rows arrive.

`title` comes from any stored source carrying the DOI, which is rare (the corpus usually has
nothing for a paper it could not get); `cited_from_*` is the page whose references named it.

#### `POST /connect/v1/documents`

`multipart/form-data`:

| Field | Required | Validation |
|---|---|---|
| `file` | yes | At most `MERIDIAN_CONNECTOR_MAX_MB` (default 100); not empty; a type the worker extracts (below), with bytes that look like it |
| `doi` | no | Normalised by the resolver's own normaliser (`https://doi.org/…` and `doi:` prefixes stripped, lower-cased); refused if it is not a DOI |
| `title` | no | Up to 1,000 characters; replaces the extracted title |
| `description` | no | Up to 10,000 characters; kept as provenance |
| `topic` | no | Must be an existing topic |
| `source_url` | no | `http(s)` with a host; kept as provenance, never used as the source's URL |
| `source_tier` | no | One of `sources.source_tier`'s values |

Accepted types: `application/pdf`, `text/html`, `application/xhtml+xml`, DOCX, XLSX, PPTX
and `text/csv` — exactly what the worker's extractors read
(`tests/unit/test_connector.py` holds the two lists together). A part sent as
`application/octet-stream` is typed by its file name's extension. A PDF must contain `%PDF-`
in its first kilobyte and an Office file must be a zip; HTML and CSV must not be binary.
Plain text is refused: the worker has no plain-text extractor, and a document it cannot read
would only fail later.

```bash
curl -s -H "Authorization: Bearer $MERIDIAN_CONNECTOR_TOKEN" \
  -F "file=@paper.pdf;type=application/pdf" \
  -F "doi=10.1234/example.5678" \
  -F "topic=some-topic" \
  http://127.0.0.1:22401/connect/v1/documents
```

`202` with the new upload:

```json
{
  "upload_id": 17,
  "sha256": "9f2c…",
  "filename": "paper.pdf",
  "media_type": "application/pdf",
  "size_bytes": 482113,
  "doi": "10.1234/example.5678",
  "title": null,
  "description": null,
  "topic": "some-topic",
  "source_url": null,
  "source_tier": null,
  "status": "queued",
  "task_id": 90211,
  "source_id": null,
  "error": null,
  "client_address": "192.0.2.10",
  "user_agent": "curl/8.9.1",
  "created_at": "2026-11-01T09:00:00.000000+00:00",
  "updated_at": "2026-11-01T09:00:00.000000+00:00"
}
```

The same bytes again (by sha256) answer `200` with the **existing** upload, whatever the new
form says. Refusals: `413` over the cap, `415` a type nothing extracts or bytes that are not
the declared type, `422` a bad field or an empty file, each with a `detail` naming the field.

#### `GET /connect/v1/documents/{id}`

```bash
curl -s -H "Authorization: Bearer $MERIDIAN_CONNECTOR_TOKEN" \
  http://127.0.0.1:22401/connect/v1/documents/17
```

The same shape as above. `status` is `queued`, then `done` with `source_id` set, or `failed`
with `error` saying why. `404` for an id that does not exist.

### How an upload becomes a source

1. The connector streams the file into `${DATA_ROOT}/inbox/.incoming/`, hashing it, and
   refuses it past the cap. It then moves it to `<first two hex digits>/<sha256><ext>`: only
   the digest and an allowlisted extension reach the filesystem, never the sender's name.
2. In one transaction it writes the `uploads` row and a queue row: `task_type = 'upload'`,
   `seed_source = 'upload'`, `url_or_query = 'upload:<id>'`, the form's topic, and priority
   100 (an operator's own seed's priority, ahead of the crawl).
3. A worker with the inbox mounted claims it (`CONDITIONAL_TASK_TYPES`: a worker without the
   inbox does not claim `upload` rows, so they wait rather than fail). It reads the file back
   and checks it against its digest.
4. The bytes go through the worker's own `_extract` and `_keep` — the path a fetched
   document takes — with these differences, carried by `HandedIn`:

   | | A fetched document | A handed-in document |
   |---|---|---|
   | `sources.url` | the URL | `https://doi.org/<doi>` with a DOI, else `upload://<sha256>` |
   | `source_tier` | from the domain and the document | the form's; else `peer_reviewed` with a DOI; else `informal` |
   | Retention | by tier | always `primary`: the raw file is kept, since it exists nowhere to fetch again |
   | Title, DOI | extracted | the form's when given, else extracted |
   | `trust_state` | the domain's verdict | `cleared` (whoever holds the token chose it, as a curated domain is chosen), `quarantined` if the injection screen flags it |
   | Domain bookkeeping | screening and novel-fetch counts | none: there is no domain |
   | `extra` | | `upload`: upload id, sha256, file name, description, source URL, client address, time |

   Chunking, figures, document kind, links followed and cited DOIs queued are the same.
5. The upload is marked `done` with its `source_id`, and the queue row `done`. A DOI satisfied
   this way has a source with text, so it stays off `/wanted`.

An upload **fails** — `uploads.status = 'failed'`, the queue row `failed` with the same
reason — when nothing could read the file (`no_text: …`, e.g. `no_text: pdftotext-failed
found no text` for a corrupt PDF), when it reads as an error page (`error_page: …`), or when
the inbox file is missing or no longer matches its digest (`inbox_unreadable: …`). No source
row is written for a failed upload. A scanned PDF is not a failure: it is stored and queued
for OCR, as a fetched one is. A storage error (the database or raw store) is retried like a
fetch's.

## Design choices

- **A separate app on its own port, not routes on the API.** The connector is meant to be
  reachable by a tool on the LAN with one token; putting it on the API would put Explore,
  Admin and MCP behind that same door. A test (`test_the_unauthenticated_routes_are_only_health`)
  holds the app to its four routes.
- **Fail closed, before the body.** No token configured refuses everything (503); the gate
  runs before routing so a stranger's 100 MB upload is refused on its headers, never spooled.
- **The sender's `source_url` is not the source's URL.** It could name a page the crawl
  already holds, and the upload would overwrite that row's checksum and chunks. It is kept in
  `sources.extra["upload"]` and on the upload row.
- **One source per DOI.** Two different files for the same DOI land on the same
  `https://doi.org/<doi>` row; the second replaces the first's text (the checksum changed),
  as a re-fetched page does. A crawled copy of the same paper under another URL is a separate
  source, which `worker.docdupes` treats like any other pair of copies.
- **Deduplication by bytes, not by DOI.** The same file is one upload; a re-upload of a
  failed file returns the failed record rather than retrying, since the same bytes fail the
  same way. Fix the cause (the inbox file, an extractor) and requeue the queue row by hand.
- **`uploads.source_tier` has no CHECK.** It is validated against `SOURCE_TIER` in code, so
  a new tier needs no second constraint to widen; `sources.source_tier` keeps its own.
- **`cited_by` is omitted.** Counting every source whose reference list names a DOI means
  unnesting `sources.extra->'citations'` across the corpus on every request, unindexed. The
  first citing page (`cited_from_*`) is cheap and is what the list carries instead.
- **Networks.** `internal` reaches Postgres. A container on `internal` alone cannot publish a
  port (`B-18`), so it is also on `lan`, as `deploy/lan/publish-web.yml` puts `web`; the nft
  egress rules (`deploy/egress-restrict.nft`) still govern what it can reach outbound. Never
  `egress`: it fetches nothing.
- **Named variables, no `env_file`.** A service on an open port is given the two database
  URLs and its own settings, not every password and API key in `.env`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `COMPOSE_PROFILES` | — | Include `connector` to run it (`COMPOSE_PROFILES=connector`, or `autoupdate,connector`) |
| `MERIDIAN_CONNECTOR_TOKEN` | unset | The bearer token; unset or under 16 characters refuses every request |
| `CONNECTOR_BIND`, `CONNECTOR_PORT` | `127.0.0.1`, 22401 | Where compose publishes it. A LAN address opens it to the LAN |
| `MERIDIAN_CONNECTOR_MAX_MB` | 100 | The size cap per file; a malformed value stops the service at start |
| `MERIDIAN_INBOX_ROOT` | `/data/inbox` | Where files are kept, in the connector (read-write) and the worker (read-only); `${DATA_ROOT}/inbox` on the host |

See [reference/environment.md](../reference/environment.md). Uploads are spooled in the
container's `/tmp` (a tmpfs) while they arrive, so a large cap needs that much memory.

## Operating it

```bash
# .env: COMPOSE_PROFILES=connector, MERIDIAN_CONNECTOR_TOKEN=$(openssl rand -hex 32)
docker compose up -d connector
docker compose logs -f connector
curl -s http://127.0.0.1:22401/health
```

The worker's hourly health line carries `inbox: configured | absent`. `absent` means the
worker cannot see the inbox and will not claim uploads; with the stock compose file the
worker always mounts it.

What is in flight: `SELECT status, count(*) FROM uploads GROUP BY 1`. Failures and why:
`SELECT upload_id, filename, error FROM uploads WHERE status = 'failed'`.

The inbox keeps every original file, including those of failed uploads; the raw store keeps a
second copy of each successful one. Neither is pruned.

## Failure modes and traps

- **DOIs settled before this release are not on the list.** Until `B-264` a `doi` row that
  found no copy settled `done` with nothing recorded, indistinguishable from one whose copy
  was queued. The worker now writes `error = 'no_open_access_copy'` and `fetched_at` (when the
  resolver answered) on such a row. Older rows can be re-asked by setting them back to
  `pending`; the resolver answers again and records it.
- **A 401 with the right token** is usually a proxy rewriting or dropping `Authorization`.
- **Every upload fails `inbox_unreadable`.** The worker and the connector mount different host
  folders, or `datadirs` has not handed `${DATA_ROOT}/inbox` to uid 1001
  (`test_the_worker_reads_the_inbox_the_connector_writes` guards the first).
- **Uploads stay `queued`.** No worker claims them: check the health line for `inbox: absent`,
  and that the upload's topic is one a topic-restricted worker (`MERIDIAN_WORKER_TOPICS`)
  serves.

## Tests

- `tests/unit/test_connector.py` — the gate without a database: fail closed with no or a short
  token, 401 for every wrong form of the header, 413 on the declared length; the app's routes;
  media-type drift against the worker's extractors; refused types and mismatched bytes; inbox
  paths that cannot escape; the source URL and tier rules; `_unusable` and `HandedIn`.
- `tests/integration/test_connector.py` — against Postgres: an upload is kept, recorded and
  queued; sha256 dedupe; every bad field and file refused with nothing kept; `/wanted` lists
  exactly the no-copy DOIs the corpus lacks, pages, and drops one once handed in; the worker
  turns an uploaded page and PDF into sources with chunks and provenance; a corrupt PDF and a
  missing inbox file fail with a reason; a worker without the inbox leaves uploads alone; the
  read-only role cannot write `uploads`; the CHECKs take every model value and refuse others.
- `tests/integration/test_worker_run.py` — a DOI with no copy records `no_open_access_copy`.
- `tests/unit/test_compose_topology.py` — the service is profile-gated, runs only the
  connector app, sits on `internal` and `lan`, holds only its named variables, publishes on
  loopback by default, and shares the worker's inbox.
