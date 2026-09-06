# Meridian — Architecture & Scope

*Autonomous research system*

**Status:** Design specification, pre-build
**Author context:** Personal project, self-hosted, public-source data only

---

## 1. Purpose

A self-hosted system that continuously acquires, structures, and relates research material across several topics of interest, so that:

- **Primary:** I can learn a domain deeply and see how topics interconnect
- **Secondary:** the accumulated graph can eventually support written output (e.g. a Singapore walkability plan)

Working topics: **walkability**, **on-demand buses**, **AV deployment / passenger-carrying vehicle use cases**.

**Explicitly out of scope:** work-sourced or internal data. Public sources only.

---

## 2. Design principles

These are the invariants. Everything else is negotiable.

1. **The Pi must never need the reasoning model to stay busy.** Ingestion, crawling, embedding, novelty filtering and frontier expansion run 24/7 with no LLM. This is what makes batched reasoning viable.
2. **Decouple through shared state, not calls.** Ingestion, reasoning and interface planes communicate only via the queue and databases. None blocks another.
3. **Provenance on everything.** Every node, edge and tag records the source chunk that justified it. Without this, nothing is auditable, re-taggable, or citable.
4. **Re-derive from source, never from summaries.** Any re-processing goes back to original chunks. Reasoning over the model's own prior output compounds error.
5. **Steering adjusts generation, never deletes.** All attention changes are reversible because nothing is destroyed.
6. **Validate writes server-side.** Anything enforced only by prompting will eventually be talked around.
7. **Structure over verdicts.** Extract observable properties (stance, funding, hedging, disagreement) rather than asking the model to judge bias or truth.

---

## 3. Hardware

| Component | Spec | Notes |
|---|---|---|
| SBC | Orange Pi 5 Plus, 16–32GB | 24/7 ingestion node |
| Storage | 2TB NVMe SSD | ~50–100× more than the pipeline needs; headroom is for raw files and offline corpora |
| Cooling | Active | Continuous operation |
| Network | Cloudflare Tunnel | No open inbound ports |

**Storage budget (est. 50k documents):**

- Clean extracted text: ~2GB
- Embeddings (bge-m3, ~1M chunks): ~4GB
- Graph (Postgres + AGE): a few GB
- Raw PDFs/HTML retained: 50–150GB
- Optional offline corpora (OSM SEA extract, filtered arXiv, Wikipedia): the real consumer — be selective

**Backup is the actual risk, not capacity.** The graph encodes many synthesis passes and cannot be cheaply regenerated. Snapshot the Postgres cluster off-device regularly (`pg_dump`, or WAL archiving for point-in-time recovery). The vector store is rebuildable from text; the graph is not.

A **UPS is the single highest-return durability item** — cheaper and more effective than any database choice. Consumer NVMe often lacks power-loss-protection capacitors, so it may acknowledge writes still sitting in DRAM; Postgres's guarantees assume `fsync` tells the truth.

---

## 4. Architecture

Three planes, deliberately decoupled.

```
┌──────────────────────────────────────────────────────────┐
│  INGESTION PLANE — Orange Pi, 24/7, no LLM               │
│                                                          │
│  Queue → Fetch → Extract → Metadata capture              │
│    ▲                          ↓                          │
│    │                   Novelty gate (cosine)             │
│  Frontier expansion           ↓                          │
│  (links, citations,    Chunk → Embed (bge-m3)            │
│   spaCy NER, TF-IDF)          ↓                          │
│    ▲                   ┌──────┴──────┐                   │
│    └───────────────────┤  vectors    │  Raw store (FS)   │
└────────────────────────┴──────┬──────┴───────────────────┘
                                │ MCP (Cloudflare Tunnel + Access)
┌───────────────────────────────▼──────────────────────────┐
│  REASONING PLANE — hosted frontier model, ~1h/day        │
│                                                          │
│  pull new chunks → extract relations → tag attributes    │
│  → coverage scoring → analogical expansion → gap         │
│  analysis → emit seeds → advance mark                    │
└───────────────────────────────┬──────────────────────────┘
                                ▼
              ┌─────────────────────────────────┐
              │   POSTGRES  (single store)      │
              │  queue · metadata · graph (AGE) │
              │  vectors (pgvector) · JSONB     │
              └────────────────┬────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────┐
│  INTERFACE PLANE — local web app                         │
│  search · graph view · steering · annotation · export    │
└──────────────────────────────────────────────────────────┘
```

### Stack

| Layer | Choice | Rationale |
|---|---|---|
| Queue + metadata + graph | **Postgres** (+ Apache AGE) | One battle-tested store, one recovery path, one backup routine |
| Vectors | **pgvector** (HNSW) | Same server. Adequate to ~1M vectors; Qdrant is the documented migration path if recall becomes the measured bottleneck |
| Flexible/evolving fields | JSONB | Absorbs schema evolution (§7.3) without a document store |
| Orchestrator state | Plain `runs` table in Postgres | Stage-level resumability; no framework needed (§11.10) |
| Embeddings | bge-m3 | Multilingual — essential for JP/CN/KR sources |
| Entity extraction | spaCy + gazetteer (`EntityRuler`) | Fast, no LLM; gazetteer carries the domain acronyms no pretrained model knows (§5.6) |
| Backend | FastAPI | Serves UI and MCP |
| Frontend | Sigma.js v3 + graphology | WebGL rendering; graphology supplies centrality, community detection and pathfinding client-side (§12.1) |
| Search backend | SearXNG (self-hosted) + paid API fallback | No keys, residential IP; fallback removes fragility (§6.4) |
| Fetch + extract (HTML) | Crawl4AI (pinned, loopback-only) | Clean markdown, citation extraction, prefetch discovery, crash-recoverable deep crawl (§6.4) |
| Document conversion | MarkItDown (`convert_local`/`convert_stream` only) | PDF, Office, EPub, archives → markdown (§6.6) |
| OCR | OCRmyPDF/Tesseract (Pi) + VLM (Fedora box) | Two-tier, deferred queue, never inline (§6.6) |
| Exposure | Cloudflare Tunnel + Access | Already-proven setup |
| Reasoning | Hosted frontier model via MCP | Edge quality is the system bottleneck |
| Offline fallback | Local model on the Fedora box (§11.7) | Degraded tagging when tunnel is down |

**Rationale for consolidating on one server:** the Pi has 16GB *shared with other services*. Running Postgres + Qdrant + a separate graph store would push the working set into swap, which on a Pi is where responsiveness dies. One tuned Postgres fits; three stores do not.

### Options considered and rejected

| Option | Why not |
|---|---|
| **Kùzu** | **Archived October 2025** following Apple's acquisition of Kùzu Inc.; website shut down, maintenance passed to early-stage community forks (LadybugDB, bighorn). Not a basis for a system that must run unattended for years |
| **Neo4j Community** | Best Cypher ergonomics and native vector indexes, but online backup is Enterprise-only — Community can only dump against an *offline* DBMS, meaning every snapshot requires downtime. JVM footprint also poor on a shared 16GB Pi. Reconsider if a dedicated 32GB+ box appears |
| **MongoDB** | Vector search reached Community GA in June 2026, so that objection is gone — but `$graphLookup` is weak for multi-hop traversal with edge predicates, which is the analytical core here. Also needs two processes (`mongod` + `mongot`) |
| **MySQL** | No graph extension, immature vector support |
| **Separate Qdrant** | Earns its place at scale, but HNSW on ~1M 1024-dim vectors wants ~4GB+ resident — the thing that would push this Pi into swap |
| **LangChain / LangGraph** | Both rejected. The synthesis run is eight sequential steps with no branching, no multi-agent handoffs, no conditional routing — a pipeline, not a graph workflow. LangGraph's main draw (durable checkpointing) duplicates the `runs` table that the durability requirement demands anyway. Costs: ARM dependency weight, fast-moving APIs, framework debugging at 3am unattended, and an abstraction layer sitting on top of load-bearing server-side validation. Revisit only if the run develops real branching, multi-agent handoffs, or mid-run human interrupts |

> **Verify before building:** model options, the MCP authorization spec, and Apache AGE's release cadence have all moved quickly. Check current documentation rather than relying on this document.

**Decision: no local 32B.** Graph edge quality dominates every downstream outcome, and a hosted frontier model materially outperforms a local 32B at extraction and gap analysis. Since no work data enters the system, the main objection to hosted inference does not apply. This trades offline reasoning for quality — a deliberate, named trade, not a drift.

---

## 5. Data model

### 5.1 Queue

```
task_id, url_or_query, task_type, status, priority, topic,
seed_source (frontier | model | user), attempts,
created_at, fetched_at, error
```

Status flow: `pending → fetched → extracted → embedded → done` (or `failed`, `rejected_duplicate`)

### 5.2 Source record

```
source_id, url, archive_url, title, author, publisher,
publication_date, doi, accessed_at, checksum,
source_tier, raw_file_path, language
```

**Source tiers:** `peer_reviewed | government | institutional | press | informal`.
Tier is the first tiebreaker when sources conflict. Assigned at ingestion from domain and document structure — not a model judgment.

### 5.3 Chunk

```
chunk_id, source_id, text, embedding, page_or_offset,
chunk_index, created_at
```

Page/offset is captured **at extraction time**. Reconstructing it later is painful and often impossible.

### 5.4 Graph

**Node types** (typed ontology per domain, not one generic "concept"):

- Shared: `concept`, `place`, `organisation`, `intervention`, `finding`, `source`
- AV / ConOps: `use_case`, `ODD`, `vehicle_class`, `service_model`, `stakeholder`, `failure_mode`, `regulatory_requirement`

**Edge record:**

```
edge_id, from_node, to_node, relation_type, topic_labels[],
supporting_chunk_ids[], confidence, stance, certainty,
contested_with[], schema_version, created_at, created_by
```

**Retention tiers for raw files:**

| Tier | Keep |
|---|---|
| Primary (gov, papers, reports) | Raw file + full metadata + checksum |
| Background (news, commentary) | Extracted text + metadata; snapshot if cited |
| Junk / near-duplicate | Drop after novelty gate |

Link rot is the binding reason for raw retention — government URLs reorganise constantly, and a local copy plus archive URL is what keeps a citation checkable years later.

### 5.5 Entity resolution

Without this the graph fragments: "LTA", "Land Transport Authority", "the Authority" and "LTA Singapore" become four nodes. Downstream this breaks silently — coverage scoring undercounts, the attribute discrimination test misfires, and cross-topic edges never form because the shared entity was split.

**Resolve at write time, not as periodic cleanup.** Duplicates that reach the graph propagate into edges before they are noticed.

```
entity_id, canonical_name, type, aliases[],
embedding, merged_from[], confidence
```

**Pipeline on every new mention:**

1. **Normalize** — lowercase, strip punctuation, expand known abbreviations
2. **Block** — fuzzy match against canonical names and aliases, plus embedding kNN restricted to the same node type
3. **Score** — string similarity (token-set / Jaro-Winkler) + embedding cosine + **context overlap** (do the two co-occur with the same neighbours?). Context is the strongest signal: "Cambridge" the city and "Cambridge" the university sit in entirely different neighbourhoods
4. **Three-band decision** — high → auto-merge; low → separate entity; **middle band → queue for model adjudication** in the next daily run. The middle band is small, cheap, and the only place a model adds value

**Merges must be reversible.** Reassign edges to the canonical node, retain the old ID as a redirect rather than deleting, log every merge. Bad merges are worse than duplicates because conflation is invisible once done.

**Two cheap wins:** never merge across node types, and hand-seed domain acronyms (LTA, URA, TOD, ODD, MRT) at cold start — this is exactly where automatic matching fails and manual entry costs ten minutes.

### 5.6 Gazetteer

Generic NER models do not know domain entities. "Land Transport Authority" may resolve as an ORG; "Electronic Road Pricing", "Silver Zone", "ODD", "Walk2Ride" will not. A curated gazetteer is what makes fast-loop entity extraction useful.

```sql
CREATE TABLE gazetteer (
  term_id      BIGSERIAL PRIMARY KEY,
  canonical    TEXT,
  aliases      TEXT[],
  entity_type  TEXT,      -- agency | scheme | infrastructure | metric | concept
  topic_labels TEXT[],
  source       TEXT,      -- manual | auto_acronym | model_proposed
  approved     BOOLEAN
);
```

Loaded into spaCy's `EntityRuler` at worker startup, so gazetteer matches take precedence over statistical NER.

**Do not hand-write it — bootstrap it.**

1. **Seed ~50 manually** at cold start: LTA, URA, MRT, ERP, HDB, BCA, NParks, PUB, ODD, TOD, plus the obvious counterparts for comparison cities.
2. **Auto-harvest acronym definitions.** Government and academic documents define acronyms on first use, so the pattern `Full Name Here (ACRONYM)` is extremely high-yield and costs one regex over already-extracted text. This alone will populate most of the list.
3. **Model-proposed additions.** The slow loop flags recurring capitalised sequences that resolve to no known entity, queued as `approved=false`.
4. **Approve in the UI** — a two-minute weekly task, or auto-approve above a frequency threshold.

The gazetteer also feeds entity resolution (§5.5): its alias lists are exactly the alias matching that resolution needs, so the two share one table rather than duplicating.

---

## 6. The two loops

### 6.1 Fast loop — 23h/day, Pi, no LLM

```
pop task → fetch (rate-limited, robots-respecting)
  → extract text + capture metadata + store raw
  → novelty gate: cosine vs existing vectors; drop if >0.95
  → chunk → embed → store
  → frontier expansion:
       outbound links + citations
       spaCy named entities
       TF-IDF co-occurrence terms
  → enqueue new targets at frontier priority
```

Frontier expansion is what keeps 23 hours productive without any model awake. On a topic like walkability the citation graph alone sustains a full queue for weeks.

### 6.2 Slow loop — ~1h/day, hosted model via MCP

```
1. list_new_since(mark)          — pull day's novel chunks
2. extract entities + relations  — add_edge with provenance
3. tag attributes                — tag_entity, schema-versioned
4. coverage_report               — topic × dimension thinness
5. analogical expansion          — comparison cases + disanalogies
6. gap analysis                  — rank by relevance to goals
7. enqueue_seed                  — validated, capped
8. advance_mark
```

Batch-over-trickle is genuinely better here: one large context pass sees the full day's material, producing better gap analysis than piecemeal reasoning.

### 6.3 Scheduling

High-water mark. The Pi tracks the last chunk ID consumed; the model pulls everything after it, then advances. Cron-triggered daily, optionally early-triggered when unprocessed novel chunk count crosses a threshold.

### 6.4 Source acquisition

**Search backend: SearXNG**, self-hosted. Metasearch over multiple engines, JSON output, no API keys, Docker deployment. A residential IP helps — upstream engines block datacenter ranges far more aggressively.

Its weakness is that it scrapes upstream engines, so individual engines break or get rate-limited regularly. Configure several, treat engine failure as routine, and never let a dead engine stall the queue. **Fallback to a paid search API** (Brave, Tavily, Serper) when SearXNG returns nothing — keeps cost near zero while removing the fragility.

**Fetch and extract: Crawl4AI** (Apache 2.0, ARM64 Docker images available).

Used for what it does well, and deliberately not for the rest:

| Use | Skip |
|---|---|
| Clean/fit Markdown with `PruningContentFilter`, BM25 filtering | `LLMExtractionStrategy` — an LLM in the fast loop breaks the core invariant (§2, principle 1) |
| Citations/references extraction — feeds frontier expansion directly | Stealth mode, undetected browser, proxy escalation — not needed under the fetch policy below |
| `prefetch=True` for cheap URL discovery, then selective processing | |
| `resume_state` / `on_state_change` for crash-recoverable deep crawls | |
| `AsyncUrlSeeder` for sitemap-based discovery | |

**Two operational constraints:**

1. **Don't render every page.** Playwright + Chromium + a browser pool is heavy against 16GB shared. Use plain HTTP for static content — most government PDFs and academic pages — and reserve the browser for genuinely JS-dependent sites. Keep the pool small.
2. **Bind to loopback; never expose through the tunnel; pin the version.** Crawl4AI's Docker API had critical vulnerabilities fixed in v0.8.7 (RCE, SSRF, auth bypass, arbitrary file write, hardcoded JWT secret), a compromised dependency replaced in v0.8.6, and was made secure-by-default only in v0.9.0. The maintainer has been responsive, but that is heavy churn for a component that ingests untrusted web content. Reachable from the worker container only.

**Prefer structured APIs over scraping.** For a research corpus, the sources worth citing almost all offer proper programmatic access:

| Source type | Use |
|---|---|
| Academic papers | arXiv API, OpenAlex, Crossref, Semantic Scholar, Unpaywall |
| Singapore government data | data.gov.sg, LTA DataMall |
| Publications and reports | RSS feeds, sitemaps, bulk dumps |
| Statistics and standards | Institutional APIs |

These return structured metadata, never rate-limit aggressively, and never captcha. Bot detection is largely a news-site and commercial-content problem — the `background` tier (§5.2), extracted for context but rarely cited.

**Fetch policy: try, fail, move on.**

No stealth, no retry escalation, no proxy chains, no detection logic. Fetch each link; Crawl4AI returns a status and the worker acts on it.

Every behaviour below is **configurable**, not hardcoded. Defaults ship in `fetch_policy.yaml`, seeded into the DB at first boot, editable per-domain in Admin (§12.6).

```yaml
# fetch_policy.yaml — global defaults
respect_robots:        true
user_agent:            "MeridianBot/0.1 (+https://<contact-url>)"
send_contact_header:   true

concurrency_per_domain: 2
delay_per_domain_ms:    1000
respect_crawl_delay:    true

conditional_requests:   true      # If-Modified-Since / ETag
timeout_s:              30
max_retries:            2
backoff_base_s:         5

blocked_after_failures: 5         # consecutive; then mark domain blocked
prefetch_filter:        true      # domain blocklist + already-seen check
render_js:              auto      # auto | always | never
max_page_bytes:         20_000_000
```

```sql
CREATE TABLE fetch_policy (
  domain     TEXT PRIMARY KEY,   -- '*' = global default row
  settings   JSONB,              -- overrides merged over global
  status     TEXT,               -- active | blocked | paused
  note       TEXT,
  updated_at TIMESTAMPTZ,
  updated_by TEXT
);
```

Resolution order: per-domain row → global row → file default. Per-domain overrides matter because one setting for a large API and a small municipal server is wrong in one direction or the other.

**Rationale for the four defaults that do real work:**

- **Per-domain concurrency and delay** — keeps one slow domain from monopolising the worker, and avoids hammering small servers
- **Blocked-domain marking** — otherwise one dead site consumes crawl budget for weeks unnoticed
- **Pre-fetch URL filtering** — SearXNG returns a lot of content-farm and SEO junk; the novelty gate catches duplicates afterwards, but not fetching them is cheaper
- **Conditional requests** — makes re-checks nearly free

`render_js: auto` uses the browser only for genuinely JS-dependent pages; static content takes the cheap HTTP path.


### 6.5 Paper full-text resolution

Given a DOI, resolve to a legally available copy in order:

1. **Unpaywall** — `api.unpaywall.org/v2/{doi}` → `best_oa_location`
2. **OpenAlex** — also returns OA locations, already in use for metadata
3. **CORE** — aggregates institutional repositories
4. **arXiv / SSRN / RePEc** — preprints; strong coverage for transport economics and planning
5. **Institutional repository** — author copies the publisher version paywalls

**Store DOI and full metadata even when full text is unavailable.** A metadata-only node still participates in the citation graph, still counts toward coverage scoring, and is still citable — the abstract alone is usually enough to place it correctly.

The residual gap is smaller than it looks for this corpus: government reports, LTA/URA publications, planning documents and consultancy work are not paywalled, and the field has a healthy preprint and grey-literature culture. For the handful that matter and resist all of the above, institutional access or emailing the author is the route.

### 6.6 Document conversion, OCR and figures

#### Format routing

| Input | Tool |
|---|---|
| HTML | Crawl4AI (§6.4) |
| PDF, DOCX, XLSX, PPTX, EPub, CSV/JSON/XML, ZIP | **MarkItDown** (MIT) |
| Scanned PDF / image-only pages | OCR queue (below) |

MarkItDown covers the formats that otherwise have no path — government and consultancy sources arrive as Office documents far more often than expected.

**Security:** MarkItDown performs I/O with the privileges of the calling process, and its `convert()` is intentionally permissive across local files, remote URIs and byte streams. The documented guidance is to never pass untrusted input directly and to call the narrowest method available. **Use `convert_local()` or `convert_stream()` on already-fetched bytes — never `convert()` on a URL.**

#### Scan detection

Cheap check before any OCR work: `pdftotext`, then characters-per-page. Below ~100 chars/page means it is a scan.

```
fetch PDF → pdftotext → chars/page
  ├─ native  → extract → chunk → embed
  └─ scanned → enqueue to ocr_queue; source stays metadata-only
```

**OCR never runs inline** — it would stall the 23-hour loop. The source record enters the graph as metadata-only and is enriched later, so a scan never blocks ingestion.

#### Two-tier OCR

| Tier | Tool | For |
|---|---|---|
| Cheap | OCRmyPDF / Tesseract, on the Pi | Clean single-column scans |
| Quality | VLM-based, on the Fedora box | Multi-column planning reports, tables, poor scans |

`markitdown-ocr` is **VLM-based, not classical OCR** — it extracts text from embedded images using LLM Vision via the same `llm_client`/`llm_model` pattern MarkItDown uses for image descriptions. Two consequences:

- It is OpenAI-compatible, so it points at the local llama.cpp vision model (§11.7)
- **If no `llm_client` is provided the plugin still loads but OCR is silently skipped**, falling back to the standard converter. Silent degradation is unacceptable in an unattended system: assert the client at startup and record `ocr_applied` explicitly so skipped documents are findable

**Two requirements carried over from native extraction:**

1. **Preserve page boundaries.** OCR pipelines flatten pagination readily; citations need accurate page numbers (§6.5). Configure page markers and store offsets at chunk creation.
2. **Gate on quality before ingesting.** Bad OCR poisons the graph with nonsense entities that are expensive to unpick. Heuristic gate — dictionary hit rate, character distribution, alphanumeric ratio. Below threshold, mark `ocr_failed` and keep the source metadata-only.

Record `ocr_applied`, `ocr_tier` and a confidence score. The §11.12 reprocessing machinery then applies unchanged: when better OCR becomes available, re-derive from the stored raw file.

#### Figures

Figures often carry findings more compactly than the text — network diagrams, mode-share charts, ODD maps, streetscape comparisons.

```sql
CREATE TABLE figures (
  figure_id       BIGSERIAL PRIMARY KEY,
  source_id       TEXT REFERENCES sources,
  page            INT,
  bbox            JSONB,
  file_path       TEXT,
  thumbnail_path  TEXT,
  caption         TEXT,      -- extracted, usually available
  alt_text        TEXT,
  vlm_description TEXT,      -- deferred enrichment
  ocr_text        TEXT,      -- embedded chart labels
  linked_nodes    TEXT[]
);
```

**Start with captions, not vision.** Figure captions are text, usually extractable, and often the most information-dense sentence about the figure. Indexing them in normal chunk search delivers most of the value at no extra cost.

**Lookup affordance:** figures link to graph nodes and surface as thumbnails in the node detail panel (§12.5), with a link to the raw file at the correct page. Clicking a node shows supporting chunks *and* relevant figures.

Multimodal embeddings for image similarity search are deliberately out of scope initially — real scope creep, and caption search covers most of the need.

#### Enrichment is user-triggered

VLM work — figure descriptions, chart OCR, quality-tier document OCR — is expensive and optional. It does **not** run automatically.

```sql
CREATE TABLE enrichment_queue (
  item_id, item_type,   -- figure_vlm | ocr_quality | chart_ocr
  target_id, status,    -- pending | running | done | failed
  requested_by, requested_at, completed_at
);
```

The UI shows pending counts by type and estimated cost; a button starts a batch. Telegram mirrors it, in the same shape as the reprocessing prompt (§11.12):

> 340 figures and 28 scanned documents pending enrichment. Local vision model available.
> **[Run all]** · **[Figures only]** · **[Sample 20]** · **[Skip]**

This keeps the pipeline autonomous for acquisition while leaving the expensive optional passes under explicit control — consistent with the acquisition/consumption split in §16.

---

## 7. Attribute system

The attribute layer is what enables cross-topic and cross-city reasoning. It is the analytical core.

### 7.1 Scoping

- **Global** (all topics): climate, density, governance capacity, income level, land constraint, modal share
- **Topic-local**: ODD/fleet size (AV); shade provision/sidewalk width (walkability)

Without scoping, topic-specific attributes create empty cells across unrelated entities and coverage scoring misreads them as thin evidence. Global attributes carry cross-topic reasoning and warrant a higher retirement bar.

### 7.2 Analogical expansion

Decompose the study context into attributes, then search each axis **independently** — because each generates a different, barely-overlapping comparison set:

| Axis | Comparison set |
|---|---|
| Equatorial climate | Bangkok, KL, Jakarta, Darwin, Miami |
| Governance capacity | Hong Kong, Shenzhen, Gulf states |
| Density / land scarcity | Tokyo, Seoul, Hong Kong |
| High income + demand management | Nordic cities, London |

**Every comparison edge stores both the dimension of similarity and the disanalogy.** "Singapore is equatorial, therefore Jakarta's findings apply" is exactly the shallow inference to guard against — the two share climate and almost nothing on governance capacity or income. Comparisons without stated limits are how bad policy papers get written.

Attribute-derived seeds get a capped share of the queue (~20–30%) so they don't drown out core-topic material.

### 7.3 Schema evolution

Every tag carries `schema_version`, `tagged_at`, and its justifying chunk.

**On schema change:** enqueue a targeted backfill extracting *only* the new attribute across existing entities, amortized across several daily runs. Coverage scoring must be schema-aware, or a half-backfilled attribute reads as thin evidence rather than untagged.

**Autonomous attribute proposal — mechanical gate, no human required:**

- Evidence requirement: the attribute must appear as an explanatory variable in ≥N independent sources
- Discrimination test: entropy across applicable entities; auto-reject if it doesn't usefully split the set
- Hard cap: ~12 active attributes, with auto-retirement of the lowest-utility when a stronger candidate qualifies
- All schema changes logged

**Monthly audit** scores each attribute on discrimination, usage frequency, tagging coverage, and explanatory power (appearance in cross-topic edges and contradiction resolutions — the strongest signal).

**Hysteresis:** an attribute must fail 2–3 consecutive audits before retirement. Every schema change triggers backfill cost, so churn is expensive and slow-moving schema is a feature.

### 7.4 Diversity seeding — escaping the filter bubble

Citation-following is self-reinforcing by construction: it walks toward consensus and away from dissent. Left alone, the contested-node machinery (§9) starves — not because there is no disagreement, but because the crawler never found it.

**Reserve 10–15% of the seed budget for diversity seeds**, generated by five mechanisms:

1. **Stance imbalance → auto counter-seed.** *Build this first.* If every source on a node argues the same direction, that is either genuine consensus or a one-sided crawl, and the two are indistinguishable without looking. Auto-generate counter-queries: "criticism of X", "X negative findings", "why X failed". Measurable, self-triggering, and targets the actual failure rather than adding noise.
2. **Source-tier imbalance.** A node evidenced entirely by advocacy-funded or entirely by government sources gets seeds aimed at the missing tiers.
3. **Citation-graph escape.** Periodically query using naive phrasings absent from the corpus. Citation-following converges on the field's own vocabulary; an outsider's words surface material the field does not cite.
4. **Random distant walks.** Expand from a node far from current focus — low edge count or embedding-distant. Cheap serendipity, well-matched to the exploration goal.
5. **Forced non-English seeds** on well-covered topics. The primary comparison set is largely non-Anglophone, so this is not optional.

---

## 8. Source assessment

**Do not ask the model whether a source is biased or factual.** For contested policy topics this is unreliable in a specifically dangerous way: the model's own priors silently shape the evidence base, and the result looks objective while being skewed.

**Decompose into observable properties instead:**

| Instead of | Extract |
|---|---|
| "Is it biased?" | **Stance** — what position does it argue for? |
| "Is it factual?" | **Claim vs. evidence** — asserted, cited, or original data? |
| "Trustworthy?" | **Provenance** — author, institution, funder, review status |
| "Confident?" | **Hedging** — "may reduce" vs "reduces" |
| — | **Disagreement** — contradiction edges to other sources |

Bias then becomes **structure you read off the graph**, not a score. If a cluster of pro-intervention findings all trace to advocacy-funded sources, that is visible — and far more defensible in writing than a model's verdict.

Likewise, "sentiment analysis" is the wrong frame for research literature. The useful axes are **stance, certainty, and contestedness**.

---

## 9. Contradiction and time

**Contradictions are signal, not error.** When sources conflict, store both edges and mark the pair contested. Contested nodes are the highest-value nodes — they locate live debates and are where written output has something to argue. A graph that silently resolves conflicts hides the interesting part.

**Temporal decay.** Every node carries a publication date. Gap analysis flags dimensions where evidence is stale — critical because ageing is topic-dependent: a 2014 finding on AV public acceptance is near-worthless, while a 2014 finding on pedestrian thermal comfort remains sound.

**Language coverage.** Serious literature for the primary comparison set (Tokyo, Seoul, Hong Kong, Shenzhen) is substantially non-English. bge-m3 embeddings plus translation at synthesis time; English-only ingestion would systematically bias toward Western sources.

---

## 10. Steering

Attention is a weight vector over topics; seeds are drawn proportionally.

```
walkability      0.40
on-demand-bus    0.35
av-deployment    0.25
```

Steering rewrites the vector. Nothing is deleted, so returning costs nothing — no rebuild, no re-crawl.

**Modes:**

- **Boost with decay** — temporary multiplier that returns to baseline automatically ("steer back later" without needing to remember)
- **Maintenance** — topic stops generating new seeds but keeps processing its queue
- **Floor guarantee** — every active topic retains 5–10% minimum so nothing fully stalls

**On resume:** re-validate stale pending seeds (link rot) rather than executing blind. Time-sensitive material published during a quiet period was missed — the citation frontier is static and recovers, but policy announcements do not; the floor guarantee mostly covers this.

### 10.1 Config and audit

Both manual and autonomous steering write the same tables.

```sql
CREATE TABLE topic_config (
  topic            TEXT PRIMARY KEY,
  weight           REAL,
  floor            REAL,          -- minimum share, so nothing fully stalls
  ceiling          REAL,
  boost_factor     REAL,
  boost_expires_at TIMESTAMPTZ,   -- decay handled by expiry, not by memory
  pinned           BOOLEAN,       -- autonomous adjustment may not touch
  status           TEXT           -- active | maintenance | paused | archived
);

CREATE TABLE steering_log (
  changed_at TIMESTAMPTZ,
  actor      TEXT,   -- user | orchestrator
  topic      TEXT,
  field      TEXT,
  old_value  TEXT,
  new_value  TEXT,
  reason     TEXT
);
```

### 10.2 Intervention model

**Default: the system steers itself.** It generates seeds and executes them. Nothing waits for approval — server-side validation (domain allowlist, per-run caps, §11.4) is what makes that safe, not human gating.

Interventions are optional, and each carries a lifetime:

| Scope | Example | Behaviour |
|---|---|---|
| **Once** | Inject a seed; trigger a run now | Executes, leaves no config trace |
| **Temporary** | Boost a topic 2× for 3 weeks | `boost_expires_at` decays it back automatically — nothing to remember to undo |
| **Permanent** | Change baseline weight, pin a topic, blocklist a domain, **add a topic**, **archive a topic** | Persists until changed again |

Every intervention path — UI, MCP tools, Telegram — writes the same `topic_config` rows and logs to `steering_log`.

**Adding a topic** inserts a new `topic_config` row (`add_topic` — weight, floor, ceiling) and re-normalises existing weights so the set still sums to 1.0. It is a small repeat of Phase 0 cold start (§15): a handful of hand-seeded sources for the new topic are worth the same evening of care the original topics got, for the same reason (§16, "cold-start seed quality propagating").

**Archiving a topic** (`archive_topic`) sets `status = 'archived'`: it stops generating seeds and drops out of the weight-normalization pool, exactly like maintenance mode but permanent until reversed. **Nothing is deleted** — existing nodes, edges, and tags stay in the graph untouched, and un-archiving is a one-line status change, not a rebuild (§10's "nothing is deleted, so returning costs nothing" applies to whole topics, not just weights).

**The seed list is a log, not a gate.** It shows what the system proposed and executed, so corrections happen after the fact rather than blocking the loop. This matters for a system designed to run unattended for weeks: anything that waits for a human eventually stalls.

`steering_log` is not optional. With two writers, the alternative is opening the UI in a month and not knowing why a weight is where it is. Log actor and reason on every change.

**Guard rails on autonomous adjustment:** per-topic floors and ceilings, and `pinned` topics the orchestrator cannot modify.

---

## 11. External agent integration

All reasoning is performed by **external agents** — hosted over the internet, or local on the LAN. Nothing large runs on the Pi itself. There are two distinct integration directions, and conflating them is the main design trap.

### 11.1 Three integration directions

| | **Pi-initiated (orchestrator)** | **Agent-initiated (MCP)** | **Subscription session** |
|---|---|---|---|
| Who calls whom | Pi → model API | External agent → Pi | Agent CLI → Pi |
| Trigger | Scheduled / threshold | Me, ad hoc | Me, weekly-ish |
| Purpose | The daily autonomous loop | Interactive exploration | Frontier-quality synthesis |
| Tools exposed | Local Python functions | MCP read tools | MCP read + scoped write |
| Billing | API credits | — | Existing subscription |
| Needs human | No | Yes | Yes |

### 11.1a Subscription-backed synthesis

Frontier-quality synthesis without API spend: rather than the backend calling out to a subscription, an agent CLI (Claude Code, Codex CLI) connects *inward* to Meridian's MCP server and does the work.

```
open agent CLI → connect to Meridian MCP
  → list_new_since(mark) → extract relations → add_edge (validated)
  → coverage_report → gap analysis → enqueue_seed → advance_mark
```

Same tools, same server-side validation, same provenance. Requires a **scoped write token for human-driven synthesis sessions**, since write scope is otherwise orchestrator-only (§11.4).

**Resulting cadence:**

| | Actor | Tier | Work |
|---|---|---|---|
| Nightly | llama.cpp, opportunistic | 2 | Tagging, translation, triage |
| Weekly–monthly | Subscription agent session | 4 | Relation extraction, gap analysis, analogical expansion |
| Rare | Hosted API | 4 | Only when unattended operation is needed |

Combined with §11.12, each frontier session *upgrades* the intervening tier-2 work through reprocessing.

**Gotchas:**

- If `ANTHROPIC_API_KEY` is set in the environment, Claude Code bills API usage instead of drawing on the subscription — unset it in that shell
- Subscription usage limits are shared across normal Claude use and Claude Code, so scope each session to a defined task list rather than turning it loose on the whole backlog
- Never script the web UI or use unofficial API wrappers — against terms, risks the account, and worse engineering than the MCP path

**If frontier sessions never happen**, nothing breaks: ingestion continues, tier-2 tagging continues, the downgrade guard prevents corruption, and the reprocessing queue simply grows. The one unrecoverable cost is that a weaker model chose the seeds, so the *corpus* is less well-targeted — reprocessing later fixes the edges but not the crawl history. The damage is bounded because frontier expansion and diversity seeding are model-independent and account for most of the queue.

### 11.1b Why the directions differ

**This is the key point:** MCP is agent→server. It cannot wake anything. For the daily loop to run unattended, the Pi must be the *client* — a small local orchestrator process holding an API key, calling the hosted model, and passing its own local functions as tools. MCP alone would mean something external must initiate every run, which defeats autonomy.

All three paths hit the same validation layer. None gets privileged access.

### 11.2 The orchestrator (Pi-side, autonomous)

A lightweight daemon on the Pi. Cron- or threshold-triggered.

```
wake → assemble batch (new chunks since mark)
     → select agent profile for task type
     → call hosted model API with local tools bound
     → apply validated writes
     → advance mark → log run summary → sleep
```

Holds the API key locally — never exposed through the tunnel. If the model API is unreachable, the run defers and retries with backoff; ingestion is unaffected.

### 11.3 Agent registry and capability routing

Different tasks want different models. Rather than hardcoding one:

```
agent_id, provider, model, task_types[], token_scope,
cost_tier, quality_tier, max_context, enabled, fallback_agent_id,
endpoint, health_url, availability, wake_mac
```

Routing by task type:

| Task | Profile | Rationale |
|---|---|---|
| Relation extraction | Strong reasoning, large context | Edge quality dominates everything downstream |
| Attribute tagging | Mid-tier | Narrow, structured, schema-constrained |
| Gap / analogical analysis | Strongest available | The genuinely hard reasoning |
| Translation | Any multilingual | Mechanical |
| Drafting | Strongest, long context | Infrequent |

Fallback chains mean a provider outage degrades rather than halts. Swapping models becomes a config row, not a code change — which matters given how fast the options move.

### 11.4 Scoped tokens per agent

Every agent gets its own credential with an explicit tool scope. An interactive exploration session should hold **read-only** scope; only the orchestrator's own profile carries write tools.

```
token_id, agent_id, allowed_tools[], rate_limit,
seed_cap_per_run, expires_at
```

This is what makes it safe to point an ad-hoc chat session at the graph without risking mutation.

### 11.5 Async job pattern

MCP calls and HTTP requests time out; a full synthesis pass will not fit in one. So long work is submitted, not awaited:

```
submit_job(type, params) → job_id
get_job_status(job_id)   → queued | running | done | failed
get_job_result(job_id)   → payload
```

The agent submits and disconnects. Work continues on the Pi. This also means an interactive session can kick off a heavy query, close, and collect the result later.

### 11.6 MCP tool surface

**Read** (generous): `search_chunks`, `query_graph`, `get_node`, `get_neighbours`, `coverage_report`, `list_contested`, `list_new_since(mark)`, `get_source_metadata`

**Write** (narrow, validated, orchestrator scope only): `add_edge`, `tag_entity`, `enqueue_seed`, `advance_mark`, `propose_attribute`

**Jobs** (async, user-scoped — §11.5, §11.13): `submit_report_job`, `get_job_status`, `get_job_result`

**Ops** (see §13): `get_health`, `set_topic_weights`, `add_topic`, `archive_topic`, `pause_topic`, `trigger_run`, `list_recent_runs`

### 11.7 Local large model (optional tier)

The spec's default drops the local 32B in favour of hosted reasoning (§4). But if a GPU box exists or gets built, **it connects as another registry row — not as a separate architecture.**

**Why it needs no special casing:** Ollama, llama.cpp's server, and vLLM all expose OpenAI-compatible endpoints. The orchestrator's calling code is identical; only the registry entry differs.

```
agent_id:      local-llamacpp
provider:      openai_compatible
endpoint:      http://onelaptop.local:PORT/v1
health_url:    http://onelaptop.local:PORT/health
availability:  opportunistic          # poll; drain queue when ready
wake_mac:      xx:xx:xx:xx:xx:xx      # WoL safety net
task_types:    [tagging, translation, triage, extraction_fallback]
cost_tier:     free
enabled:       true
```

**Availability-driven execution is the primary mode.** The laptop is used daily, so rather than waking it on a schedule, the Pi polls its endpoint and drains queued LLM work whenever it is up. Wake-on-LAN becomes the safety net, not the main path.

```
every 5 min:  poll http://onelaptop.local:PORT/health
  ├─ ready + work queued  → drain queue
  └─ down / loading       → skip

daily 03:00 (safety net):
  if no local run in 24h AND work is queued:
     ├─ send WoL → poll /health up to 3 min
     ├─ ready → run → suspend box
     └─ down  → route to hosted, or defer
```

**Three llama.cpp specifics to design around:**

1. **Contention.** The server has a fixed number of parallel slots (`--parallel`). Meridian saturating them while the laptop is in interactive use will be felt immediately. **Run a dedicated llama.cpp process on its own port for Meridian** rather than sharing the one serving editor tooling — sharing also makes diagnosing request hangs much harder.
2. **One model per process.** If Meridian wants a different model than the interactive server is holding, that is a second process, not a config change. Same conclusion as above.
3. **Cold start.** The first request after load pays model-load time, potentially minutes for a large quant off disk. Poll `/health` until ready rather than firing a request and timing out.

**What it's genuinely good for:**

| Task | Local model | Why |
|---|---|---|
| Attribute tagging | Good fit | Schema-constrained, high volume, error-tolerant |
| Translation | Good fit | Mechanical |
| Triage / relevance | Good fit | Cheap, tolerant |
| Relation extraction | Acceptable fallback | Noisier edges; hosted preferred (§2, principle 3) |
| Gap / analogical analysis | Poor fit | Hardest reasoning; quality gap most visible here |

Routing local-first for the first three and hosted for the last two cuts recurring cost substantially while protecting edge quality where it matters.

**It restores the original offline requirement.** With a local model in the registry and hosted agents marked preferred-but-optional, a network outage degrades the loop rather than halting it. This was the capability traded away in §4; the local tier buys it back at the cost of hardware.

**The real caveat is tool calling, not speed.** Local models handle structured tool invocation noticeably worse than hosted ones — malformed JSON, invented tool names, dropped fields. Mitigations, in order of preference:

1. **Constrained decoding** — GBNF grammars (llama.cpp) or a schema-enforcement library, so malformed output is impossible rather than merely unlikely
2. **Parse structured output** rather than relying on native tool-calling
3. Server-side validation catches the rest — already required for hosted agents (§11.4), so this is free

**Hardware, if building:** a 32B at Q4 needs roughly 20GB, so 24GB VRAM (e.g. a used 3090) or a unified-memory Mac. CPU-only inference works but at speeds that make even batch use painful.

### 11.8 Prompt injection through the pipeline

This is self-inflicted: the crawler fetches arbitrary web content, which is then fed to a model holding write tools. A page containing injected instructions is a live attack path.

**Mitigations:**

1. Wrap all retrieved content in explicit untrusted-data framing; never let scraped text occupy an instruction position
2. **Validate writes server-side** — `add_edge` rejects non-existent nodes; `enqueue_seed` enforces a domain allowlist and per-run cap
3. Log every write with its motivating source chunk — bad edges traceable and reversible
4. Seed injection executes automatically — server-side validation is the control, not human review (§10.2). The seed log makes bad injections visible and reversible after the fact

Server-side validation is load-bearing. Tunnel security (no inbound ports, Access service tokens, rate limits) is the easy part.

### 11.9 Cost control

There is a compounding feedback loop in this design that nothing else caps:

> gap analysis emits seeds → seeds become crawl targets → more documents ingested → larger batch tomorrow → more tokens consumed → more seeds emitted → …

Concretely: 50 documents on day 1 produce ~40 seeds; each seed yields ~5 documents, so day 2 has ~200 documents to reason over, producing ~60 seeds. It compounds until crawl capacity saturates, with inference cost rising alongside. Because the loop is unattended, the first signal would be the bill.

**Controls:**

- **Token budget per run** — if the day's new chunks exceed it, process highest-novelty first rather than everything. Degrades gracefully instead of overspending
- **Seed cap per run** — already enforced server-side (§11.4); size it against the budget rather than picking a number arbitrarily
- **Monthly ceiling with hard stop** — on breach, route to the local tier (§11.7) or pause synthesis while ingestion continues uninterrupted
- **Log cost per run and alert on trend**, not only on absolute breach — a 20% week-on-week rise is the useful signal, well before the ceiling is reached

The local tier reduces exposure but does not remove the need for caps: compounding affects crawl volume and batch size regardless of who does the inference.

### 11.10 Orchestrator state — no framework

Synthesis runs must survive a crash mid-flight. This is handled by a plain table, not a workflow framework:

```sql
CREATE TABLE runs (
  run_id        BIGSERIAL PRIMARY KEY,
  started_at    TIMESTAMPTZ,
  stage         TEXT,        -- pull | extract | tag | score | seed | done
  last_chunk_id TEXT,
  tokens_used   INT,
  status        TEXT,        -- running | done | failed | deferred
  error         TEXT
);
```

A crash at `stage='tagging'` resumes there on the next wake. Combined with the rule that **the high-water mark advances only after writes commit** (§6.3), this gives full resumability in roughly 200 lines, with no abstraction layer between the orchestrator and its validated tool calls.

### 11.11 Credentials

**API keys and bot tokens never go in the database.** The database is snapshotted off-device for backup (§13.4), and keys stored there travel with every snapshot.

Keep them in environment variables or Docker secrets; the agent registry row stores only the *name* of the variable to read. Same for the Telegram bot token.

The orchestrator holds credentials locally and is never reachable through the tunnel — only the read/write MCP surface is exposed (§11.4).

### 11.12 Model provenance and reprocessing

Every artifact records which model produced it, so quality can be improved retroactively as better models become available.

```sql
-- on every edge, tag, and attribute assignment
produced_by     TEXT,          -- agent_id
model           TEXT,          -- exact model string
quality_tier    INT,           -- ordinal from the registry
produced_at     TIMESTAMPTZ,
schema_version  INT
```

`quality_tier` is an explicit ordinal in the agent registry (local small = 1, local large = 2, hosted mid = 3, hosted frontier = 4). **It is distinct from `cost_tier`** — cheap and good are different axes and conflating them makes routing decisions wrong.

**The invariant: quality tier only moves up automatically.** A lower tier never silently overwrites a higher one.

#### Upgrade path

When a higher-tier agent connects, query for artifacts produced below it and offer to re-derive. Telegram prompt with inline buttons:

> 1,240 edges and 3,100 tags produced by `local-llamacpp` (tier 2). `hosted-frontier` (tier 4) now available.
> **[Reprocess all]** · **[Sample 100 first]** · **[Contested + high-degree only]** · **[Skip]**

*Sample first* is usually the right choice — it reveals the disagreement rate before committing budget to a full pass. Prioritise by value when reprocessing partially: contested nodes, high-degree nodes, and anything feeding current written output.

Reprocessing re-derives from **source chunks, never from prior model output** (§2, principle 4), so it corrects errors instead of compounding them.

#### Downgrade guard

If only a lower tier is available and tonight's batch touches higher-tier artifacts, ask before running:

> Batch touches 340 nodes previously tagged at tier 4. Only tier 2 available.
> **[Run anyway]** · **[Tier-2-safe tasks only]** · **[Defer]**

**Default to defer.** Availability accidents must not degrade the graph — this is the same reasoning as the fast-loop invariant: missing a night of synthesis costs little, corrupting edges costs a lot.

#### Free evaluation data

Reprocessing produces old-vs-new pairs. **Log disagreements rather than silently overwriting.** This yields the local model's real error rate against a frontier model *on this corpus* — which is the edge-precision measurement §14.1 requires, obtained without separate manual sampling.

It also validates the task routing in §11.7 empirically: if tagging disagreement is 4% and relation extraction is 28%, the local-first/hosted-first split is confirmed by data rather than assumption.

### 11.13 Report generation (drafting jobs)

Manually triggered, not autonomous — the same class of action as enrichment (§6.6) and reprocessing (§11.12): expensive, optional, and initiated from the UI or Telegram, never scheduled. This does not reopen the "don't automate reading" tension (§16) — a report the user explicitly scopes and reviews is consumption they asked for, not unattended synthesis.

**Scope first.** A report job is always bound to an existing scope — a saved view, a coverage-dashboard cell, the contested list, or a path-mode result between two nodes — plus a free-text question. An unscoped "summarise the whole graph" option is deliberately not offered; §14.3's ten-questions exercise is the intended on-ramp for framing a good question.

**Coverage pre-flight, before cost estimate.** Before a job is submitted, run `coverage_report` (already exists, §11.6) against the requested scope and surface the result — sources counted, tagged fraction, source-tier spread, and staleness (§9) per dimension in scope. Thin or stale dimensions are flagged, not blocked: the user can proceed anyway, in which case the drafting prompt is required to carry the same coverage summary and is instructed to hedge or mark gaps explicitly in the output rather than asserting confidence the evidence doesn't support. This is the same move as §8's stance/hedging extraction, applied to the system's own generated text instead of only to ingested sources.

**Async, same pattern as §11.5.** `submit_report_job(scope, question)` → `job_id`; `get_job_status`; `get_job_result` returns the draft plus its citation set. Routes through the agent registry's `drafting` task type (§11.3) — strongest available agent, long context, infrequent — so it inherits the existing cost estimate, fallback chain, and per-run budget accounting (§11.9) with no new routing logic.

**Output.** Markdown with inline citations resolving to node/chunk IDs, exported the same way as annotations (§12.5: Markdown + BibTeX). A report is a draft the user reads and edits — it is never written back into the graph.

```sql
CREATE TABLE reports (
  job_id            BIGSERIAL PRIMARY KEY,
  scope             JSONB,          -- saved view id, coverage cell, or node pair
  question          TEXT,
  coverage_snapshot JSONB,          -- coverage_report output at submit time
  status            TEXT,           -- queued | running | done | failed
  output_path       TEXT,
  produced_by       TEXT,
  model             TEXT,
  quality_tier      INT,
  created_at        TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ
);
```

---

## 12. Interface

The graph UI is the primary artifact, not an inspection tool — this is where the learning actually happens.

### 12.1 Rendering stack

**Sigma.js v3 + graphology.**

- **Sigma** renders via WebGL, so it stays smooth well past where canvas libraries (Cytoscape.js) start to struggle
- **graphology** is the in-memory graph model underneath — and it carries the algorithms Postgres adjacency tables cannot do: Louvain community detection, betweenness centrality, shortest path, connected components

That second point resolves the gap left by choosing Postgres over Neo4j. Load a *filtered subgraph* client-side (a few thousand nodes), run the algorithms in the browser, and get community clustering and path-finding without a graph database. The server handles retrieval and filtering; the client handles topology.

Cytoscape.js is the reasonable alternative — gentler learning curve, more built-in layouts — if WebGL performance turns out not to matter.

### 12.2 Never render the whole graph

A few hundred thousand nodes on one canvas is a hairball carrying zero information. The pattern is **focus + expand**:

- Land on one node; show neighbours to depth 1, capped at ~30, ranked by edge weight
- Click any neighbour to expand; breadcrumb trail back
- Live filters on the canvas: topic, attribute, source tier, date, contested-only
- **Path mode** — pick two nodes, show connecting routes. This is where cross-topic links surface, and it serves the core goal most directly

### 12.3 Multiple views over one query result

The API returns a normalised subgraph; different renderers consume it. A view switcher, not separate features:

| View | Answers |
|---|---|
| **Node-link** | How is this connected? |
| **Table** | What exactly do I have, sortable and exportable? |
| **Adjacency matrix** | Which clusters are dense? Where are the gaps? |
| **Timeline** | How did this evidence accumulate? Where is it stale? |
| **Coverage grid** | Topic × dimension, thin cells highlighted — the "what next" view |

The matrix and coverage grid are the ones people skip and then miss: both make *absence* visible, which node-link diagrams are bad at, and absence is what gap analysis acts on.

### 12.4 Wiring the LLM to the canvas

The chat panel calls the same curated MCP tools (§11.6) — no separate retrieval path. What makes it feel integrated is **bidirectional binding**:

- **Question → canvas.** The agent's answer carries `entity_ids`; those nodes highlight, and the canvas can jump to that subgraph. The answer and the picture stay in sync.
- **Canvas → question.** Right-click a node for contextual prompts: *"how does this relate to on-demand buses?"*, *"what disagrees with this?"*, *"summarise the evidence here"*. Selection becomes query context automatically.
- **Answers cite nodes, not just chunks** — every claim traceable to graph elements and, through provenance, to raw sources.

An escape hatch alongside the curated tools: `run_readonly_query(query, limit)`, enforced by a read-only database role with a statement timeout and row cap. Watch which queries the agent writes there — those are the next curated tools.

### 12.5 Required features

- Hybrid search (pgvector + `tsvector`, fused by reciprocal rank), with filters applied *before* the vector search
- Three entry points: search box, coverage dashboard, contested list
- Node detail panel: description, attribute tags with confidence, supporting chunks with source and tier, contested edges, raw file links, own annotations
- **Annotation as first-class nodes** — my own notes and edges, tagged as mine. Over months this becomes the highest-quality layer in the system and the one that actually reflects my thinking. Build the affordance early or it won't get used
- **Figure panel** — thumbnails linked to the node, with page-accurate links to raw files (§6.6)
- **Enrichment controls** — pending counts by type, batch trigger for VLM/OCR passes (§6.6)
- **Saved views** — a filter set plus focus node, named and re-openable
- Steering controls (weight vector, pins, boosts, add/archive topic — §10.2); seed review queue
- **Report generation** — submit a drafting job scoped to a saved view, coverage cell, or contested slice (§11.13); coverage pre-flight warning before submit, async status, Markdown+BibTeX output
- **Notifications panel** — job completions (reports, enrichment, reprocessing), seed/gazetteer approvals, alerts; filterable by type and by surface (Explore vs Admin). The in-app counterpart to the Telegram digest (§13.3) — not everyone wants to check a phone while already at the screen

**Optimize for legibility over volume.** The bottleneck is reading time, not generation. 500 well-described nodes beats 5,000 unread ones.

**Export:** BibTeX (citations) and Markdown (notes) — avoid trapping material in a bespoke store.

**Observability:** daily health line — queue depth, fetch success rate, novelty pass rate, edges added. Unattended systems fail silently; without this the Pi can crawl 404s for a week unnoticed.

### 12.6 Surface split

Two surfaces. **Explore is where thinking happens; Admin is where steering happens.**

| Explore | Admin |
|---|---|
| Graph canvas, focus + expand | Topic weight sliders (% normalising to 100) |
| Hybrid search | Pins, boosts with expiry |
| Node detail, figures, provenance | Seed log and manual injection |
| LLM chat panel, bound to canvas | Agent registry, model routing |
| Annotation | Enrichment and reprocessing triggers |
| Coverage dashboard, contested list | Run history, health, fetch policy per domain |
| Saved views | Gazetteer approvals |

**Annotation belongs in Explore, not Admin.** It is part of reading; putting it behind a control surface means it will not get used, and it is the highest-value layer in the system (§12.5).

Most of Admin is CRUD over config tables that already exist — `topic_config`, agent registry, `enrichment_queue`, `runs`, `gazetteer`. Generated forms are fine. Effort belongs in Explore.

**Auth is deferred, but make two choices now** so adding roles later is middleware rather than a refactor:

1. **Route prefixes by mutation.** `/api/explore/*` for reads, `/api/admin/*` for writes and control. Role-gating then becomes a single middleware check on a path prefix.
2. **Separate database roles.** Explore reads through a read-only Postgres role — the same enforcement `run_readonly_query` needs (§12.4), so it is being built anyway.

Until app-level auth exists, **Cloudflare Access is the auth.** The app is never exposed without it.

---

## 13. Operations without remoting in

**Design goal: SSH is for breakage, not for running the system.** If routine operation requires a terminal, the system will quietly stop being used.

### 13.1 Config lives in the database, not in files

Topic weights, schedules, domain allowlists, agent registry, fetch policy, seed caps — all rows in Postgres, editable through the UI. YAML files seed defaults at first boot only; they are not read thereafter. Nothing routine should require editing a file on the box.

Corollary: **no cron files.** The scheduler reads its timetable from the DB so schedule changes are a UI action.

### 13.2 Everything operational is a UI action or an MCP tool

| Task | Instead of SSH |
|---|---|
| Adjust topic weights | UI slider / `set_topic_weights` |
| Pause or boost a topic | UI toggle / `pause_topic` |
| Add or archive a topic | UI / `add_topic`, `archive_topic` (§10.2) |
| Draft a report from a view | UI / `submit_report_job` (§11.13) |
| Trigger a run early | Button / `trigger_run` |
| Check what happened last night | Run summary in UI |
| Inspect failures | Log view, filtered |
| See what seeds ran | Seed log (§10.2) |
| Inject a seed | UI / `enqueue_seed` / `/seed` |
| Retry failed fetches | Bulk action in UI |
| Change model / provider | Agent registry row |
| Adjust fetch behaviour | `fetch_policy` table, global or per-domain (§6.4) |

Exposing these as **both** UI controls and MCP tools means an external agent can also self-manage — e.g. an interactive session can check health and trigger a backfill without me touching anything.

### 13.3 Telegram — reporting and control

One channel for both directions. Telegram is chosen over Discord/ntfy because its inline keyboards make intervention possible from a phone — steering, enrichment triggers, and reprocessing prompts without opening the UI.

**Outbound:**

- **Daily digest** after each synthesis run: queue depth, fetch success rate, novelty pass rate, edges added, seeds proposed, tokens/cost, errors
- **Alerts** on *sustained* conditions only — fetch success below threshold for an hour, no successful run in 48h, disk above 80%, agent failures repeated. Single-event alerting teaches me to ignore the channel, which is the real failure mode
- **Approval prompts** with inline buttons: proposed seeds, attribute proposals, gazetteer additions, entity-merge adjudications

**Inbound commands:**

```
/status                      health line + last run summary
/weights                     current topic weights
/boost <topic> <x> <weeks>   temporary multiplier with decay
/pause <topic>               maintenance mode
/seed <query> [topic]        inject a seed directly
/run                         trigger a synthesis run now
/contested [topic]           top contested nodes
/tiers                       artifact counts by producing model
/reprocess [sample|all]      re-derive lower-tier artifacts
/enrich [figures|ocr|all]    run pending VLM/OCR enrichment
```

Bot token in the environment, not the database (§11.11). Restrict to a single allowed chat ID — the bot can trigger runs and change steering, so it is a control surface, not just a notifier.

### 13.4 Self-healing so absence is safe

- **systemd** units with `Restart=always` for ingestion, orchestrator, and web app — survives crashes and reboots unattended
- **Exponential backoff** on fetch and API failures; a dead domain must not spin the queue
- **Deferred runs**: if the model API is unreachable, skip and retry next cycle. Ingestion continues regardless — the fast-loop invariant means an agent outage costs synthesis, not acquisition
- **Automatic snapshots** off-device on a schedule (`pg_dump`; WAL archiving if point-in-time recovery matters)
- **Watchdog**: if no successful synthesis run in N days, alert

### 13.5 When SSH is actually needed

Keep a path in — Cloudflare Tunnel with SSH over Access, or Tailscale — but treat needing it as a signal that something operational is missing from the UI. Track those instances; each one is a feature request.

---

## 14. Evaluation and conduct

### 14.1 Knowing whether it works

Without measurement, tuning is blind — there is no way to tell whether a schema change helped or hurt.

- **Held-out question set.** 20–30 questions with known good answers, written at cold start and re-run monthly against the graph. Cheap, and the only real regression test
- **Edge precision sampling.** Read 30 random edges monthly, count how many are wrong. This number matters enormously for anything written later — a graph with 30% bad edges cannot ground a paper. Reprocessing runs (§11.12) supply much of this for free as old-vs-new disagreement rates
- **Entity resolution audit.** Sample merges and near-misses; conflation is invisible once done, so it must be looked for deliberately
- **Track alongside schema changes**, so the effect of an attribute addition or retirement is attributable

### 14.2 Crawling and copyright conduct

- Identify the user-agent honestly; respect robots.txt and per-domain rate limits
- Do not route around paywalls. Government and academic publishers tolerate polite crawling and respond badly to the alternative
- Raw retention for personal research is fine; **redistribution is not**. Keep the raw store private, and cite rather than reproduce in any published output

### 14.3 Design exercise before building

Write down the ten questions most worth asking this system in a year — *"which interventions work in equatorial high-density cities but not elsewhere?"*, *"where do walkability and on-demand transit findings contradict each other?"* — then check the schema can actually answer them.

This backwards pass reliably surfaces a missing node type or edge attribute, and does so far more cheaply than discovering it in month four.

---

## 15. Deliverables and build order

> Directory layout, container topology, database roles and build commands are specified separately in **meridian-project-scaffold.md**.

The system is useless until the loop closes. Build thin end-to-end first.

| Phase | Deliverable | Checkpoint |
|---|---|---|
| **0** | Cold start: attribute schema, topic labels, 15–25 hand-seeded sources, domain allowlist | Seeds quality propagates through everything |
| **1** | Queue + fetcher + extractor + Postgres + raw retention | Ingestion runs unattended for 48h without failing |
| **2** | Embeddings + search UI | **Honest checkpoint** — is searching my own corpus already useful? |
| **3** | MCP server, read tools only | A connected model can retrieve usefully |
| **4** | Graph + write tools + server-side validation | **Loop closes here** |
| **5** | Frontier expansion + novelty gate + coverage scoring | Now autonomous |
| **6** | Graph UI + annotation | The payoff layer |
| **7** | Attribute audit, analogical expansion, contradiction tracking, temporal flags | Full design |

**Phase 2 is the real go/no-go.** If searching the corpus isn't already valuable with no LLM involved, the later layers won't rescue it — and that's discovered in a weekend rather than a month.

---

## 16. Known risks and open questions

| Risk | Mitigation | Residual |
|---|---|---|
| Self-confirming graph — model reasons over its own prior output | Re-derive from source chunks; confidence + provenance on tags; periodic re-tagging | Partial. Drift detectable via old/new tag contradictions |
| Prompt injection via scraped content | Server-side write validation, untrusted-data framing, capped seeds | Manageable if validation is genuinely server-side |
| Supply-chain / CVE exposure in crawler stack | Pin versions, loopback-only binding, no tunnel exposure, watch release notes | Real and ongoing — Crawl4AI has had frequent security releases (§6.4) |
| Cold-start seed quality propagating | Careful manual seeding | Real. Worth spending an evening on |
| Publication bias — best AV/policy material is non-public | Acknowledge explicitly in any written output | **Unresolvable.** System is structurally biased toward what gets published — skews optimistic and Western |
| Topic drift from autonomous expansion | Relevance decay, floor guarantees | Loosened deliberately: for exploration, drift is a feature |
| Entity fragmentation — duplicate nodes for the same entity | Write-time resolution (§5.5), acronym seeding, merge audits | Real. Bad merges are harder to detect than duplicates |
| Runaway cost from the seed→ingest→cost feedback loop | Per-run token and seed caps, monthly ceiling, trend alerting (§11.9) | Manageable if caps are set before first autonomous run |
| Filter bubble from citation-following | Diversity seed budget, stance-imbalance counter-seeding (§7.4) | Reduced, not eliminated — the crawl still starts from my seeds |
| Attribute semantic drift | Periodic re-tagging surfaces inconsistency | Choosing which meaning is intended remains a human call |
| Graph accumulates unread | — | **The central tension** (below) |

### The central tension

The stated goal is autonomy with minimal intervention; the stated purpose is personal learning. These pull against each other. A fully unattended system produces an excellent graph that hasn't been read — and the reading *is* the learning.

**Resolution: autonomous acquisition, deliberately non-autonomous consumption.** Automate everything mechanical — crawling, tagging, coverage scoring, schema evolution, seed generation. Do not automate reading. Steering reduces to occasionally rewriting a weight vector, which is a one-line intervention rather than a management burden.

Two things also resist automation on quality grounds, not effort:

- **Relevance judgment** — cheap for a human, hard to specify, and upvotes are the best training signal the priority scorer will ever get
- **Attribute semantics** — audits optimize for internal usefulness (does it discriminate, does it get used), not for whether it matters to the goal. Reading the audit log monthly — not acting on it, just reading — is enough to catch divergence.
