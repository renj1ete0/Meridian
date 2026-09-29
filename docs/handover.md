# Handover

**This file is not the task list.** [TASKS.md](../TASKS.md) is the source of truth for
*what* to build and [AGENTS.md](../AGENTS.md) for *how* to write it. This document
exists for the third thing: the working knowledge that is true about this repository
but written down in neither, and that otherwise has to be rediscovered by whoever
picks the work up next.

Everything below is something that cost real time to learn. If you learn another one,
add it here.

---

## 0. In flight at the end of 2026-09-24 — read before starting

> **2026-09-29 night — site review, `B-119`–`B-126` (`v0.156.11`).** Traps:
> - **`readable()` is display-only and conservative on purpose.** It joins a line break only
>   when the next line starts lower-case, the line ends on a comma or a joining word ("of",
>   "the"), and most breaks in the passage are mid-sentence. A looser rule, run over 300 stored
>   passages, merged headings and navigation labels into sentences. Re-run that sample
>   (`json_agg(text)` from `chunks tablesample`) before loosening it.
> - **`/api/explore/gaps` is cached in-process** (`api/cache.py`, 15 min, stale-while-revalidate).
>   A test that reads it must call `KEPT_GAPS.forget()` first, as `test_gaps.py`'s client does.
> - **Sitemap entries are boosted only when their path matches a topic**; most do not, so a
>   proven host's sitemap pages mostly sit at the bottom (-10). Measure before changing.
> - **The colour-token test scans tests too**: use names, not hex, for fake palette values.

> **2026-09-29 evening — runs 15–16, `B-115`–`B-118` (`v0.156.2`).** Proven hosts first
> (`B-115`) took run 15 to 35% of new pages on a topic and 4× run 14's count; sitemaps of proven
> hosts are mined hourly (`B-116`, `B-118`); four more search engines (`B-117`). Traps:
> - **The scheduler has its own image.** Rebuilding only `worker` left the scheduler without a
>   new module ("No module named worker.sitemapmine"). Build every app service:
>   `build worker embed scheduler bot orchestrator api web`, then `up -d --no-deps` the same.
> - **Every claim draws a topic.** A task queued with `topic=NULL` is claimable only by the
>   unscoped fallback, which never runs while any topic has work: `B-116`'s hundred sitemaps sat
>   at the top of the queue unclaimed. Test new task sources with a topic-scoped `claim_next`.
> - **Loop runs lift the embedding ceiling; normal operation does not.** After a run the
>   backlog is above 20k and the crawl pauses until it drains — expected, and the reason the
>   yield per embedded page, not fetches per hour, is the number that matters.
> - **Proven hosts dominate followed-link fetches now** (84% in run 15). Watch the tier mix and
>   host count: politeness and the per-host cap of 500 are what keep it from narrowing further.

> **2026-09-29 afternoon — loop runs 12–14, `B-113`, `B-114` (`v0.155.2`).** Traps:
> - **`docker compose up -d --build <services>` recreates Postgres too** when its image is
>   local; `embed` then crashes on name resolution until it is back (7 restarts). Deploy app
>   services with `--no-deps`.
> - **The query task type is `query`, not `search`**: `queue.task_type='query'` for pending
>   searches; `seed_source='search'` marks the result URLs.
> - **`B-114` blocks by note** (`fetch_policy.updated_by='refusals'`) and lifts after 30 days.
>   `en.wikipedia.org` is among the blocked: it refuses clients with no contact address. Once
>   `MERIDIAN_CONTACT_EMAIL` is set, unblock it in Admin; a hand unblock gets a fresh window.
> - **Yield by how a page was found** is the number to watch (runs 12–14 comparison): search
>   ~40–50% on a topic, followed links ~2%, mostly the pre-`B-91` backlog.

> **2026-09-29 — `v0.155.0`: RUM ranking, the contested list, `B-53` measured.** Live locally;
> pushed to GitHub, **not on GHCR** (operator: hold GHCR).
> - **`B-65`: the database image now carries `rum`** (PGDG `postgresql-17-rum`, also published
>   for arm64). **Rebuild `meridian/postgres` before migrating**, or `CREATE EXTENSION rum`
>   fails on a missing control file. The migration builds `ix_chunks_search_rum` in under a minute
>   on the local corpus, holding chunk writes meanwhile. The lexical arm takes RUM's best 1000
>   and reorders them by `ts_rank_cd`; `LEXICAL_POOL` explains the measurement.
> - **The planner now answers plain `@@` from RUM too**, even on the live corpus, so the GIN
>   index may be redundant. Check the watch and area-view plans before dropping it.
> - **`P6-10`**: `/contested` and `GET /api/explore/graph/contested`. The live graph has no
>   contested pair yet, so the page shows its empty state; the populated layout was only
>   seen with injected data (Playwright `page.route`).
> - **`B-53`** is now an operator decision; the numbers are in TASKS and
>   `meridian-calibration/b53/notes.md`. `translation_lookups` is empty: `B-52` has never run,
>   for want of `MERIDIAN_CONTACT_EMAIL`.
> - Running `uv run pytest` directly (not `make test`) needs `.env.dev` exported
>   (`set -a; . ./.env.dev; set +a`), or API tests fail on `PG_RO_URL is not set`.

> **2026-09-27 night — `v0.154.0`: "Ask the graph" wired, not yet run against a model.**
> `P6-06`/`P6-07` stay open until a real model has answered. What exists:
> - **Panel** (`web/src/explore/AskPanel.tsx`): toggle bottom-right on every reading surface
>   except Admin; the node page registers itself as removable context via `useAskSubjects`.
>   Earlier questions come from `GET /api/explore/chat/threads`; asking is
>   `POST /api/admin/chat/ask` (a write: it spends tokens and stores a thread).
> - **Server** (`meridian_core/chat.py`): hybrid search on the question plus the context names,
>   ≤ 8 passages (≤ 2 per source), then graph edges whose `supporting_chunk_ids` overlap them.
>   The prompt is framed (`framing.py`); the answer's `[n]` and `{Nn}` markers are checked and
>   anything not in the given context is removed before storing (`check_answer`).
> - **Model choice is configuration.** Task type `chat`; seeded row `local-chat`
>   (`openai_compatible`, **disabled**) with endpoint `${LOCAL_CHAT_LLM_URL}` and model
>   `${LOCAL_CHAT_MODEL}`, expanded at call time by `provider.resolved`. Admin → Agents can
>   now change any row's model string. `MERIDIAN_CHAT_DAILY_TOKENS` (default 200000) caps
>   a UTC day.
> - **To try it:** set `LOCAL_CHAT_LLM_URL=http://host.docker.internal:<port>/v1` and
>   `LOCAL_CHAT_MODEL=<name the server reports>` in the env the local compose reads, `up -d
>   --no-deps api`, then Enable `local-chat` in Admin → Agents. The local compose maps
>   `host.docker.internal` for the API. In production the API has **no route out**: the
>   model server must be reachable on `lan` (two-board layout) with an nft `lan_allow` entry.
> - **Traps:** a new enum column needs a `schemas/enums.py` alias and an `ENUM_PAIRS` row
>   (`test_drift`); an env var only read through a registry `${VAR}` must be listed in
>   `REGISTRY_VARIABLES` in `test_compose_topology.py`. With no agent enabled, "chat" appears
>   in Admin's "Nothing serves" line: that is correct.
> - **Not pushed to GHCR / not promoted.** The code is pushed to GitHub only.

> **2026-09-27 evening — `v0.152.2` deployed.** Route mode, "this is noise", watched
> questions, plain wording, label floor 0.50 (relabelled the corpus in 6 min; `topics` does it
> anyway when the basis changes). Traps:
> - **The search queue runs dry quietly.** Run 9's directed share fell from 32% to 6% with no
>   error anywhere: 8 results pending. Check `url/search/pending` in `snapshot.sh`'s start file
>   before trusting a run's search share. Topics with no approved vocabulary exhaust their
>   queries (`B-103` gives them description facets).
> - **A unique constraint on edges breaks merges** (`B-60` entry): merge repoints edge by edge.
> - **Links queued now are fetched days later** — ~211k older frontier links sit ahead of them,
>   so a 1h run cannot measure anything about links queued in the same hour.
> - **`.venv` accumulated ~100 stale `meridian_*.dist-info` folders** with no RECORD, which
>   printed a page of uv warnings per command; removed them.
> - The timetable row for a new job must be inserted by hand on the live stack.
> - **Stale `meridian_*.dist-info` folders reappear** after each version bump (the old one loses
>   its RECORD), and uv then prints warnings on every command. Harmless; delete the ones with no
>   RECORD. Cause not found.
> - **Search engines rate-limit us, silently.** Run 10: every web engine behind SearXNG was
>   suspended (too many requests, CAPTCHA); only news answered. Check with
>   `docker compose exec worker python -c` hitting `$SEARXNG_URL/search?format=json` and read
>   `unresponsive_engines`. `B-107` retries throttled queries and paces them 15 s apart; 93 of
>   the day's empty queries were set back to pending by hand (error "B-107: revived").
> - **The science category still answers when web engines refuse.** `!science <q>` (Google
>   Scholar, Semantic Scholar, arXiv…) returned 50 results a query and ~34 new ones; seeding asks it
>   for every concept and facet since `B-111`.
> - **An edge constraint that must hold across a merge is deferred** (`uq_edges_claim`): check it
>   with `SET CONSTRAINTS uq_edges_claim IMMEDIATE` in a test, or it is never checked before a
>   rollback.

> **2026-09-27 afternoon — run 8, the Map as a map, UX review (`v0.149.3`, deployed).**
> Run 8 split yield by how a page was found: search 70% on a topic, followed links 4%. The
> queue never recorded a followed link's parent (`B-91` now does) — run 9 should measure
> child yield by parent label before gating link-following (`B-92`). The Map's wheel now
> zooms (level follows at 1.7× and 3.4×); levels share coordinates because each finer level
> is laid out inside the coarser circles. Traps:
> - **Run the web suite before committing a schema change.** `B-89` added a source field and
>   broke the web drift test (`tests/api.test.ts`); fixed in `v0.148.16`.
> - **A Playwright handle to Find's input goes stale after a search** (the field re-renders
>   at a different size); query the input again after each submit.
> - **The concept graph is small (≈256 nodes)**, so Find's neighbourhood is often empty for
>   a subject that is well covered by passages. That is the graph's coverage, not a bug.

> **2026-09-27 — `B-89`, long documents embedded from a sample (`v0.148.13`, deployed).**
> After the overnight shutdown the backlog was ~153k, and over half of it sat in ~115 very long
> documents, nearly all reached by followed links and mostly off-topic, never labelled because
> labelling waited for every passage. Now a source's sample (first 16 passages, then every
> 16th; `chunks.in_sample`) is labelled first, and the rest waits in the `last` tier unless the
> sample scores ≥ `TRIAGE_FLOOR` (0.41). Sample labels are provisional
> (`sources.topic_sample_best` not NULL) and re-read once the whole text is embedded. Measured
> before building (script pattern in `meridian-calibration/loop/b89/`): sample-vs-whole on/off
> agreement 89–93%, so samples triage and never finalise. Traps:
> - **A bare `x < NULL` in a tier predicate drops the passage from every tier**, silently and
>   forever. The hold predicate spells out `IS NOT NULL`; a test guards it.
> - **The worker's rootfs is read-only**: `docker cp` into it fails. Pipe a script in with
>   `docker compose exec -T worker python - < script.py`.
> - `worker.retopic`'s report now prints "from a sample N, the rest held back M".

> **Start here — end of 2026-09-26 (+08, ~22:50).** `main` = `v0.148.12`, built and deployed;
> every container up, every timetable job `ok`, worker ceiling back at 20000. Overnight the
> crawl sits mostly paused behind the embedding backlog (~176k chunks, falling at roughly
> 120–290/min depending on chunk length); that is expected, not a fault. `seedsearch` now
> runs every 3h (live row set by hand, `B-87`).
>
> First things tomorrow:
> 1. Re-measure on-topic yield **by seed source** for runs 6 and 7 once their chunks are
>    embedded (query in `meridian-calibration/loop/run7/comparison.md`'s notes: join `sources`
>    to `queue` on `url_or_query = url`). Run 7's 64% is mostly search-found sources so far.
> 2. Ask the operator about `B-83` (label floor 0.45 lets generic government pages in; three
>    options in TASKS). Do not move it without them.
> 3. If the backlog is under ~20k, run another 1h loop run (export
>    `MERIDIAN_WORKER_MAX_EMBED_BACKLOG=200000` in the shell *before* `snapshot.sh start`).
> 4. Remaining `P6-42` (field "this is noise" action; shading by quality/freshness) and
>    `P6-44` (concept page labels, reader vs operator navigation).
> 5. The operator may want to reword the `B-79` commit message (it quotes a real link); it is
>    unpushed, so that is a history rewrite — their call.

> **2026-09-26 — UX and information audit, bf16 embedding, loop run 6.** An audit of what
> the Map shows found one of twelve top-level fields mostly on the topics (at
> 70%); the rest are 0–16%, legacy drift from before the 2026-09-24 fixes. Sources collected
> since then are 43–72% on-topic. The Map now shades fields by on-topic share (`areas.examined`,
> `on_topic`, `topic_mix`, counted at build time; a passage never examined is neither on nor
> off). Embedding was the bottleneck (190k chunks waiting, crawl paused): the CPU is
> memory-bound, so more processes do not help, but bfloat16 doubles throughput on a CPU with
> `avx512_bf16` (`B-77`). Traps:
> - **`snapshot.sh start` recreates the worker**, dropping a backlog ceiling that was lifted
>   only on the compose command line. Export `MERIDIAN_WORKER_MAX_EMBED_BACKLOG` in the shell
>   before `start`, or lift it after, and check with `printenv` in the container.
> - **Bare `uv run pytest` fails every integration test** with "PG_RO_URL is not set". `make
>   test` loads `.env.dev`; by hand, `set -a; . ./.env.dev; set +a` first.
> - **SQLAlchemy writes Python `None` into a JSONB column as JSON `null`**, not SQL NULL. Use
>   `sqlalchemy.null()` when NULL is meant; SQL reading such a column must check
>   `jsonb_typeof`.
> - **A statement binding one parameter per source** breaks at 32,767 (`B-84`: docdupes).
>   Join or batch instead.
> - **The label floor's band 0.45–0.48 holds over half of all labels** and is noisy on
>   generic government pages (`B-83`, the operator's call).
> - **The timetable has no Admin route.** `config/schedule.yaml` seeds it at first boot only;
>   `seedsearch` was moved to 3 hours on the live stack with a direct `UPDATE scheduled_jobs`
>   (and `next_run_at` reset, since it keeps the old interval's next run) — `B-87`.
> - **Search results were three times as often on-topic as followed links** since the crawl
>   fixes; the directed share is now pinned by a test to the measured values (`B-86`).

> **2026-09-25 close of day.** Also shipped from the operator's UI review: source titles
> cleaned at write and in bulk (`B-69`, `worker.retitle`), gazetteer terms typed by head
> word (`B-70`, `harvest --retype`), Map clusters named from a fixed list of fields of work
> and unique per level (`B-71`, `B-74`, `config/fields.yaml`, `worker.areas --name-only`;
> match *after centring* both sides, or a few generic labels name everything), the topic
> web (`B-72`), saved views with topics (`B-73`), About and Matrix views. The reader-journey
> plan is `P6-41`..`P6-44` and `P0-18` in TASKS. In flight at shutdown: two agent branches
> (semantic zoom; answer page) and run 5, which must be rerun.

> **2026-09-25 evening — the optimisation loop and what it found.** Runs 3 and 4 (2h each)
> tested the directed claim share (`B-61`) and embedding by value (`B-66`). Run 3 found
> that a cited-paper DOI outranking every page let one topic spend its whole crawl share on
> lookups (`B-68`: the ordinary draw now claims pages only; lookups alternate with search
> results in the directed slot), and that three quick throttled retries wrote papers off
> (`B-67`: providers cool; throttled DOIs retry after the cooldown). Run 4 confirmed both.
> Traps: *labels wait on embedding*, so in-window on-topic yield is unmeasurable unless the
> first tier is served newest first (`B-75`); *on a CPU a bigger embedding batch is
> slower*, so do not raise `MERIDIAN_EMBED_BATCH_SIZE` there; a window's throughput is not
> comparable when image builds or test suites share the CPU.


**Three delegated agents** worked concurrently on the connection screens, each
in its own worktree under `.claude/worktrees/` with its own Postgres (compose
projects `meridian-map`, `meridian-find`, `meridian-gaps` on ports 21121,
21131, 21141 — they were told to tear these down; check `docker ps`). They were
told not to bump versions or touch CHANGELOG/TASKS, and to commit tested
partials when stopped.

| Scope | Tasks |
|---|---|
| Map | `P6-30` areas, `P6-31` bridges, `P6-34` Map screen, `P6-35` steering from the map |
| Find | `P6-33` neighbourhood + Find panel, `P6-32` route |
| Gaps | `P2-22` question-set runner, `P6-36` Gaps screen |

To merge each: `git worktree list`, `git log --oneline main..<branch>`, read
every commit body, cherry-pick with `--no-commit` one task at a time,
re-parent any Alembic revision onto `main`'s head (`7fbdd2242063` at
handover, or whatever the previous merge left), run `make test` and the web
checks, then bump + changelog + `TASKS.md` in that task's commit. Screens must
be screenshotted against the mocks before they count as done (the agents were
told to; check).

**The live stack at handover** (`v0.124.0` deployed or deploying):

- **Crawler started** under the new rules (host gate, search seeding,
  evidence tiers). What to measure next time: share of fetches from search
  vs followed links, and the on-topic share of new sources (`retopic`
  report). This comparison is the point of the day's work and has not been
  made yet.
- **Embeddings are catching up.** The re-chunk superseded a large set of
  chunks, and `worker.embed` is embedding the new ones through the sidecar
  while a one-off container `meridian-reembed` (`docker logs
  meridian-reembed`) re-embeds old vectors under the `B-49` view. Both share
  one CPU sidecar, so this takes hours. Until it finishes, those chunks are
  missing from vector search (lexical still finds them) and their sources are
  not relabelled. Remove the container when it exits (`docker rm
  meridian-reembed`).
- **`translation_lookups` was empty at handover.** The first manual
  `worker.translate` ran on an image built before `B-52`. The timetable row
  runs it daily; to run it now, `docker compose -f docker-compose.local.yml
  run --rm --no-deps scheduler python -m worker.translate --once`.
- **Check the last deploy landed.** At handover `v0.124.4` was building,
  followed by `worker.requeue --apply` with the fix that recomputes queued
  links' tier priority. The first minutes of the widened crawl were still
  fetching followed links from academic hosts at the old top priority, queued
  before `B-50`. Verify: `SELECT priority, count(*) FROM queue WHERE
  status='pending' AND seed_source='frontier' GROUP BY 1` should show no
  academic-domain links at the old top value, and search results should now
  make up a real share of fetches. About half the first search results that
  failed were Cloudflare challenges, which the crawler does not try to beat
  (§6.4).
- **Translations need a contact.** `MERIDIAN_CONTACT_EMAIL` is unset on the
  local stack, Wikimedia answers anonymous clients 403, and `worker.translate`
  now refuses to run without it. So there are no non-English seeds until it is
  set. The same variable is what lets the DOI resolver use Unpaywall.
- **An unattended deploy chain was left running** at handover: after the
  `v0.124.4` build and requeue, it rebuilds at `v0.125.0`, migrates, runs
  `worker.docdupes --apply` (the operator left the call to the lead; it is
  reversible and deletes nothing), and restarts worker, scheduler, api and web.
  Check `SELECT version_num FROM alembic_version` (should be `f5b9328b0df8`)
  and `SELECT duplicate_reason, count(*) FROM sources GROUP BY 1`. Then enable
  the `docdupes` timetable row in Admin (it shipped disabled).

**P1-16 checkpoint:** a 12-hour unattended window from late 2026-09-24,
starting when the evening's deploy finished and snapshotted at both ends into
`~/Documents/gh/meridian-calibration/p1-16/`. Read `report.md` first next
session. If `end.txt` is missing, the machine was asleep or logged out when
the end timer fired; run `snapshot.sh end` by hand.

**The optimisation loop (operator's request, 2026-09-24):** run, then
re-check tagging and quality, re-steer, and run again. Keep going while each
iteration still improves on-topic yield, tagging coverage or question-set
scores materially, and stop after two runs without material improvement.
Work files go in `~/Documents/gh/meridian-calibration/loop/`. A session-only
scheduled prompt drives iteration 1 at about 11:47 on 2026-09-25. If the
session has ended, do it by hand: close run 1 (P1-16 `end.txt`), merge the
agent branches, deploy and run the backfills, write the findings, re-steer,
and start a 5h run 2. Never deploy or restart during a measurement window.

**Operator decisions, 2026-09-24:** topics stay **broad** at the start and the
crawl finds its way in. Descriptions are optional, a way to steer into more
defined spaces later, and never required. Undescribed topics are searched by
name. For other languages, **English where a site offers it, otherwise
whatever language the page is in** (`B-57`). The contact address for the
Wikipedia and Unpaywall lookups is still open (see TASKS, needs the operator).
- Deploying without restarting Postgres: as before, `build <svc>`, then
  `run --rm --no-deps tools alembic upgrade head`, then `up -d --no-deps
  <svc>`.

**New traps from this session:**

- **`docker compose start <svc>` restarts the old container, on its old
  image.** A service stopped for maintenance and left out of the `up -d
  --no-deps …` list comes back as the code from before the deploy. On
  2026-09-25 a whole test window ran the previous worker because of this.
  Bring a service back with `up -d --no-deps <svc>`, and check what a
  container actually runs (`python -c "import importlib.metadata as m;
  print(m.version('meridian-worker'))"` inside it), not the repository's
  `VERSION`.

- `pkill -f "alembic upgrade head"` also matches a `docker compose run …
  alembic upgrade head` for the *live* stack. Kill by PID.
- An `ALTER TABLE` on the dev database queues behind a running test suite,
  and while it waits Postgres blocks every new query on that table, so the
  suite then hangs as well. Don't migrate the dev database while a suite runs.
- Builds and test runs are several times slower while the embedding sidecar
  is busy. That is CPU contention, not a hang.
- SearXNG's Bing *web* engine returned results unrelated to the query (now
  disabled). If search quality drops, measure each engine's relevance before
  trusting its results. `B-51`'s commit shows the method.

## 1. Where the build actually is

**`v0.129.0` at the end of 2026-09-24.** Test counts move by the hour; run
`make test` rather than trusting a number here.

**What the corpus pipeline now does, end to end, with no model.** Fetch →
extract (a garbled PDF text layer goes to OCR, a soft-404 is junk) → clean
(menus, banners, running heads, debris cut around, `B-43`) → chunk → embed the
reader's view of the text (`B-49`) → novelty gate → topic labels from content
(`P2-21`) → duplicates at document level (`B-44`). The crawl decides where to
go next from that: per-host relevance learned from the labels (`B-48`), tiers
that need document evidence before calling anything scholarly (`B-50`), fresh
per-topic search queries every six hours including news and counter-evidence
(`B-51`), non-English queries in each language's own words (`B-52`), and an
English version fetched first where a page declares one (`B-57`).

**What reads it.** Find (hybrid search, with a neighbourhood panel beside the
results, `P6-33`), the node workspace (`P6-01`–`P6-03`), routes between
concepts with every hop labelled cited or similar (`P6-32`, API only until the
Map's route mode), Gaps (`P6-36`), the held-out question-set runner (`P2-22`),
and Admin. The Map (`P6-30`–`P6-35`) is being built.

**What is still thin.** The graph: a few hundred entities from the relay
agent, one batch at a time. Synthesis at corpus scale needs an API key or a
local model. The operator asked for the relay to be paced when it restarts,
to stay inside the account's token limits. Phase 2's go/no-go (`P2-09`) needs
the question set run against the live corpus. The operator's own questions
are in, and 30 drafted items still await review.

**The unattended run.** A 12-hour checkpoint of `P1-16` ran overnight
2026-09-24/25 (see §0), and the re-steer and re-run loop builds on it. The
full 48-hour sign-off is still open.

### The four processes

§6.1 draws the fast loop as one pipeline. It is not one, and holding the split in
your head explains most of the operational surprises:

```
worker.main       fetch → extract → chunk          24/7, no model, no vectors
worker.embed      embedding IS NULL → vector       24/7, needs the model or the sidecar
worker.novelty    novelty_checked_at IS NULL       Postgres and arithmetic only
worker.scheduler  the timetable in `scheduled_jobs` spawns the above as subprocesses
```

**Two of those are compose services and the rest are scheduled** — and getting
that split wrong is `B-25`. `worker.embed` has a `run_forever` mode and was
deployed as an hourly `--once` job anyway, which the scheduler then killed at
its 1800-second ceiling. `tests/unit/test_timetable_ownership.py` now fails if
a module is both an enabled row and a service, because two owners claiming the
same batches is the other way to get this wrong.

Each queue is a predicate on a column, so each pass is resumable with no state
outside the table, and any of them can lag the others without anything breaking.
What it costs is a window where a chunk exists, is not searchable, and is not yet
known to be a duplicate.

Three more passes are on demand rather than on the loop:

```
worker.sweep      retention report; deletes only with --apply
worker.harvest    §5.6 acronym definitions → gazetteer, unapproved
worker.retopic    topic labels from content (P2-21); hourly with --apply, report by default
```

### What exists that the older version of this document said did not

- **Retrieval.** `meridian_core/search.py` fuses a lexical arm (tsvector/GIN) and
  a vector arm (pgvector HNSW) with RRF. `_conditions()` is the single filter
  source for both arms, deliberately — two filter sites is how one arm silently
  returns material the caller excluded.
- **An API.** `/api/explore/*` on the read-only role, `/api/admin/*` on the
  read-write one. The prefix *is* the role boundary (§12.6) and a test asserts
  nothing under `/api/explore` accepts a write.
- **A UI that renders data.** Explore with search, a topic filter, the corpus
  counts, a since-last-visit delta, saved views, notifications and the reader's
  own notes; a source page at `/sources/{id}` with passages, figures, both
  exports and a note composer; a node panel at `/nodes/{id}`; and Admin with
  three screens (gazetteer, topics, domains).
- **Annotation** (`P6-05`), which is the only thing in the graph tables that has
  rows on a fresh install — a note is an `entities` row somebody wrote by hand.
  It is also the only write in the system whose author is assigned rather than
  declared — see the exceptions below.
- **An MCP read surface**, mounted on the same app, same read-only role, same
  provenance. Scoped tokens, a statement-timeout SQL escape hatch on a separate
  `meridian_guest` role, and Access JWT verification.
- **An embedding sidecar**, and as of `P2-19` the backfill uses it too, so a
  stack running both holds one copy of the weights rather than two.

### What still does not exist

- **A graph with anything in it.** `entities`, `edges`, `observations` and
  `attribute_values` are tables with DTOs, drift tests, provenance rules and,
  since `P4-16`, a path that writes them — exercised against a scripted model,
  not a real one. `P6-05`'s annotations remain the only rows a fresh install
  has, and they are written by hand. Apache AGE (`P4-01`) is not installed; it
  does support PG17 (v1.6.0), so it is not blocked on a Postgres downgrade,
  only on not swapping the image mid-deploy.
- **Any real LLM call.** `provider.complete()` exists and the stages call it;
  nothing in this repository has yet reached a model that generates text,
  because no deployment has had a key and a budget row at the same time. The
  orchestrator also has no service of its own (`P4-17`).
- **spaCy NER.** `P5-02` built the gazetteer and the `EntityRuler` patterns, and
  spaCy is an optional extra (`uv sync --extra ner`) that the worker image does
  not carry. `P5-01`'s frontier NER and TF-IDF are unbuilt.
- **OCR.** Scanned PDFs are detected and filed in `enrichment_queue`, and nothing
  ever runs that queue — §6.6 makes OCR user-triggered, so the rows wait for a UI
  to spend against them.
- **Anything that needs a *derived* node to exist.** The node panel (`P6-04`) is
  built, tested and reachable at `/nodes/{id}`, and the pipeline has put nothing
  in it. Likewise a saved view's `focus_entity_id`. Both were built ahead of the
  graph deliberately — their hard parts are about how a claim is presented, and
  those do not get easier by waiting for rows — but do not mistake "the screen
  exists" for "the feature works end to end".

  `P6-05` is the exception that proves it: annotations *are* entity rows, so a
  reader can fill `/nodes/{id}` with their own notes today. That is a real
  end-to-end path, and it is not the graph.

### The shape of the read path

```
GET /api/explore/search ──► paged_search (api/search_service.py)
                             │
                             ├─► RemoteEmbedder.embed_one()  ── sidecar, or absent
                             │                                   (absent ⇒ degraded)
                             ▼
                        search() (meridian_core/search.py)
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
        _lexical()                     _vector()
        websearch_to_tsquery           embedding <=> query
        ts_rank_cd                     HNSW, vector_cosine_ops
              └──────────────┬──────────────┘
                             ▼
                      rrf() fusion → hydrate → SearchHit
```

Both arms narrow through `_conditions()`, which always excludes superseded
chunks (`P1-32`) and, unless asked otherwise, near-duplicates and junk-tier
sources.

### Conventions that are load-bearing and easy to miss

- **Nothing is deleted.** A re-crawl supersedes chunks rather than deleting them
  (`P1-32`); a rejected gazetteer term keeps its row as a tombstone (`P6-13`); an
  archived topic keeps its weight (`P6-12`); a retention sweep reports and only
  deletes with `--apply`. The pattern is consistent and each instance has a
  different reason — follow the citation in the code.
- **NULL and empty are different facts** in at least three places:
  `sources.topic_labels`, `chunks.novelty_checked_at`, `sources.acronyms_harvested_at`.
  NULL means no pass has looked; empty means one looked and found nothing. Each
  distinction is what makes a backfill queue finite.
- **Admin fails closed.** `/api/admin/*` refuses everything with 503 unless
  Cloudflare Access is configured or `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` is set.
- **Run `uv lock` in the same commit as a version bump**, or the Docker build
  breaks. See §3.

### Three exceptions, all deliberate, all easy to mistake for bugs

- **`GET /api/explore/nodes/{id}` returns superseded chunks**, and it is the only
  read path that does. Everywhere else a superseded chunk is text the page no
  longer carries; there it is the text an attribute was *derived from*, and §2.4
  re-derives from source chunks — so the citation has to resolve even after the
  page changed.
- **Saved views read on `/api/explore` and write on `/api/admin`.** §12.6 splits
  by mutation, not by audience, and here that is the useful split: views are
  shared state with no per-viewer scoping, so a guest opens the owner's and
  cannot add to them. Saving therefore needs Access configured, or the
  anonymous opt-out.
- **Annotations take the same split, and `produced_by` is not an input.** Notes
  read on `/api/explore/annotations` and are written under `/api/admin`, for a
  sharper version of the same reason: a note is the one thing here that reads as
  the owner's own thinking, which makes it the worst thing on this system to be
  able to forge. So `AnnotationCreate` has **no** `produced_by` field and
  forbids extra keys — `meridian_core/annotations.py` assigns the reserved
  `human` id and nothing else can. `scripts/seed.py` refuses to register an
  agent under that id for the same reason, and that refusal will look like an
  over-zealous validation until you know why it is there.

  Two consequences that look wrong and are not. A note's `quality_tier` and
  `model` are **null**, because §11.12's tier is an ordinal over models and a
  person is not on that scale — "quality tier only moves up" must not become a
  rule about a person. And a note's `annotates` edges carry the note's own
  `supporting_chunk_ids`, duplicating what the node already holds; the node's
  copy is the source of truth (a note with no target has no edges at all), and
  the duplicate exists because *every* edge here names the chunks behind it.
  They cannot drift — `annotations.py` is the only writer and rewrites the edges
  from the note on every change.

## 2. Getting a working environment

```bash
docker compose -f docker-compose.dev.yml up -d postgres   # crawl4ai only if you need the browser
make migrate
make seed
make test
```

**`pdftotext` and `pdfinfo` must be on PATH** (Fedora: `poppler-utils`). They are
the PDF extractor (`P1-09`), and their absence raises rather than degrading — a
worker that had quietly lost poppler would store every PDF and extract none of them.
The PDF *tests* additionally need `ghostscript` for `ps2pdf`, and skip without it.

**Use `make test`, never a bare `uv run pytest`.** The Makefile does `-include .env.dev`
and exports it. Some tests shell out to subprocesses — `alembic check`, `scripts/seed.py` —
which read `PG_*` from the environment and cannot see it otherwise. They fail with
`PG_RW_URL is not set`, which reads as a code failure and is not one.

Without Postgres running, the integration tests **skip** rather than fail — currently
635 of 1910, a third of the suite. A green run that finished in a few seconds is a run
that tested almost nothing; read the skip count, not just the colour.

Crawl4AI is a 6GB image that runs a browser pool. Start it only when you are actually
exercising the browser path, and `make dev-down` when you stop.

**The dev database now holds a real crawl.** After `v0.16.0` a single run leaves a
few hundred pending frontier rows behind, which is the point — §6 asks for real
crawl snapshots rather than fixtures, and this is one. It also means a bare
`python -m worker.main` with no `MERIDIAN_WORKER_MAX_TASKS` will keep going for a
very long time. That is correct behaviour, not a runaway.

**Running the worker by hand.** `make test` exports `.env.dev`; nothing else does, so
the worker needs it sourced:

```bash
set -a; . ./.env.dev; set +a
MERIDIAN_RAW_ROOT="$PWD/.devdata/raw" MERIDIAN_WORKER_MAX_TASKS=4 uv run python -m worker.main
```

**Set `MERIDIAN_RAW_ROOT` or the worker writes to `/data/raw`,** which is the
container's bind-mount target and almost certainly not writable natively. It will not
crash — a fetch it cannot keep is retried and then failed with `storage_error:` — but
a run where every task retried three times and nothing was stored is this, not a bug.
`.devdata/` is gitignored and is the natural place for it.

Without `MERIDIAN_WORKER_MAX_TASKS` it runs until signalled, which is correct and not
what you want at a prompt. It crawls the real web — the seeded frontier is real
real government sites — so a run leaves real `queue` and `fetch_attempts` rows
behind in the dev database. Reset the statuses afterwards if the next thing you do
depends on the frontier still being `pending`.

To exercise the shutdown path, `timeout -s TERM 5 uv run python -m worker.main` works
— `uv run` forwards the signal — but pipe it to a file rather than to `grep`, or the
signal takes the pipeline with it and you lose the last lines.

**Running the read surface by hand.** Same environment, two more processes:

```bash
set -a; . ./.env.dev; set +a
uv run uvicorn api.main:app --reload --port 21114   # API, Admin and /mcp
cd web && npm run dev                               # UI on :21115, proxying to the API
```

The UI reaches the API through Vite's dev proxy, so there is no base URL anywhere in
`web/` and nothing to configure. If the API is not running, Explore says the corpus
counts are unavailable rather than rendering zeros — which is the distinction
`CorpusCounts` exists to preserve, not a bug.

**A stale uvicorn on :21114 is the trap here.** It serves old code and every new route
404s, which reads exactly like a route that was never registered. Kill it before
assuming the mount is wrong.

**Admin needs `MERIDIAN_ADMIN_ALLOW_ANONYMOUS=true` in `.env.dev`,** or every
`/api/admin/*` route answers 503. That is the intended behaviour on an exposed
instance without Cloudflare Access (`P6-13`), and it is a confusing five minutes
locally if nobody told you. The 503 body names both fixes.

**The optional extras.** `uv sync --extra ner` installs spaCy for `P5-02`'s
`EntityRuler`; without it the pattern tests still run — the compiler lives in
`meridian_core` and needs nothing — and the tests that drive the real matcher
skip. `uv sync --extra embed` is the 2.3GB model. Neither is in the worker image
by default.

**`uv run` re-locks; `uv run --no-sync` does not.** Worth knowing while iterating
on a `pyproject.toml`, and worth *not* relying on: the lock must be committed with
the version bump either way (§3).

---

### The compose networks are a boundary, not organisation

`internal` is `internal: true`, so Docker installs no default route and nothing
on it reaches the open web. `egress` is an ordinary bridge. `worker` is the only
service on both, because it is the only one that fetches hostile content and
writes it to the database.

`crawl4ai` is on `egress` alone and that is the point of the whole split: it
drives a real browser against pages this crawler found by following links, so it
is the most likely thing in the stack to be compromised, and it has no route to
Postgres by construction rather than by policy.

It also receives no `env_file`. That is why `x-common` carries neither
`env_file` nor `networks` — a shared default for either is precisely how the
browser sandbox came to hold every database password in `.env`, which it did
until `v0.24.0`. `tests/unit/test_compose_topology.py` fails if any of this is
undone.

Note `internal: true` is not `P1-25`. It stops a container reaching the
*internet*; it does not stop `worker`, which must have a default route, from
reaching the LAN.

### A missing browser is silent, so the health line has to say so

`Crawl4aiClient.from_env()` returns None when `CRAWL4AI_URL` is unset and the
fetcher degrades to static — correct, and invisible. The health line carries
`browser: configured | unreachable | absent`, and `unreachable` logs at WARNING,
because a worker that lost its browser a week ago otherwise looks exactly like
one that never had it and simply extracts worse.

`/health` is unauthenticated on 0.9.2: a wrong token still returns 200, while
`/schema` and `/crawl` refuse. The probe sends the token anyway.

## 3. Traps that have already cost time

### Novelty-first ordering and a high-water mark cannot both be right

§11.9 says that when a day's chunks exceed the token budget, the run should
"process highest-novelty first". §6.3 says the run tracks the last chunk id it
consumed and pulls everything after it. Implement both and the second one eats
the first: reason over chunk 900 because it scored well, mark the run at 900,
and chunks 400–899 are now behind the mark and will never be looked at. Nothing
reports it — the corpus is complete, only the reasoning over it has holes.

A watermark describes a *prefix*, not a set. So the batch is ordered by
`chunk_id` and novelty is spent as a *filter* instead: known duplicates
(`duplicate_of IS NOT NULL`) and superseded chunks (`P1-32`) are left out, which
is the same saving without the hole. Ordering by novelty needs a per-chunk
"reasoned over" flag, which is a different design and a bigger table.

### An absent signal counted as zero splits every entity it touches

`resolution.score` weights context overlap heaviest — §5.5 is explicit, and the
reason is "Cambridge" the city against "Cambridge" the university. That is the
right weighting for comparing two *stored* entities, each with a neighbourhood
built from many mentions.

It is the wrong weighting for a mention read out of one batch, and the failure
is not subtle. A mention carries the chunks of the passage it came from; an
entity created an hour ago carries the chunks it was created from. Two
identical names from two different passages share no chunk, so context scores
0.0, and with string 1.0 the combined score is `(1.0×0.3 + 0.0×0.4) / 0.7 =
0.43` — below `SEPARATE_BELOW`. **An exact name match founds a new node.** Every
passage mentioning a thing creates another copy of it, which is precisely the
fragmentation §5.5 opens with, arrived at through the mechanism meant to
prevent it.

The module already states the rule that fixes it: absent signals are dropped
and the weights renormalised, never counted as zero. A mention seen once has no
neighbourhood — its context is absent, not contradictory — so `resolve_mention`
scores with an empty context and records the chunks on the row it writes. The
test that pins this is `test_the_same_name_in_two_passages_is_one_node`, and it
looks like a test of the obvious, which is why it is worth keeping.

### A dry run that called the model would understate the bill

`--dry-run` is a transaction that is rolled back, and that is a strong
guarantee for writes. It is exactly the wrong one for spending: the HTTP
request to the provider is not in the transaction, but `reserve_tokens` and
`settle_tokens` are. A dry run that called a model would spend real money and
roll back the record of having spent it — and §11.9's whole point is that the
first signal of the compounding loop would otherwise be the bill.

So the two model stages check `journal.dry_run` and stop before the call,
saying what they would have sent. That is the one place in this file where a
stage is allowed to know which mode it is in, and the reason is that the
transaction cannot cover what happens outside the database.

### Alembic does not see CHECK constraints on existing tables

This is `P0-21`, and it was hit again in `P1-21`. Autogenerate will happily detect that
a `constrained()` column needs to be wider and leave its CHECK constraint untouched —
producing a column that accepts the new values and a constraint that rejects every one
of them. Nothing fails until the first row is inserted in production.

Any migration that changes a `constrained(...)` value set must hand-write
`op.drop_constraint(...)` / `op.create_check_constraint(...)`. Use the **bare** name
(`"fetch_outcome"`, not `"ck_fetch_attempts_fetch_outcome"`) — the metadata naming
convention expands it on both create and drop. See
`migrations/versions/*_fetch_outcomes_for_the_content_.py` for the shape.

Test it by inserting through **raw SQL**, not the ORM. SQLAlchemy's
`validate_strings=True` rejects bad values in Python, so an ORM-based rejection test
passes whether or not the database constraint exists at all — which is exactly how
Phase 0 shipped unchecked VARCHAR columns while its tests were green.

### The raw store can span more than one root, and nothing records which

`sources.raw_file_path` is relative. To what is not written down anywhere, so a
corpus is only interpretable if you already know the `MERIDIAN_RAW_ROOT` each
row was written under.

On this machine three sources dangle against `.devdata/raw` and their files are
in `.devdata/containerraw`, put there by `P1-30`'s containerised verification
writing to its own bind mount. Nothing is lost and nothing is broken; the
corpus simply has two roots and no way to say so.

Consequences, before assuming a missing file is a missing file:

- `python -m worker.sweep`'s dangling arm lists every source whose file is under
  a *different* root, so on a multi-root corpus it is noisy rather than wrong.
- `make snapshot-corpus` tars one root. A snapshot taken here today omits those
  three files silently — `restore_corpus.sh` samples `raw_file_path` after
  restoring and is the only thing that would say so.

`P1-45`. Check the root before concluding anything.

### The browser and static extraction paths were not equivalent

Until `v0.31.0` the browser path used Crawl4AI's `fit_markdown` directly while
the static path ran trafilatura at `favor_precision`. Those are very different
filters, so the same page could keep or lose its navigation depending on whether
the fetcher escalated it to a browser — and that decision is made on how much
visible text the *static* fetch found, which has nothing to do with how much
chrome the page carries.

The premise in the docstring was that re-extracting locally would throw away a
filter that saw a rendered DOM. It is worth knowing why that was wrong, because
the same reasoning is tempting anywhere a sidecar returns processed output:
**the rendered HTML comes back in the same response** (`fetch.py` stores it as
the body), so the local extractor was never working from less than the filter
was. Check what the sidecar already handed you before deciding it knows
something you cannot.

Two consequences worth holding on to:

- Boilerplate does not just add noise. It becomes entities and entities become
  edges, and it **inflates the novelty gate's duplicate count** — every page on
  a site repeats the same chrome, so real pages get marked as duplicates of each
  other's furniture.
- Precision filtering drops DOIs written in stripped regions. Links survive
  (they come from the DOM, not the text) and a page's own identifier survives
  (meta tags), but a bare DOI in a sidebar does not.

### `extra={"module": ...}` raises at runtime — and `as_dict()` hides it

`logging` refuses to let an `extra` key shadow a `LogRecord` attribute, and it
raises rather than dropping the key. `module` is one; so are `name`, `args`,
`filename`, `levelname`, `process`, `message`, `lineno` and `funcName`.

It fails **only on the line that logs it**, so a scheduler that logged
`{"job": ..., "module": ...}` started fine, claimed a job fine, and died the
moment it tried to say which module it was about to run. The traceback points at
`logging/__init__.py` and names the key, which is the one mercy.

Prefix them — `job_module`, not `module`.

**`tests/unit/test_logging.py` sweeps the source for this**, and it has now
caught the same failure three times — which is also how the third one got
through. The sweep reads literal `extra={...}` dicts out of the AST, and the
third arrival was `extra=stats.as_dict()`: the keys are a dataclass's field
names, a file away, and one of them was `created` — a `LogRecord`'s own
timestamp. The nightly acronym harvest settled `failed` in 562ms every night,
in the one line whose job was to report what the pass had done, and the suite
was green throughout (`B-21`, `v0.103.1`).

The sweep now also walks every dataclass with an `as_dict`, because that is the
convention here for "this is going into an `extra`". If a third route into
`extra` appears — a dict built by a helper, say — assume this failure will
arrive through it, and extend the test before the route is used.

### `make snapshot-corpus` snapshots whichever Postgres it finds first

`scripts/_compose.sh` picks the dev stack whenever `docker-compose.dev.yml`'s
Postgres is running, and says why: somebody with both files present is usually
working against dev, and reaching production by accident is the expensive way
round to be wrong. That is right, and it is also a trap the moment a *third*
situation exists — the local stack (`docker-compose.local.yml`) running a real
crawl while dev Postgres is up for the test suite. The snapshot then dumps the
test database and tars `.devdata/raw`, and both succeed.

Name the stack and the data root when the corpus you mean is the local one:

```bash
MERIDIAN_COMPOSE="docker compose -f docker-compose.local.yml" DATA_ROOT=.localdata \
  make snapshot-corpus
```

The manifest records row counts, so the way this is caught after the fact is a
snapshot whose numbers are far smaller than the run that was supposed to be in
it.

### The embed pass and the crawl compete, and the loser is a timeout

Observed in a real run, not in a test. The backfill batches 256 chunks per
request and the client waits 30 seconds. Idle, the sidecar answers 32 texts in
1.2 seconds, so a 256-chunk batch is about ten seconds and the whole thing is
comfortable. With the crawl running — browser pool, extraction, and Postgres
all on the same box — it is not, and the batch overruns the timeout.

What happens then is the part worth knowing, because none of it says
"contended":

1. The client reports the sidecar **unusable**, with an empty reason, because
   a timeout carries no message.
2. `P2-19`'s fallback loads bge-m3 **inside the calling container**, which is
   correct behaviour for an absent sidecar and the wrong diagnosis for a busy
   one — and it puts 2.3GB in a process that had none.
3. The scheduled job eventually hits the scheduler's own timeout and settles
   `timeout`, leaving `next_run_at` an hour later and the backlog untouched.

So a backlog bigger than one job window **never drains**: every tick dies at
the same place, and the only trace is `last_status` in `scheduled_jobs`. The
symptom at the far end is a corpus whose search is quietly lexical-only, which
is the same thing an unconfigured embedder looks like (`B-22`).

**Both halves are now fixed, and the second one is the more interesting.**
`B-25` made the pass its own service in `run_forever` mode, so there is no
window to be killed at and no hour to wait for.

`B-27` explains the rest. The sidecar was not slow — **it was never used**.
The pass batches 256 chunks into one request against a client that waited 30
seconds, and 256 real chunks are about two minutes of CPU, so every batch
timed out at exactly thirty seconds and `P2-19`'s fallback loaded the model in
the calling process. The vectors were correct and the pass finished, so the
only symptom was a second copy of 2.3 GB of weights on a machine chosen for
being small — the precise thing the sidecar exists to prevent. Anything
measured about "the sidecar's throughput" before `v0.105.1` was measuring the
fallback.

What remains is genuine throughput: roughly 2 chunks/second on this hardware
against a crawl producing about the same, which is matched with nothing to
spare. If a backlog grows anyway the brake is `MERIDIAN_WORKER_CONCURRENCY` —
fewer crawl lanes, fewer chunks per second to embed.

Draining it by hand, with the crawl stopped:

```bash
docker compose -f docker-compose.local.yml exec -T \
  -e MERIDIAN_EMBEDDER_TIMEOUT_S=300 -e MERIDIAN_EMBED_CHUNK_BATCH=128 \
  scheduler python -m worker.embed --once
```

### A fresh install could never start a synthesis run

`P4-13` refuses a run without a budget row, §16 requires caps "before first
autonomous run", and **nothing ever created one**. Every deployment deferred
its first run with "no budget is configured" and would have done so for ever.
Seeded now from `config/budget.yaml`, with a migration for databases that
already exist (`B-32`).

The shape is worth remembering rather than the bug: a rule enforced against a
row nothing creates is a rule that only ever says no. When you add a guard that
reads configuration, check that something writes it.

### The attention vector steered nothing for three phases

§10's first line is that attention is a weight vector over topics and seeds are
drawn proportionally. Nothing in the acquisition path read it: `steering.py`
was the only module in the tree that touched those weights, and the crawl
claimed by priority and age.

It compounds rather than merely being absent, which is why nobody noticed. A
discovered link inherits its parent's topic, so whatever the crawl is already
doing produces more of itself — one early citation trail decided the shape of
everything after it. The first real corpus finished 93% on the topic weighted
*lowest* of three (`B-26`).

**The fallback was the second half of the bug.** Claiming within a drawn topic
and falling through to an unfiltered claim when that topic was empty hands the
share of every empty topic to whichever topic has most queued — the
concentration this exists to correct, by the back door. Three of six active
topics had no rows at all, so 45% of the weight was being donated. An empty
topic is now dropped from the pool and another drawn.

### A shape gate will trip test fixtures that used that shape incidentally

`B-23` dropped `/about` as site furniture, and three existing tests broke —
none about furniture. They had used `/about` as a second sample link because it
is the obvious thing to type.

Worth expecting rather than being surprised by: the next shape gate will do the
same. A test whose example URL is incidental should say so, and when one
breaks, re-point it rather than weakening the gate.

### A fixture that creates shared configuration has to remove it

Twice in one day, both times found by an unrelated test failing much later.
`test_synthesis_stages` created a budget row and only restored *pre-existing*
values, leaving a half-configured budget in the dev database; `test_topic_draw`
paused every other topic to isolate its draw and restored them in a teardown
that did not run, leaving every topic paused.

Two rules came out of it. Record whether the fixture *created* the row, because
"restore what was there" is not the same as "remove what I made". And prefer
patching the loader to mutating shared rows — `test_topic_draw` now monkeypatches
`steering_topics` and touches nothing global, which cannot leak however it exits.

### `sess.commit = sess.flush` in a test double disarms the fixture's cleanup

The worker harness downgrades `commit` to `flush` so the worker's settle step
stays inside the test's transaction. The fixture that later calls
`await sess.commit()` to *delete its rows* is then calling `flush`, so the
deletes run, commit nothing, and the rows outlive the test — twelve topic rows
and 360 queue rows, in this case, discovered when an unrelated test failed on
floors that summed wrong.

Capture the real `commit` before yielding and restore it in teardown.

### Two ways a benchmark lies on a small corpus

Both were live in `scripts/benchmark_search.py` before its own output exposed
them, and both have the same shape: a number that is produced by arithmetic
rather than by the thing being measured.

- **ANN recall is 1.0 when the index is not used.** Below a few thousand
  vectors the planner prefers a sequential scan, which is exact by definition,
  so "approximate" and "exact" are the same query and recall is perfect. It
  looks like a flawless index. Always check `EXPLAIN` for the index name before
  believing a recall figure.
- **Arm agreement is 100% when the corpus is smaller than the candidate pool.**
  The vector arm takes 100 neighbours; with 26 searchable chunks it returns all
  of them, so every lexical hit is necessarily also a vector hit. That reads as
  "fusion is buying nothing", which is a strong conclusion drawn from a corpus
  that cannot support one.

### An HNSW scan returns at most `ef_search` rows, whatever the LIMIT says

`hnsw.ef_search` reads like a quality knob and behaves like a ceiling: the
index scan yields at most that many tuples, default 40. So the vector arm asked
for 100 candidates and got 33 on a live corpus, while the lexical arm returned
its hundred. Nothing reported it. `SearchResult.degraded` says an arm is
*absent*; there was no signal for one that answered short, which is the harder
failure to see because the results look fine — there are simply fewer of them
than fusion was designed around.

Two things follow, and the second is worse than the first:

- Fusion can only reorder what the arms hand it, so a third-depth arm is
  systematically under-weighted against a full one.
- A recall benchmark at k=100 is capped at 40% by arithmetic. That is a
  **fourth** way a benchmark lies here, and unlike the other three it does not
  need a small corpus — it gets worse as the corpus grows.

Fixed in `_vector` by setting the depth from the pool being asked for. Two
details worth keeping: `SET` takes no bind parameters, so it goes through
`set_config(..., is_local => true)`, which is also what keeps one caller's
depth off the next caller's pooled connection; and the factor is 2 because the
index cannot see `_conditions()` — a filtered query spends candidates on rows
the filter then discards.

**Testing it is where the time went.** The obvious assertion — ask for 100,
get 100 — passes against the bug on any fixture small enough to build quickly,
because a few hundred vectors is below the size at which the planner uses the
index at all, and `enable_seqscan = off` does not save it either: a graph that
small is traversed almost entirely however the candidate list is bounded. The
test therefore pins the mechanism (the arm sets `ef_search`, derived from
`candidates`) and cites the live measurement for the behaviour. If you are
tempted to strengthen it, seed thousands of vectors first and check `EXPLAIN`
names the index before believing whatever it tells you.

### pgvector values need pgvector's type on the way in *and* out

Two separate traps, an hour apart:

- Selecting `embedding` through a raw `text()` query returns its **text
  representation** — asyncpg has no reason to know the type — and `list()` of
  that string is a list of single characters. Select through the mapped column.
- Binding a vector as a plain string into `CAST(:v AS vector)` fails the same
  way from the other direction. Use `bindparam(..., type_=Vector(DIM))`.

Neither fails where the mistake is. Both surface as
`could not convert string to float: '['` at the next bind, several frames away.

### `websearch_to_tsquery` needs a `regconfig`, not a string

Binding the configuration name as a parameter produces
`websearch_to_tsquery(varchar, varchar)`, which does not exist — there is no
implicit cast from varchar to regconfig. The error is "function does not exist",
which reads as a missing extension rather than a type problem. `cast(TS_CONFIG,
REGCONFIG)` is the fix.

### `alembic check` cannot see a generated column's expression

`P2-05` added `chunks.search_vector` as `GENERATED ALWAYS AS
(to_tsvector('english', text)) STORED`. Autogenerate warns

```
UserWarning: Computed default on chunks.search_vector cannot be modified
```

and moves on. So model-versus-database drift — the one thing `alembic check`
normally guards, and the reason `make migrate` is trusted — is exactly what it
does **not** guard for this column. A model changed to a different text-search
configuration with no migration behind it passes `alembic check` cleanly and
leaves the corpus indexed under the old one, with no error at any point.

`tests/integration/test_search_index.py::test_generation_expression_matches_the_model`
is the replacement: it reads `information_schema.columns.generation_expression`
and compares it to the model's `Computed.sqltext`, normalising what the parser
adds (`'english'` comes back as `'english'::regconfig`).

Two other things about that column worth not rediscovering:

- **The regconfig must be a literal.** `to_tsvector(text)` resolves through
  `default_text_search_config`, which is a session GUC, so the one-argument form
  is not IMMUTABLE and Postgres refuses it in a generated column outright.
- **`attgenerated` comes back as bytes.** It is Postgres's internal `"char"`
  type, so `attgenerated == "s"` is False and `attgenerated == b"s"` is True.
  Cast it in SQL rather than comparing in Python.

### `make build-push` needs a buildx builder that is not the default one

Four Makefile targets once pointed at scripts nobody had written, and
`snapshot-corpus` was the one that mattered — `P1-16`'s deliverable is literally
"48h unattended acceptance run → `make snapshot-corpus`", so the run's output was
a target that failed at the shell. `P1-36` wrote that one, plus `restore-corpus`
and `backup`.

`make build-push` landed in `P1-37` and has one prerequisite that is not
obvious. A multi-platform build needs a buildx builder using the
`docker-container` driver; the **default `docker` driver cannot produce a
manifest list at all**, and the error it gives when asked for two platforms —
"docker exporter does not currently support exporting manifest lists" — names
neither the cause nor the fix. Once:

```bash
docker buildx create --name meridian --driver docker-container --use
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

The second line registers the QEMU handler that lets an x86 box emit arm64
layers. The script checks the driver and prints both commands rather than
letting Docker's message stand.

Two behaviours of the script that look like obstruction and are not: it
**refuses to push from a dirty working tree**, and it never tags `latest`.
Both protect the same thing — scaffold §5 pins the image SHA in compose so a
bad build does not roll out on the next restart and rollback is a one-line
edit, and a tag naming a commit whose code is not what was built turns rollback
into a guess that is only found to be wrong while rolling back. Use
`--dry-run` to see the commands without either check stopping you.

`orchestrator` and `web` have no Dockerfile yet, so the script skips them and
says so. `docker compose build` on the server remains the way round all of this
for a first deploy, and [deployment.md](deployment.md) §6 has both paths.

### A value used in code but absent from the enum fails at the insert, not at import

`P1-28` shipped a sitemap handler that passed `seed_source="sitemap"` to
`enqueue()`, and that value was in neither the model's `constrained()` set nor
the database's CHECK. Every sitemap it fetched parsed cleanly, and then raised
`LookupError` on the insert — caught by the lane's outer handler, filed as
"task failed unexpectedly", and queueing nothing. The fetch succeeded, the parse
succeeded, `fetch_attempts` recorded a 200, and the log line said the sitemap
had been read. **The feature had never worked, and nothing said so.**

This is the `P0-21` trap in its worst form. `P0-21` is "the model was widened
and the migration was not"; drift tests catch that, because there are two
sources of truth to compare. Here there was only one place the value existed —
the code that used it — and nothing compares a string literal against an enum.

What actually catches it is an integration test that drives the feature to a
**committed row**. `tests/unit/test_sitemaps.py` covers the parser exhaustively
and passed throughout, because the parser was never the problem. So: for any
handler that ends in a write, the test that matters is the one that reads the
row back, and it belongs in `test_worker_run.py` rather than beside the unit
tests for the parsing.

### A Cloudflare *managed* challenge is not beatable, and it is worth knowing why

One of the cold-start seed domains returns 403 on every URL including
`/robots.txt` and `/`. It is behind Cloudflare, and the response carries
`cf-mitigated: challenge`.

This was tested properly rather than assumed, because the obvious hypothesis
(wait for the JS challenge to auto-solve) is *usually right*. It is not here:
`UndetectedAdapter` + `enable_stealth` + `magic` + `simulate_user` +
`override_navigator`, run natively inside the crawl4ai container with no API
restrictions, with waits of 28s and 41s across `networkidle` and `load`, still
returns 403 with `just a moment`, `cf-chl` and `turnstile` in the body.

The distinguishing signal is the status code over time. An auto-solving JS
challenge serves 503 and then 200 within about five seconds. An interactive
Turnstile serves 403 and stays there. Only the first is worth waiting for.

Note also that the Docker REST API forbids `proxy_config`, `magic`,
`simulate_user` and `override_navigator` from untrusted bodies (0.9.x
hardening), but that is *not* what blocks this — the same flags set natively
fail identically. Do not spend a day building a custom image to route around
the API restriction expecting a different answer.

### `urllib.robotparser` is version-dependent

Wildcards and longest-match arrived in Python 3.13. This project declares `>=3.12`, and
on 3.12 the stdlib gives the *opposite* answer for both `Disallow: /*.pdf$` and a
longer `Allow` overriding a shorter `Disallow`. That is why `worker/robots.py` exists.
Do not "simplify" it back to the stdlib.

### httpx hands you a whole network read, decoded

`response.aiter_bytes()` yields whatever one 64KB socket read inflates to — measured at
67MB from a gzip bomb, in a single object. Any size cap checked after that has already
lost. `worker/fetch.py` reads `aiter_raw()` and drives `zlib` in bounded steps for this
reason. If you touch `_read_capped`, keep `_Inflater.feed` a **generator**; materialising
its output into a list re-opens the hole.

### TEST-NET addresses are not usable as fake public IPs

`192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24` are classified non-global by
Python's `ipaddress`, so `netguard` correctly refuses them. Tests needing a stand-in for
a public host use real-looking globals (`93.184.216.34`, `104.18.32.7`). A test that
mysteriously returns `unsafe_target` is usually this.

### `httpx.MockTransport` responses are pre-read

`httpx.Response(content=...)` cannot be streamed — `aiter_raw()` raises `StreamConsumed`.
Use `streamed(...)` from `tests/http_doubles.py`, which builds a real `AsyncByteStream`
and lets a test control chunk boundaries, which is where the caps are enforced.

### `caplog` does not work in this suite

`addopts` carries `-p no:logging`, because pytest's logging plugin attaches handlers to
the root logger and interleaves plain-text records into the JSON stream the logging
tests parse. The fixture is gone with the plugin, and asking for it fails deep inside
pytest with a bare `KeyError` on a stash key rather than anything that names the cause.
To assert on a log record, attach a handler to the module's own logger — see
`tests/unit/test_fetch_signals.py`.

### A workspace package must declare its own dependencies, or only the container finds out

`meridian_core.policy` imports `yaml`, and `pyyaml` was declared on the *root*
project. Development never noticed, because the root install provides it to
everything. A container built with `uv sync --package meridian-worker` gets
`meridian_core` and nothing the root happens to also depend on, and died on
`ModuleNotFoundError` at import. Building the image did not catch it; *running* it
did. Worth remembering when `services/api` and `services/orchestrator` get images.

### MarkItDown's declared media type is a hint, not a gate

It runs magika over the bytes and then tries *every* converter that accepts any
guess, plus a final pass where converters see no type at all. So
`application/vnd.ms-excel` with `b"x"` converts as plain text, and a `.docx` labelled
`application/epub+zip` converts as a `.docx`. The gate has to be ours.

That matters because the default registry is not something to point at untrusted
bytes: it contains converters that fetch URLs (YouTube, Wikipedia, Bing), shell out
to `exiftool`, and a `ZipConverter` that extracts an archive to a temp directory and
re-dispatches its members by extension — and a `.docx` *is* a zip, so hostile input
reaches that path through sniffing however the `Content-Type` was set.
`extract/document.py` uses `enable_builtins=False` plus four explicitly registered
converters, and two tests fail if that is widened.

### An injection flag that fires on every article about injection protects nothing

`P1-23`'s hardest constraint is the false positive, not the false negative. A
research corpus about AI legitimately quotes "ignore all previous instructions" — in
an article about prompt injection, exactly the kind of source this system should be
reading — and a flag that fires on those is one someone learns to ignore. The rule
`worker/extract/injection.py` turns on is that **hiddenness** promotes a finding from
noise to signal: visible imperative phrasing is recorded and not escalated; the same
words in a `display:none` div are. Measured at 0 flagged across the 11 real
government pages crawled so far.

Two exceptions worth knowing before changing it: `tool_directive` ("add an edge",
"send the contents to") *is* suspicious when visible, and `markitdown`'s Python 3.14
problem below is unrelated but sits in the same module tree.

### `markitdown` needs an explicit `onnxruntime>=1.29` on Python 3.14

`markitdown` pins `magika~=0.6.1`, which requires `onnxruntime>=1.17.0` with no upper
bound — and uv resolves that to 1.20.1, which has no cp314 wheel. The install fails
with a message about Python ABI tags that does not name markitdown at all. Adding
`onnxruntime>=1.29` as a direct worker dependency fixes it; magika 1.x drops the
requirement entirely on ≥3.13, so this can come out when markitdown loosens its pin.

### A scanned PDF is not an empty one, and the difference is invisible

Run a scan through a text extractor and you get a page number and a running header —
which clears no threshold and reads exactly like a page with no content. Without
§6.6's chars-per-page check, a scanned planning report enters the corpus as
"extracted, nothing found" and nobody ever looks again. `extract_pdf` sets
`needs_ocr` instead, and drops the stray text layer rather than admitting it as
content.

### `%%Title` in PostScript never reaches the PDF

It is a DSC comment for the print spooler. Ghostscript writes the Info dictionary
from a `pdfmark` — `[ /Title (…) /DOCINFO pdfmark` — and `ps2pdf` has no
`-dDOCINFO=` flag despite it looking like it should. Relevant when building real
PDFs for tests, which is worth doing: a byte string starting with `%PDF-` exercises
the error path and nothing else.

### `registrable_domain` keeps subdomains, so a blocklist needs suffix matching

It strips a leading `www.` and nothing else — correctly, because
`datamall.lta.gov.sg` is a different source from `lta.gov.sg` and tiering depends on
telling them apart. But an exact-match blocklist then blocks `facebook.com` and waves
`m.facebook.com` straight through. `Prefilter.is_blocked` matches on suffix.

### A test that seeds a URL must not use `seed_source="frontier"`

It is the default on `enqueue`, so a test seeding a row and then asserting on what
frontier expansion queued cannot tell the two apart. The seed is a hand injection —
`seed_source="user"` — which is also what it actually is.

### An offset recovered by searching for the text cites the wrong copy

The obvious way to attach an offset to a chunk is `text.index(chunk)` after the fact.
It is wrong on any document that repeats a passage — a boilerplate disclaimer, a
repeated table header, a navigation string that survived extraction — because it
resolves to the *first* occurrence and the citation silently points somewhere else.
`chunk_text` threads offsets through every split instead, and works in `(start, end)`
spans rather than substrings so a chunk is always a verbatim slice.

### `zip(xs, xs[1:], strict=True)` always raises

The pairwise idiom is inherently unequal in length, so `strict=True` — which is
otherwise the right default and what ruff's `B905` asks for — turns it into a
guaranteed `ValueError`. Use `itertools.pairwise`. This survived the unit tests and
was caught by a stress input that reached the sentence-splitting fallback.

### `iterlinks()` is not a link list

lxml's `iterlinks()` yields every URL in a document — favicons, stylesheets, scripts,
`apple-touch-icon` at six sizes. On www.lta.gov.sg that was 145 "links", of which 77
were documents. A frontier fed from it spends its budget fetching PNGs.
`extract/html.py` takes `//a/@href | //area/@href` instead.

### trafilatura wants bytes, not a decoded string

It does its own encoding detection, which is the entire point of handing it the raw
response body: a page that declares UTF-8 and serves Latin-1 is common, and decoding
here first turns a recoverable document into replacement characters. `extract_html`
accepts both and passes bytes straight through.

### An empty table hides a migration that would fail on the server

`alembic upgrade head` passing locally proves nothing about a data migration
when the table is empty, and most of this schema's tables are. `B-10` changed
`figures.linked_entity_ids` from `json` to `bigint[]`; autogenerate emitted a
bare `ALTER COLUMN ... TYPE`, which ran clean against zero rows and would have
failed on the first deployment that had crawled a PDF with a figure in it —
Postgres has no implicit cast between those types.

**Insert rows on purpose before running a data migration**, covering the cases
the column actually holds — here NULL, `[]`, and two JSON spellings of the same
list — then migrate, check the values, downgrade, and check them again. It takes
two minutes and it is the only thing that distinguishes "the migration ran" from
"the migration is correct".

Two Postgres specifics that cost time in that one:

- **`USING` cannot contain a subquery** ("cannot use subquery in transform
  expression"), so anything needing `jsonb_array_elements_text` aggregated back
  into an array cannot be done as a type change. Add a column, `UPDATE` it, drop
  the old one, rename — an `UPDATE` may contain a subquery.
- **`ARRAY(SELECT ...)` over a NULL input yields `{}`, not NULL.** If the column
  distinguishes "nothing recorded" from "recorded as empty", the `UPDATE` needs
  `WHERE col IS NOT NULL` or the distinction is silently collapsed.

### A test whose clock is fixed and whose rows' clock is not expires on a date

`tests/integration/test_alerts.py` pins `NOW` to a literal instant, which is
right: a window test that used the wall clock would measure a different window
every run. But `record_alert` takes `created_at` from the *database* clock, so
a test that wrote a row and then asked about it "48 hours later" was comparing
a fixed `NOW` against a timestamp that kept moving. It passed for two days and
then went red with nothing changed, which is the worst possible shape for a
failure: the blame lands on whatever was committed that morning.

The rule, and `attempts()` in that file had followed it from the start: **a test
about a window must own both ends of it.** If the code under test derives one
end from a clock you did not set, set it yourself afterwards
(`row.created_at = when`) rather than assuming the two clocks stay close.

Worth checking the same way: anything calling `func.now()` or a `server_default`
timestamp and then asserting against a literal date. Grep for `dt.datetime(20`
in the suite — each one is a fixed end of some window, and the question is
always what the other end is.

### A dev database that has actually crawled breaks absolute-count assertions

`test_seed_loads_no_content` asserted the content tables were *empty* after seeding,
which was the same thing as "seeding loaded no content" right up until the worker
started writing `sources` rows — and then it began failing on any development database
that had crawled, which is every one worth having (§6 asks for real crawl snapshots
rather than fixtures). It now measures the delta across the seed run. Expect the same
trap in anything that counts `chunks` or `edges` once those stages exist.

### The seeded source-tier map is `{tier: [domains]}`, not `{domain: tier}`

`config/source_tiers.yaml` groups domains *under* a tier — `exact: {government:
[lta.gov.sg, ...]}` — and `resolve_tier` iterates it that way. A test or fixture that
writes `exact[domain] = tier` produces a mapping that parses, resolves to the default
for everything, and fails nothing.

The map lives in the global `fetch_policy` row's `settings` blob, seeded once, and
`resolve_policy` deliberately strips it out because it is not a fetch setting.
`resolve_source_tier()` in `policy.py` is what reads it back.

### SQLAlchemy does not track in-place changes to a JSONB column

`row.extra["k"] = v` and `row.settings["k"] = v` are writes that never reach Postgres.
They fail by doing nothing, which nothing catches — an assertion against the in-memory
object passes, because the in-memory object *did* change. Always reassign the whole
dict (`row.extra = {**row.extra, "k": v}`), and force a real read with
`await sess.refresh(row)` in the test that proves it landed. `sess.expire(row)` is not
the tool: touching an expired attribute in async SQLAlchemy raises `MissingGreenlet`
rather than reloading.

### An integration test that does not filter by topic claims the seeded frontier

This dev database holds the 13 real seeded queue rows, all `pending` and all priority
100. A test that enqueues its own task and then runs anything built on `claim_next`
without a `topics` filter gets one of *those* rows instead, fetches it, and leaves the
test's own task untouched — which reads as "the loop never ran" and is actually the
loop working correctly. Every test in `tests/integration/test_worker_run.py` takes a
`run_topic` fixture for this reason. `test_queueing.py` documents the same trap from
the other side.

### A test asserting on an INFO log passes or fails depending on test order

`configure_logging()` sets the root logger to INFO, and once *any* test has called it
the level sticks for the process. A test that attaches a handler to a module logger
and expects an INFO record therefore passes in a full run and fails on its own, with
nothing to suggest why. Set the level explicitly in the test and restore it — see
`test_housekeeping_prunes_and_logs_the_health_line`. WARNING assertions are unaffected,
which is why `test_fetch_signals.py` never hit this.

### `asyncio_mode = "auto"` makes an explicit `pytestmark` counterproductive

Adding `pytestmark = pytest.mark.asyncio` to a mixed sync/async test module marks the
sync tests too, and pytest warns once per sync test. Auto mode already handles the
async ones; leave the mark off.

### Crawl4AI 0.9.2 binds loopback *inside* its container

Without `CRAWL4AI_API_TOKEN` set, its entrypoint binds gunicorn to `127.0.0.1` inside
the container, so any published port reaches nothing and the failure looks like a
network problem. With the token set, every request needs
`Authorization: Bearer <token>`. Both compose files now set it; `.env.dev` uses `dev`.

### `uv.lock` goes stale on a version bump, and only the Docker build says so

The lock records all four workspace versions. Both Dockerfiles use
`uv sync --frozen`, which refuses when the lock disagrees with the pyprojects — so
a release that bumps `VERSION` without `uv lock` leaves the images unbuildable.
Nothing local notices, because `uv run` re-locks in place. It had been stale
across a dozen commits before anyone looked. `tests/unit/test_lockfile.py` now
catches it; **run `uv lock` in the same commit as the bump.**

### An `exists()` on a table the outer query also joins has no FROM

SQLAlchemy auto-correlates every table a subquery shares with its enclosing
statement. `exists().where(ChunkTopics.chunk_id == Chunk.chunk_id, …)` inside a
select that already *outer-joins* `chunk_topics` correlates both tables away, and
the statement fails at compile time with "returned no FROM clauses due to
auto-correlation". Used inside a select that does not join it, the same
predicate works — so it breaks only for the caller that reuses it. Alias the
table inside the predicate (`aliased(ChunkTopics)`), as
`meridian_core.passagetopics` does, and it works in both.

### Walking `app.routes` finds nothing in FastAPI 0.141

`include_router` stores an `_IncludedRouter`, which exposes neither `path` nor
`routes`. A test that walks `app.routes` looking for `/api/...` paths therefore
finds none and asserts an empty list against an empty list — passing, forever,
for the wrong reason. The handles are on `route.original_router.routes`. Any test
that enumerates routes should first assert it found a known one.

### Staging a whole file commits whatever else is in it

Twice now: `git add <path>` on a file that had accumulated two separate changes
put an unrelated addition into a fix commit whose message said nothing about it.
The rule in AGENTS.md is one task per commit, and the way it is broken is never
`git add -A` — it is a single path that happens to hold more than one thing.
Check `git show --stat` against the message before moving on.

### `TimestampMixin` indexes `created_at`, and a migration that forgets it fails

Every table using the mixin gets `ix_<table>_created_at`. A hand-written
`create_table` that adds the column and not the index passes its own tests and
fails `alembic check` — which is `test_migrations_match_the_models`, so it shows
up as one unrelated-looking integration failure. The index is not decoration; add
it in the migration.

### `constrained()` is a VARCHAR with a CHECK, not a native enum

So there is no Postgres enum type to drop in a `downgrade`. Writing
`sa.Enum(name=...).drop(...)` looks right, does nothing, and suggests to the next
reader that these are native enums.

### SQLAlchemy cannot negate a `text()` fragment

`~sql_text("EXISTS (...)")` raises an assertion deep inside `elements.py` rather
than producing `NOT EXISTS`. Write the negation into the SQL string. It fails
loudly and immediately, which is the good case — the bad version of this bug is
a clause that silently matches everything.

### Apache AGE cost four separate failures to install, none of them obvious

`P4-01` put AGE into the database. Every step failed first, and each failure
named something nobody had written, so they are all here.

**`shared_preload_libraries` in the image is only half the answer.** The
Dockerfile appends it to `postgresql.conf.sample` — and a *sample* is read only
by `initdb`. An existing data directory never sees it, silently, and every
Cypher call then fails with `unhandled cypher(cstring) function call`. The
compose files pass `-c shared_preload_libraries=age`, which works for both a
fresh database and one that predates AGE.

**Do not name the graph after the project.** `create_graph` creates a Postgres
*schema* of that name. Called `meridian`, it collides with the `meridian` role,
so `"$user"` in the default `search_path` resolves to it — the graph silently
becomes the default schema, `alembic check` proposes dropping AGE's internal
tables, and a later `CREATE TABLE` with no schema would put an application
table inside the graph. It is called `graph`.

**`create_graph` needs `ag_catalog` on the search path**, not merely
schema-qualifying. The label tables it creates reference `graphid_ops`
unqualified, so without it you get `operator class "graphid_ops" does not exist
for access method "btree"`. `SET LOCAL` inside the migration's transaction is
enough and leaves nothing behind.

**The writer must *own* the graph, not be granted on it.** AGE creates a table
per label on first use and attaches it with `ALTER TABLE ... INHERIT`, which
Postgres permits only to the parent's owner. `GRANT ALL` is not enough: the
first edge a model writes fails with `must be owner of table _ag_label_vertex`.
Ownership of the schema and the two base tables goes to `meridian_rw`.

And one for anything that sends Cypher through SQLAlchemy: **`:Label` collides
with `:param`.** `text()` parses `(:Finding)` as a bind parameter named
`Finding` and refuses to run without a value for it. Use `exec_driver_sql`.

### Absent is refused, and the codebase now says so in four places

A pattern worth naming because it recurs and because the wrong version of it is
always the friendlier one. `reserve_seeds` refused a `None` cap before anything
could supply one; `budget.py` (`P4-10`) makes the same choice for tokens and
the monthly ceiling, and nothing seeds a default budget; `trust.py` (`P4-14`)
admits `cleared` rather than excluding `quarantined`; `P4-12` treats a NULL
`seed_allowed` as undecided rather than permitted.

In every case the ergonomic default — unlimited, allowed, unexamined-is-fine —
is the shape in which forgetting to configure something becomes an incident,
and the loop is unattended so the first signal is a bill or a leak rather than
a log line.

The exception proves the rule and is worth knowing: **`within_rate_limit`
treats `None` as no limit**, deliberately. A rate limit throttles something
already authorised, so a token issued without one is a decision somebody made.
A budget with no cap is a decision nobody made. The two look identical in code
and are opposite in meaning.

### An unset cap and a wrong cap fail in different directions

Three places now refuse to widen access on a mistake, and the reasoning is the
same each time. `tiers_allowed` returns *nothing* for an unrecognised
`max_source_tier`, because reading "unknown ceiling" as "no ceiling" turns a
typo in a grant into a widening. `ResolvedGrant.tools` returns an empty set for
an unknown profile, so a profile added by a later migration fails closed.
`half_life_for` gives an unknown source tier the default decay rather than
exemption, because exemption is the valuable state and should be granted
deliberately.

The general form: when a lookup misses, ask which way the mistake fails, and
pick the direction that does not quietly grant more than somebody intended.

### Compose does not skip a service it cannot build

It fails the whole command. A `build:` pointing at a Dockerfile nobody has
written gives `lstat ...: no such file or directory` and nothing starts — so
`web` before `B-05`, and `orchestrator` until `B-18`, each meant
`docker compose up -d` could not bring up the production stack at all.

The fix for a service that genuinely does not exist yet is `profiles:`, not
silence: `up` ignores a profiled service, and naming the profile says out loud
that this is declared ahead of its image. `orchestrator` carries `phase4` for
exactly as long as `services/orchestrator/Dockerfile` is missing.

The neighbouring one, found at the same time: `ports:` on a service attached
only to `internal: true` networks publishes **nothing**, and is not an error.
`api` carried `127.0.0.1:21114:8000` with a comment calling it loopback-only,
which is worse than having no line at all — it tells the next person to curl
21114 on the server and read the silence as an API that is down. Verified
directly rather than assumed: a container on an internal-only network with a
published port refuses the connection.

Both are asserted now, over every compose file rather than the two services
that happened to be wrong.

### The first two commands in the deploy runbook could not work

`docs/setup.md` and `docs/deployment.md` both said

```bash
docker compose run --rm worker alembic upgrade head
docker compose run --rm worker python scripts/seed.py
```

and the worker image has neither. `alembic` is in the root project's `dev` group
and every application image syncs `--no-dev`; the worker Dockerfile copies
`packages/`, `services/` and `config/`, never `scripts/`. These are the first
commands an operator runs on a new server, and nobody had run them there.

Documentation rots quietly because nothing executes it, which is the general
lesson. `tests/unit/test_documented_commands.py` now resolves every
`docker compose run` in those two files to the Dockerfile that builds the
service and checks the invoked thing is in it. It cannot tell you the command
*succeeds* — only that it is not missing — and that is still most of the value.

### Docker creates a bind-mount source as root, and nothing here runs as root

The worst of the four found by first running the stack in containers. Every
application image creates and uses `meridian`, uid 1001, with `cap_drop: ALL`
and a read-only root filesystem — all correct. Docker, meeting a bind-mount
source that does not exist on the host, creates it as **root**. So the first
crawl fetched a handful of real pages, hundreds of kilobytes each, and could not `mkdir
/data/raw/<domain>/`, and settled every task as

```
"outcome": "success", "disposition": "retry", "stored": null, "chars": null
```

which is *true* — the fetch succeeded. The traceback is there at ERROR, one per
page, in among a stream that otherwise reads like a healthy crawl.

It would have done the same on the server. `docs/setup.md` and
`docs/deployment.md` chown `/srv/meridian/app` because that is the checkout, and
neither says anything about `raw`, `figures` or `models`. `B-16` added a `chown`
one-shot to both compose files, run by `make quickstart` and belonging in the
runbook before `up`.

Three things about the shape of it. The directories are chowned, not `-R`:
what is created beneath them inherits the owner, and a recursive chown over a
100 GB raw store is its own outage. The one-shot runs `network_mode: none`,
because omitting `networks:` silently puts a container on compose's default
bridge — which has egress, and this is the only container in the stack running
as root. And nothing in the test suite could have caught it: tests write to
`tmp_path` as whoever ran them, and a container's view of a bind mount does not
exist until there is a container.

### The embedding sidecar sits where it cannot fetch its own weights

`embedder` is on `internal`, which is `internal: true` — no gateway, no DNS. The
weights are *not* in the image: the worker image installs `sentence-transformers`
and never downloads `BAAI/bge-m3`, which is why `MERIDIAN_EMBED_CACHE` and the
`/models` mount exist at all. So on any stack nobody had hand-seeded, the sidecar
started, answered `/health` with `loaded: false`, and failed every embed request
after a 30-second timeout — for ever.

Nothing says so. `/health` is honest, and an unloaded model is the ordinary state
of a lazy sidecar nobody has used. The symptom is one line in a search response:
`degraded_reason` saying the embedding service "did not answer", which is easy to
read as a transient outage rather than a permanent impossibility.

`B-14` added `python -m worker.fetchmodel` — a one-shot on `egress` that writes
into the same volume and exits. **Run it before `up` on any new machine**; it is
in `make quickstart` and in the deploy runbook, and a populated cache makes it a
no-op. Keep the sidecar off `egress`: it runs corpus text through a model, and a
route out from there is a route out for anything that ever gets in.

Two smaller things fell out of the same hour. `worker.embed` *appears* to work
anyway, because `P2-19`'s fallback loads the model in-process when the sidecar
does not answer — it just downloads 2.3 GB into a layer that dies with the
container, every run, and says so only at INFO. And the compose comment asserting
the weights were in the image sat two lines above the mount that exists because
they are not; a comment stating a fact about the build is worth checking against
the build.

### A service that is running is not a service anything talks to

`docker-compose.local.yml` started the embedding sidecar and never gave the API
`MERIDIAN_EMBEDDER_URL`. Nothing failed: `RemoteEmbedder.from_env()` returns None
when the variable is unset, because "this deployment has no embedder" is a
supported state (`P2-07`) — so search ran the lexical arm alone and reported
exactly that, truthfully, a few hundred bytes of network away from a running
model.

The general shape: an absent-is-fine default plus a service nobody wired to means
a stack that is fully healthy and half functional. `tests/unit/test_compose_
topology.py` now asserts the pairing — if a compose file runs the sidecar, the
services that would use it must be able to find it — and `test_fetchmodel.py`
asserts the fetcher and the sidecar agree on the path, because two containers
agreeing by coincidence is the version of this that looks like success.

### A library that does not know it is offline retries until it gives up

`embedder` is on `internal` with no route out, and after `B-14` its weights are
in the cache. It still took **144.6s** to answer its first embed, because
`huggingface_hub` checks for the optional config files the cache does not hold:
a HEAD to huggingface.co per file, `Temporary failure in name resolution`, five
retries with backoff, then on to the next file — and finally a correct load
from cache. The logs are a wall of WARNING lines about a host being
unreachable, which is precisely what a *broken* sidecar looks like, and is how
`B-14`'s fix was nearly mistaken for not having worked.

`HF_HUB_OFFLINE=1` on that service takes it to **4.9s** with no warnings, on
the same cache, measured both ways. Set it wherever a container reads the model
cache and has no egress — and never on `modelfetch`, whose whole job is the
download. Both directions are asserted in `tests/unit/test_fetchmodel.py`.

The general shape is one this codebase keeps meeting: a component that is
*correct* about its own state and wrong about its surroundings will spend a
long time finding out, loudly, in a way that reads as a different fault.

### The scheduler is a supervisor, and for a long time nobody supervised it

`P5-06` was ticked, its code worked, and no compose file ran `python -m
worker.scheduler`. `seed.py` writes five `scheduled_jobs` rows — embed and
novelty hourly, digest, sweep and harvest daily — all `enabled`, all with
`next_run_at` in the past. Reading that table told you embedding ran every
hour. It had never run once, in either stack.

So a stack left alone fetched, extracted, chunked and stopped. `embedded_chunks`
stayed at zero while the backlog grew, which looks like an embedder problem and
is not one. `B-15` added the service.

**The wider point is the one to carry.** A task is done when it runs, and "its
code runs when invoked" is not the same claim as "something invokes it". The
scheduler had tests, a lease, SKIP LOCKED and a careful argument about not
passing module names to a shell — all of it correct, none of it reached by any
code path outside the suite.

**`scheduler` is the second service on both networks**, and that is a real
widening of `P1-22`'s boundary rather than an oversight. Of the five jobs it
spawns, only `worker.digest` needs `egress`; the other four want nothing
outside `internal`. They inherit the container's environment, so `worker.harvest`
now parses crawler-fetched text somewhere with a route to the internet. The
trade was taken because the alternative is a scheduled send that fails into
`last_error` and nowhere else, and because `worker` already makes exactly this
trade in exactly this image. `test_only_named_services_write_from_egress` holds
the allowlist, and the reasoning is written into it so the next person can
disagree with it rather than discover it.

**Omitting `healthcheck:` does not give a container none.** It inherits the
image's, and the worker image's probe imports `worker.main` and checks poppler
— which passes for as long as the package tree is intact. The scheduler shipped
that way for one commit, so a wedged one would have read `healthy` for ever
while also suppressing the restart that no probe at all would have left to
`restart: unless-stopped`. Caught by watching the container come up and report
`health: starting` a minute after the comment claiming it had none was written.

`B-19` closed it properly: the loop writes `P5-08`'s heartbeat itself, and
**where** it beats is the design. After each claim rather than before it,
because here the database round trip is the thing that hangs and a beat in
front of it would be refreshed by a scheduler that never gets an answer — the
opposite of `worker.main`, which beats first because a lane wedged inside a
fetch should stop beating within its own iteration. And continuously while a
job runs, because a backfill takes half an hour and a probe firing during
normal work would restart the scheduler in the middle of the work it was
reporting on. That second beat is the weaker claim — a job is in flight and has
not hit `--timeout-seconds` — and the timeout is what stops it covering for a
permanently hung child.

A long-running service that overrides its image's command must now declare a
healthcheck or disable one explicitly, so the next one cannot inherit a probe
for a process it does not run.

### A control surface is a service, and a service is a thing that can be down

`P5-07`'s inbound half is `worker/bot.py`, and it is a separate compose service
rather than a lane inside the worker. Three things about it are worth knowing
before the first command is typed.

**It drops whatever was sent while it was down.** Telegram holds undelivered
updates for 24 hours and replays them on the next poll, so a bot restarted after
a night off would work through yesterday's queue at breakfast — `/run` at
midnight starting a run in the morning, a `/boost` applied a second time. The
first poll therefore acknowledges the backlog without reading it. If a command
seems to have been ignored, this is why, and the fix is to send it again.

**It polls rather than being called.** No inbound port, no certificate, no
hostname — which is the whole reason it works on a machine with no ingress. The
cost is that it is one more long-lived process to notice the death of, so it
touches the same liveness file the worker does and carries the same healthcheck.

**An unknown chat gets silence, not a refusal.** A reply would confirm that the
bot exists and is listening. If your own messages are being ignored, the
unauthorised chat id is in the log — that is what it is logged for, because the
likeliest cause is a `TELEGRAM_CHAT_ID` that is wrong rather than an intruder.

### The tables are the source of truth; AGE is a projection

`P4-01` shipped the graph store and deliberately left one question open:
whether AGE holds nodes and edges, or mirrors the `entities` and `edges` tables
that already exist. **Decided 2026-09-20 — the tables hold them, AGE is
derived.** It is recorded here as well as in `TASKS.md` because a year from now
the question is "what did we agree to", and the answer needs to sit next to its
reason.

The reason is the MCP surface. An assistant asking "what do we know about X"
needs a passage and a URL it can follow, and those come from `chunks`,
`entities` and `edges` — not from a traversal. So the store that answers the
common question is the one that holds the truth, and AGE takes the questions
the tables are bad at: paths, neighbourhoods, contested subgraphs.

What that buys, and it is the whole argument: **a projection can be dropped and
rebuilt.** Under the other two readings — AGE authoritative, or both
authoritative — a disagreement between the stores has no cheap resolution, and
the place it would surface is the MCP surface, as a fact citing a chunk the
graph does not have.

The consequence for anyone writing a stage: **write to the tables.** Nothing
writes to AGE yet and nothing should until the projection pass exists. A stage
that wrote Cypher directly would be creating exactly the divergence this
decision exists to prevent.

### A merge folds a repeated claim, and the folded row lives in `merge_log`

`B-41`. When a merge re-points an edge onto a subject, relation and object the
target already holds, `merge` folds it into the held edge — citations, topic
labels and contradictions unite; the judgement moves only to a strictly higher
tier, as in `add_edge` — and **deletes the moved row from `edges`**, keeping it
whole in `merge_log.combined`. The same happens to an attribute value both
entities carry (the table allows one per entity, so the move used to fail
outright). Observations are moved, never folded: two equal readings are two
pieces of evidence, and nothing defines when they are one.

Three things worth knowing before touching it:

- **An edge id can disappear and come back.** `reverse` re-inserts a folded
  row under its original id. Anything that caches edge ids across a merge
  (a saved view, a contested pair) should expect a miss, not an error.
- **Reverse merges newest first when they share a survivor.** If a later merge
  folded away the edge an earlier one folded into, reversing the earlier one
  is refused (`reason="order"`) before it touches anything.
- **`merge_log.moved_columns` says which end of a row moved.** Before it, a
  reversal moved back every column naming the target, so an edge between the
  two entities came back as a self-loop on the source. Merges logged before
  `B-41` have it null and still reverse the old way.

Duplicates left by earlier merges are folded by `python -m worker.edgedupes`
(report by default, `--apply` writes). Each fold is appended to the `combined`
of the merge that caused it, so reversing that merge splits it; a duplicate no
merge explains is listed and left alone.

### One unfinished run is an invariant, so a stuck run blocks every later one

`P4-08` puts two unique partial indexes on `runs`: at most one row may be
`running`, and at most one `deferred`. That is deliberate — two orchestrators
on one corpus means double spend and two sets of writes racing the same
high-water mark — but it has an operational consequence worth knowing before it
is met at three in the morning.

**A run that is stuck occupies the only slot.** Nothing else can start until
either it finishes, or its `heartbeat_at` goes stale (30 minutes, `STALE_AFTER`)
and the next wake takes it over. That window is not a delay anybody chose to
wait through; it is long because a single stage can legitimately spend minutes
on a frontier model, and a shorter one would hand a live run to a second
process.

To unblock deliberately, end the run rather than deleting it:

```sql
UPDATE runs SET status = 'failed', error = 'ended by hand', completed_at = now()
 WHERE status IN ('running', 'deferred');
```

**`status='running'` does not mean anything is running.** It means a row was
claimed. `heartbeat_at` is the column that says whether anybody is there, and
it is NULL for a run that died before completing its first step — which reads
as stale, on purpose.

**Deferred is not failed.** A deferred run is waiting for the next cycle
(§13.4: an unreachable provider should cost synthesis, not the crawl), keeps
its stage, and is resumed rather than restarted. If synthesis seems to have
stopped, `status` tells you which of the two it is and `stage` tells you how
far it got.

### Fixing a config file does not fix a database that was already seeded

`scripts/seed.py` skips a row that already exists. That is deliberate and worth
keeping — re-seeding must not undo steering — but it means **editing
`config/*.yaml` changes new installs only**. Every database seeded before the
edit keeps the old value, and nothing that reads the YAML can tell.

`P4-07` hit this. The agent registry declared `tagging` for a task everything
else calls `tag_attributes`; `task_types` is `text[]`, so Postgres accepted it,
routing matched on equality and the agent was simply never chosen. The visible
symptom would have been the frontier model doing narrow work configured to go
to a cheap one — a bill, not an error.

The fix is two things, and only doing the first is the trap:

1. correct the YAML, so new installs are right;
2. **a migration**, so seeded databases are too.

`tests/integration/test_routing.py` now asserts the rule against the `agents`
table rather than against the file, which is the only version of that test that
can tell the two apart. The same shape applies to `config/seed_sources.yaml`,
`config/attributes.yaml` and the gazetteer.

### A container on an `internal: true` network cannot publish a port

Docker installs no gateway on it, so there is nothing for the host to forward to
— and the `ports:` line is not an error, it is inert. Production gets away with
it because `cloudflared` sits on `internal` and proxies inward, but the
`ports: ["127.0.0.1:21114:8000"]` on its `api` does nothing whatsoever. The local
stack needs a third network (`frontdoor`) carrying only `api` and `web`, for no
reason other than having a gateway to publish through.

### A key set on the global `*` policy row is set for every domain

`P1-27` nearly shipped dead because of this: the rule was "learn only where
nobody configured `render_js`", and the global row ships `render_js: auto` as its
default — so every domain counted as configured and the learning never applied.
Any per-domain rule of the form "only when this key is absent" has to be written
against the *merged* value, not against the presence of a key in some row.

---

### A retry sooner than a cached refusal is the same refusal three times

`B-33`. An unreadable robots.txt refuses its origin for `ERROR_TTL_S` (ten
minutes); the queue's retry backoff is seconds. Making the outcome retryable was
therefore not a fix on its own — all three attempts would have been served the
cached "no" and the URL failed anyway under a new label. The general rule: when
a failure's cause is cached, the retry has to outlive the cache
(`queueing.retry_floor_s`). Anything else that caches a negative verdict wants
the same check.

### `numpy.linalg.eigh` on a 1024 × 1024 float32 matrix takes seven seconds

On this machine, host and container alike, while the matrix product that built
it took 30 ms. Found by timing the corpus map on the real corpus; nothing in the
suite runs at that size. The map needs three components (two until `P6-29`),
so `corpusmap.project` uses seeded subspace iteration, pinned against the exact
decomposition by a test. Anything else reaching for a full decomposition of
embedding-sized matrices should measure first.

### The 3D corpus map: what is not obvious from the code

`P6-29`. `web/src/explore/map/`. Four things that each cost a round trip:

- **The canvas is a dark island.** Its wrapper carries `data-theme="dark"`, so
  every token inside it — series, surface, text — resolves to the dark value
  in both themes, like the graph canvas (§2 publishes canvas colours for dark
  only). The palette is read with `getComputedStyle` *on that element*, not on
  `:root`; read it from the root in light mode and the dots take light-theme
  series values meant for white paper.
- **A zero `gl_PointSize` is not invisible everywhere.** SwiftShader (headless
  Chromium) clamps it to one pixel, so a hidden topic stayed on screen as
  dust. Hidden points are moved outside the clip volume instead.
- **Picking is in screen pixels, not by `Raycaster`.** A ray threshold is in
  scene units and changes meaning with zoom. `geometry.pick` projects every
  visible point through the camera matrix — a small fraction of a frame at the
  8000-point ceiling, and testable in jsdom, which has no WebGL.
- **three.js is loaded lazily** (`React.lazy`), about 560 kB minified in its
  own chunk; Vite's size warning on that chunk is expected. Headless Chromium
  renders it through SwiftShader at 60 fps with 8000 points, so the
  screenshot tool sees the real 3D view.

### Tailwind drops a colour utility it cannot resolve, silently

`B-34`. `border-border` compiled, rendered, and passed every test; the border
fell back to `currentColor`. `tests/tokens.test.ts` now fails any colour utility
whose role is not in `app.css`'s `@theme`.

### Two sessions running `make test` against the dev database collide

Seen twice while a delegated agent ran the suite in parallel: one error in a
`runs` heartbeat test and one failure in `test_admin_agents.py`, both reading
shared tables the other run was writing. Both passed on rerun. Parallel agents
are fine for writing code; run the full suite from one of them at a time.

### `compose run --build` and `up -d --build <service>` can restart Postgres

Migrating the live stack with `docker compose run --rm --build tools alembic
upgrade head`, and later `up -d --build orchestrator`, each took Postgres down
under a running worker: "the database system is shutting down", then refused
connections for a few seconds. The worker survived it — lanes back off and
expired leases are reclaimed — but thirteen ERROR lines and a handful of
unrecorded failures are the cost. During a run that matters, stop the worker
first, or build images with `docker compose build <service>` and then
`up -d --no-deps <service>`.

### The relay agent, and how to drive it

`P4-18`. Enable `claude-code-session` in Admin → Agents, then run
`docker exec meridian-local-orchestrator-1 python -m worker.orchestrate --once`.
A cycle that defers leaves `<key>.prompt.json` in `.localdata/relay`; write
the answer with `docker exec -i meridian-local-orchestrator-1 sh -c 'cat >
/relay/<key>.answer.txt' < answer.json` and run another cycle — or two, since
the first only closes the deferred run and the second re-pulls the batch.
Each batch needs two answers, `extract` then `tag`. **While it is enabled with
nobody answering, the orchestrator service's own schedule defers every run**;
disable it when the session ends.

### A listing is measured from the extracted text, and a bare link list never gets there

`B-59` decides whether a page is a listing from `document.text`, where
trafilatura leaves links as markdown — not from the HTML. Two consequences.
A plain `<ul>` of links with nothing else extracts to almost nothing (trafilatura
reads it as navigation), so the page is metadata-only and never reaches the
rule; test fixtures for listings need structure (`<dl>`, an `<article>`, a
count beside each link) to survive extraction at all. And the fetch path
classifies the text *after* `clean_cut` has left furniture out, because that is
what `worker.dockind` reads back from stored chunks — classify the uncleaned
text and a footer of links can turn an article into an index at fetch time
and not in the backfill.

The raw store under `.localdata/raw` is written by the container user and is
not readable from the host account, so calibrating against the live corpus
means reading chunk text through `psql`, not re-extracting raw files.

### One weight change logs a row for every active topic

`steering.renormalise` records each weight it moves, and setting one topic's
weight moves all the others. So anything keyed on "was this topic touched
recently" in `steering_log` sees *every* active topic as touched after any
weight change. `P6-38`'s proposal pass relies on exactly that — no proposal
for a topic with a log row in the last 24 hours, whoever wrote it — and it is
deliberately conservative: after one weight change anywhere, nothing is
proposed for a day. If that proves too quiet, filter on `field` and the
reason's prefix, not on actor alone, because an auto-applied proposal's
renormalisation rows carry actor `proposal` on topics it never proposed for.

Also from `P6-38`: the window a proposal waits (`steering_proposal_window_hours`
in the global policy row) is not in Admin's fetch-policy `EDITABLE` set, so it
is changed with SQL for now. Scheduled as `steerproposals`, hourly, enabled.

## 4. What is verified live, and what is only tested

Tests are hermetic by design, so "the tests pass" and "it works against the real web"
are different claims. As of `v0.10.0` the following were confirmed against real servers,
not doubles:

| Behaviour | Evidence |
|---|---|
| Static fetch, pinned to the validated address | example.com, iana.org, lta.gov.sg, arxiv.org |
| SSRF refusal | `http://169.254.169.254/` → `unsafe_target` |
| Plaintext-final refusal | `http://neverssl.com/` → `unsafe_target` |
| Browser path end to end | Crawl4AI 0.9.2, markdown and links carried through |
| `render_js: auto` escalating | excalidraw.com, 32 → 474 visible chars |
| `render_js: auto` *not* escalating | react.dev, vitejs.dev (both pre-rendered) |
| `Crawl-delay` honoured | arxiv.org publishes 15s; observed 15s gaps |
| Conditional request | iana.org returned a real `304`, zero bytes |

And as of `v0.12.0`, with the loop driving instead of a script:

| Behaviour | Evidence |
|---|---|
| Unattended drain of the seeded frontier | 7 tasks claimed, fetched and settled; queue statuses and `fetch_attempts` rows written and committed |
| A 403 abandoned, not retried | a challenged domain → `http_error` 403 → `failed` at `attempts=1` |
| Health line (§12.5) | `{"message": "health", "queue_depth": {...}, "fetch_success_rate": 0.857, "by_outcome": {...}}` |
| Graceful `SIGTERM` | two fetches in flight both finished and settled; process exited 0 |
| Two lanes not stampeding one host | per-domain `waited_ms` of 1000–1500 across concurrent lanes |

And `v0.13.0`, where the *second* run is the evidence:

| Behaviour | Evidence |
|---|---|
| Raw store, primary sources | 4 `.gov.sg` pages written to `<domain>/<shard>/<sha256>.html`, 956KB |
| Retention split (§5.4) | landtransportguru.net → `informal` → `background` → `"stored": null`, checksum recorded |
| Source tier from the seeded map | `lta.gov.sg` → `government` with no per-test override |
| Conditional requests, finally live | second pass: 4 of 5 returned a real `304`, zero bytes |
| Checksum change detection | the fifth answered `200` with `"content_changed": false` |

And `v0.14.0`:

| Behaviour | Evidence |
|---|---|
| HTML extraction over the seeded frontier | 7 fetched, 6 extracted, 265–987 chars each, titles and dates on every one |
| Metadata-only is a real resting state | www.sae.org → 62 visible chars, a JS shell → `text_available=False`, still a source row |
| Real bibliographic metadata | arxiv.org/abs/2401.02777 → title, `2024-01-05`, abstract, `10.48550/arxiv.2401.02777` |
| A paper does not cite itself | the same arXiv page → `citations == ()` after self-identifier exclusion |

And `v0.15.0`, where the round-trip is the claim worth checking:

| Behaviour | Evidence |
|---|---|
| Chunking in the fetch pass | 7 fetched, 6 chunked, including a `background` source that keeps no raw file |
| Offsets locate their passage | every stored `page_or_offset` re-extracted from the raw file on disk and matched exactly |
| A re-crawl rewrites nothing | 5 real `304`s plus one byte-identical `200` → `chunks: 0` across the run |

And `v0.16.0`, the run where the crawl stopped being a fetcher:

| Behaviour | Evidence |
|---|---|
| Frontier expansion | 13 seeds → 334 pending in one 12-task run, claiming frontier-discovered pages within the same run |
| Tier priority, unwired since `P1-17` | `.edu.sg` queued at 60, `.gov.sg` at 50, blogs below — nobody curated a list |
| Blocklist and shape gates | 45 blocked-domain drops across 8 pages; no social link, shortener or asset URL in the queue |
| Already-seen dedup | one deep `lta.gov.sg` page: 62 links considered, 43 already seen, 12 queued |

And `v0.17.0`, against real government PDFs the crawl had queued for itself:

| Behaviour | Evidence |
|---|---|
| PDF native-text extraction | `lta.gov.sg/.../MTM.pdf` → 1,367 chars across 2 pages |
| Page-accurate chunks (§5.3) | `chunks.page_or_offset` = 1, 2 — the page a citation opens at |
| Title from the PDF's own Info dictionary | Land Transport ITM → `"20230301 Land Transport ITM e1"` |
| robots.txt still honoured on PDFs | two datamall user guides → `robots_denied`, abandoned |

And `v0.18.0`, where the number that matters is the one that stayed at zero:

| Behaviour | Evidence |
|---|---|
| Injection screen on real pages | 11 crawled government pages → 9 clean, 2 `hidden_text` noted, **0 flagged** |
| Hidden instructions caught | `display:none`, `hidden`, `aria-hidden`, white-on-white, offscreen, zero-size all detected in tests |
| An article *about* injection not caught | visible "ignore all previous instructions" → recorded, not suspicious |

And `v0.19.0`:

| Behaviour | Evidence |
|---|---|
| Office extraction | a real `.xlsx` built with xlsxwriter → text, chunks, character offsets |
| The converter allowlist holds | a plain zip and an HTML page mislabelled `.docx` → `markitdown-failed`, not converted |
| OOXML metadata is bomb-proof | a 200MB decompression bomb in `docProps/core.xml` → refused with an honest header *and* a forged one, 0MB RSS |

And `v0.20.0`, from inside the container rather than from a checkout:

| Behaviour | Evidence |
|---|---|
| The worker image runs the whole pipeline | `--read-only --cap-drop ALL`, unprivileged: 3 pages fetched, stored, extracted, chunked, 175 links queued |
| poppler is present and found | `HEALTHCHECK` and `pdf.available()` both true inside the image |
| The raw store works through a bind mount | files written to the host through the container's uid |

And `v0.25.0`, against the chunks a real crawl had already produced rather than
against constructed vectors:

| Behaviour | Evidence |
|---|---|
| The gate finds real duplicates | the boilerplate block a site repeats under every URL, caught at cosine 1.0 |
| It does not find false ones | every genuinely distinct page kept; nothing demoted to `junk` |
| `0.95` is nowhere near the noise floor | unrelated real bge-m3 chunks cluster around 0.71 and bottom out near 0.55 — the threshold has real headroom, which is *not* what a hash-based `FakeEmbedder` would have told you |
| The pass is cheap | the whole corpus judged in well under a second, with no index yet |

That last row is the one to re-check at scale: the nearest-neighbour scan is
sequential until `P2-04` adds the HNSW index, and "fast" here means "fast on a
corpus small enough that nothing is fast or slow".

And `v0.26.0`, against a real SearXNG rather than a mock transport:

| Behaviour | Evidence |
|---|---|
| A seed query becomes frontier | a query pending since `make seed` → 47 results → 44 queued at tier priority, task `done` |
| The prefilter earns its place here | 3 of 47 were blocked domains, dropped before a request was spent |
| §6.4's routine engine failure | one upstream engine unresponsive throughout; the query succeeded and nothing retried |
| Sitemaps enqueue at all | four loop-level tests, all of which fail against the pre-`v0.26.0` enum |

And `v0.27.0`, against real Unpaywall and OpenAlex:

| Behaviour | Evidence |
|---|---|
| A paywalled publisher paper resolves | an Elsevier transport paper → an institutional repository copy, `submittedVersion` |
| Open-access papers resolve to a PDF | two publishers → direct PDF links, `publishedVersion` |
| An arXiv DOI costs no request | resolved from the DOI itself, network handler asserted untouched |
| A genuinely closed paper is an *answer* | one publisher DOI → no copy anywhere → `done`, not retried |
| Semantic Scholar earns its place | 12 of 12 DOIs that OpenAlex could not resolve → an open-access PDF, asked one per second |
| Europe PMC does not, for this corpus | 0 hits across a transport-research sample; it is a biomedical index and will matter for health-adjacent work, not this |

### A rate-limited API looks exactly like an API with no answer

`v0.28.0`, and worth reading before adding any provider to any chain in this
codebase. Semantic Scholar's anonymous quota is strict. Resolving 75 real DOIs
back to back returned an open-access copy from it for **none** of them; the same
DOIs asked one per second returned a PDF for **every one** of the twelve
sampled. The first measurement read as "this provider adds nothing" and would
have justified deleting it.

The cause was a 429 folded in with connection errors — both "this provider could
not be asked", both skipped, chain continues. So the resolver reported *no
open-access copy exists*, the task settled `done`, and the paper was never looked
for again. A false negative indistinguishable from a true one, produced by the
system's own throughput.

**The penalty outlasts the burst, which is the part that will waste your
afternoon.** After the 75-DOI runs, the same provider returned nothing even at
one request per *three* seconds; twelve seconds apart it answered 200 with a PDF
every time. So a paced re-measurement taken straight after an unpaced one
reproduces the unpaced result and looks like confirmation. Wait it out, or use a
key.

Three rules came out of it:

- **A provider that refused because you asked too fast has not answered.** A
  resolution that found nothing while being throttled is incomplete and must
  retry rather than settle.
- **Any "provider X adds nothing" measurement taken at full speed is worthless.**
  Re-run it paced, from a cold start, before believing it — and note that the
  first paced re-run may still be inside the penalty window.
- **Per-process pacing is a floor, not a quota solution.** The queue's own
  exponential backoff is the right timescale for waiting out a quota; the
  interval only stops the worker throttling itself.

The same shape applies to SearXNG's engines — §6.4 already says engine failure is
routine — and to anything else this codebase queries in a loop.

**Never confirmed against a real server:** the decompression-ratio cap (tested against a
local socket serving a synthetic bomb) and the 5xx-robots refusal path.

### Nothing after phase 1 has met a real deployment

This is the largest gap in this document and it is worth stating plainly. Every
claim about phase 2, 3 and 6 rests on tests — a real Postgres, a real ASGI
transport, real HTTP doubles — and on nothing else, because **the stack has never
been deployed to the server**. Specifically unverified outside tests:

- the API and UI behind `cloudflared`, and Access JWT verification against real
  Cloudflare JWKS;
- an external assistant connecting over MCP from a phone, which is what §11 and
  `P3-05` exist for;
- the embedding sidecar under a real backfill — including `P2-19`'s fallback,
  which has only been exercised against fakes;
- the systemd units: `meridian.service`, and `meridian-backup.timer`'s
  `Persistent=true` catch-up after a machine was off;
- the worker healthcheck actually restarting a wedged container, as opposed to
  the liveness file being stale in a unit test;
- anything at corpus scale: HNSW recall (`P2-04`'s open half), search latency,
  the acronym harvest's precision over real documents, and whether any domain
  triggers `P1-27`'s render learning at all.

When the 48-hour run happens, that list is the checklist.

**Four of those are now shorter.** `v0.76.3`–`v0.78.1` brought the whole stack
up in containers for the first time — not on the server, but not natively
either — and the four things it broke on are the four in §3 above. What that run
established, against the real web and a real Postgres:

| Behaviour | Evidence |
|---|---|
| The stack comes up whole, from source | seven services healthy from `make quickstart` on a clean clone; migrations and the seed through the `tools` image, topic, policy and gazetteer rows written |
| The UI is served and reaches the API | nginx on `21116`, `/api` proxied, and a client-side route (`/sources/42`) surviving a reload rather than 404ing on the static root |
| The crawl stores what it fetches | after `B-16`, nearly every source in the corpus has a raw file, HTML and PDF alike. Before it, none did — each settled `"outcome": "success", "stored": null` |
| The embedding sidecar serves from a container | `loaded: true`, 1024 dimensions, 143s for a cold load off disk — which is what `start_period: 180s` is for |
| **Hybrid search, end to end, outside a test** | `arms: ["lexical", "vector"]`, `degraded: false`, both ranks populated. It had never run anywhere but in the suite |
| The timetable is read (`B-15`) | `scheduler` claimed `digest`, settled it `ok` in 747ms and rescheduled it, then claimed `embed` and began writing vectors against a backlog that had stood at one embedded chunk |

Still untouched by that: everything needing the server or Cloudflare.

### And `v0.103.0`–`v0.110.0`, the day a corpus existed

A 2h20m crawl from an empty database, then the stack kept running while the
fixes it provoked were written. **Five defects came out of watching it that the
suite could not have found**, which is the argument for doing this again before
`P1-16` rather than after.

| Behaviour | Evidence |
|---|---|
| A corpus, from empty | 1,282 sources · 19,949 chunks · 34 domains in 2h20m, frontier still widening at 58k pending — the failure `P1-16` watches for is a queue that drains, and it did not |
| Every chunk embedded | 19,949 of 19,949, after `B-25` made the pass a service and `B-27` let it reach the sidecar |
| **The sidecar served a backfill** | for the first time ever: 1.42 chunks/second, zero timeouts. Before `B-27` every batch timed out at exactly 30s and the in-process fallback did the work |
| A snapshot that is a corpus | 77MB of raw files beside a 109MB dump, with checksums. The first attempt produced 15KB, which is the "catalogue, not a corpus" the script's own header warns about (`B-31`) |
| Hybrid search measured, not asserted | HNSW used by the planner, recall@10 1.0000 at the `ef_search` `B-24` now sets, vector p50 26ms, hybrid p50 97ms |
| Fusion earns its second query | 13–16% of hits found by both arms once `B-29` made the two arms answer the same question. It had read 0% by construction |
| **The attention vector steers the crawl** | walkability was 2% of the corpus at 0.22 weight and is 62% of what is fetched now; AV was 93% at the *lowest* weight (`B-26`) |
| The frontier skips what it cannot read | 11–12 site-furniture links dropped per page, and a `doi.org` link routed to the resolver as an identifier rather than fetched as a redirect (`B-23`) |
| A run reaches the provider | `extract deferred: every agent for 'relation_extraction' refused: hosted-frontier reads its key from ANTHROPIC_API_KEY, which is unset` — the whole chain, in the right image, stopping exactly where it should |

**What is still unverified**: anything needing the server, Cloudflare, or a real
model call. `P2-19`'s fallback moved again without being designed for — `B-27`
found it had been carrying every backfill since the sidecar was built.

---

### 2026-09-23: a reboot, a boot-race defect, and two new screens

The crawl left running overnight did not run: the machine shut down two minutes
after it started, and `restart: unless-stopped` brought the stack back at boot.
The worker then came up before DNS, which is how `B-33` surfaced. After the fix,
the live crawl produced a real `robots_unreachable` — an origin timing out —
and the queue held the task roughly eleven minutes between attempts, re-reading
robots.txt each time, before giving up with an error that says "could not be
read" rather than "disallowed".

`/map` (`P6-26`) serves 3000 of ~25,000 passages in about a second; the two axes
carry about 9% of the variance, and the page says so. `/admin` → Crawl
(`P6-25`) reports `crawling` against the live worker. **Neither page has been
looked at in a browser by the session that built it** — no browser tooling was
available — so layout is verified only by tests and a production build.

### 2026-09-23 (afternoon): the relay, five resolver and pipeline defects, and a frontend rebuilt to its mocks

The relay agent (`P4-18`) answered 74 batches through chunk 3,359: 257 entities and
197 edges, every one through the validated write tools. Running it found `B-35`
(acronym expansion hid the node a name already had), `B-36` (every run restarted at
chunk 1), `B-37` (ties resolved by heap order), `B-38` (`merge_log` inside AGE's
catalog on any database migrated from empty) and `B-40` (the embedding half of
entity resolution had never run) — none visible to the suite, each now with a test
that fails on the old code. Four delegated agents rebuilt the shell and landing
(`P6-27`), Admin (`P6-28`), the graph workspace (`P6-01`–`P6-03`) and a 3D map
(`P6-29`), each screenshotted against its mock; all deployed. 307 site-furniture
pages were demoted to junk (`B-42`). The operator then set the direction recorded in
`TASKS.md`'s "Resume here".

## 5. What to build next

`TASKS.md` is authoritative; this is the reasoning behind the ordering, and the
short version is that **almost everything left is gated on one of three things**:
the 48-hour run, a Cloudflare account, or the graph.

### The gate is `P1-16`

Every phase-1 task that could be done without a real corpus is done. What remains
is running the thing for two days and looking at what comes out — which is also
the only way to answer `P2-09`, the human go/no-go on whether hybrid retrieval
over this corpus is better than reading the sources.

Bound a smoke run with `MERIDIAN_WORKER_MAX_TASKS`, not a timer: polite
per-domain delays mean a fixed wall-clock window yields wildly different volume
depending on which domains the frontier hands you, and a task count is
reproducible. `P1-16` itself is the timed one.

**The failure to watch for** is a queue that drains. A crawl that empties its
frontier and idles logs exactly what a healthy one logs. `pending` falling
monotonically to zero is the signal; a healthy run keeps finding more than it
drains. Two of the three non-link discovery channels were dead once before and
nothing reported it.

### Gated on a real model call, then on something that schedules one

**Phase 4 reasons now.** The store, entity resolution and reversible merges,
untrusted-data framing, capability routing, the run state machine and its
cycle, the four write tools, the model client and — since `P4-16` — the
prompts, the parse and the mention-to-node step all exist. A cycle over a
scripted answer writes entities, edges, attribute values and the mark, and a
malformed answer costs a batch rather than a run.

Two things are left before an edge exists that a model actually produced, and
neither is code: **a registry row with a working key**, and **a budget row**,
because an unconfigured token cap refuses rather than reading as unlimited. A
run with neither defers at `extract` and says which one is missing.

`P4-17` closed the rest. The orchestrator has its own image
(`services/orchestrator/Dockerfile`: same package, `--extra agent`, no
`--extra embed`) and runs §6.3's schedule as a service — at startup, daily, and
early when the backlog past the mark crosses `--early-at`.

**A timetable row was impossible, not merely worse**, and it is worth knowing
why before somebody simplifies it back: the scheduler spawns its jobs as
subprocesses of its own container, which is the worker image, which is built
without the SDK so that §2.1 is mechanical rather than remembered. A synthesis
row would have run in the one image that cannot call a model.
`tests/unit/test_orchestrator_image.py` reads the two Dockerfiles against each
other, because one `--extra agent` in the wrong file either ends the invariant
or defers every run with a provider error that reads as an outage.

**The parse was the load-bearing half of `P4-16`, not the prompt**, and it
stayed that way: §11.8's position is that a model's output is untrusted, so it
is validated (`P4-05`) rather than believed, and a parse that *raised* on a
malformed answer would let one bad response end a run that should have skipped
a batch.

**`P1-32` is decided and built**, so the thing that had to happen before the
first edge has happened: chunks are superseded rather than deleted, and a
citation keeps resolving after the page changes. Do not undo that by adding a
delete path.

**`P6-04` already reads the graph tables**, so `tests/integration/test_node_detail.py`
is a worked example of writing `entities`, `attribute_values` and `edges` rows
against their real constraints — including the one that catches people, the CHECK
on `observations` requiring a value.

**`P4-13` came before `P4-08`, as §16 asks**, and the ordering held: the caps
existed before anything could spend. `P4-15`'s client reserves the worst case
before a call and settles it after, so the compounding seed→crawl→cost loop —
the one first noticed as a bill — is bounded at both the token and the seed end.

### Gated on a Cloudflare account

`P3-05` and the rest of `P3-09`. The code side is done and tested: Access JWT
verification refuses rather than bypasses when JWKS is unreachable, and the MCP
surface advertises its protected-resource metadata only when authentication is
on. What is missing is a tunnel, an Access application, and an AUD tag.

### Buildable today

1. **`P4-16`** — the prompt and parse for `extract` and `tag`. The one task that
   changes what the system does, and the last of phase 4.
2. **`P6-23`** — admin: agent registry and run history. Its gate was that both
   tables stay empty until phase 4 runs something; `runs` now holds real rows
   with stages, statuses, heartbeats and counters, so there is a shape to design
   against rather than an empty screen to guess at.
3. **`P1-35`** — a Semantic Scholar key, or accept the retries. Ten minutes, and
   the 48-hour run is when it is felt.

### Explicitly *not* worth doing yet

**`P2-15`** (benchmark embedding models against each other). Its own text gates
it on `P1-16` and on `P2-09` being marginal, and it means a second embedding
column plus a full re-embed for a model you may never adopt. The benchmark that
*does* exist — `make bench-search` — measures the index and the methods over the
vectors already in the corpus, and that is the one worth running after the crawl.

**`P6-18` and `P6-19`** are marked ⚑ human. They are published-design decisions:
four light-theme canvas roles and three lockup values that were inferred rather
than decided. An agent picking values for those is inventing design, not
implementing it.

### A rule that keeps paying

Every handler that ends in a write should have a test that reads the row back.
That single rule is what caught the sitemap handler that never enqueued anything,
the digest that reported zeros forever, the gazetteer term that loaded no
patterns, and the route walk that asserted an empty list against an empty list.
The common shape is not a crash — it is a success message about work that did not
happen.
