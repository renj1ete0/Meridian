# Meridian — build tasks

**This file is the source of truth for what to build next.** Read it at the start of a
session, update it at the end of one. If work happened and this file didn't change,
something went wrong.

- Phases and their acceptance checkpoints: [docs/roadmap.md](docs/roadmap.md)
- Conventions and invariants before writing code: [AGENTS.md](AGENTS.md)

## How to use this file

- Task IDs are stable (`P1-04`). Reference them in commit messages and PR titles.
- Status: `[ ]` not started · `[~]` in progress · `[x]` done · `[-]` dropped (say why).
- Tick a task only when it runs, not when it compiles.
- A task should be one sitting of work. If it isn't, split it and give the parts new IDs.
- Tasks marked **⚑ human** need a judgment call and should not be delegated to an agent.
- Add new tasks freely; don't renumber existing ones.

**`v0.164.7`. Phases 0–3 are built; phase 1's checkpoint is not.** 5349 backend tests
against a real Postgres, 1053 frontend.

## Resume here (written 2026-09-26, end of session)

> **2026-10-08 (night) — `v0.165.5` + testing tooling, pushed; stacks left running.** Operator:
> GPU later; use Claude (relay) for synthesis now; dev-DB cleanup approved but the session's
> permission check refused it again, so it is still **to run by hand** (handover §0); GHCR not
> needed. Done: 12 more relay batches by a helper agent (48 relations, 8 attribute values);
> coverage measured (`Q-01`); mutation testing configured but not yet working (`Q-02`, see
> docs/guides/testing.md). **Resume with:** fix `Q-02` (mutmut imports the installed package;
> Stryker scores implausible), then `Q-03` (Hypothesis, CI), then from the relay agent's findings:
> the reference filter misses "- Author (year) Title" lists, entries split across passages and
> abbreviation glossaries; `pull` walks one long handbook in order (six batches of one book; cap
> passages per source per batch, `B-163`); the tag call resends the extraction call's passages
> (double tokens); "every agent … refused: waiting for …" reads like a failure. Then host drift,
> dead hosts, `make rebuild svc=`, the embedder's reconnect, the frontend items.

> **2026-10-08 (evening) — `v0.165.5`, pushed to GitHub (GHCR held); local stack left running.**
> Built: `B-164` passages read as words where a link was cut, `B-165`, `B-166` GMT+7:59 labels,
> `B-168` the answer page files a passage only under countries it names, `B-169` crowded graph
> labels move before they drop, `B-170` the tagging prompt shows wordings in use, `B-171`
> synthesis leaves reference lists out, `B-159` Map names must win clearly (ADR 0018), `B-172`.
> Measured and **not** adopted (docs say why): an OR fallback for whole-question lexical search,
> a per-source cap (`B-30`), titles in the embedding view. Measured and **parked for a GPU**:
> `B-167` cross-encoder reranking (clear gain, 20–35 s a question on CPU). Relay synthesis: 4
> batches, 40 relations, 15 attribute values. robots.txt "unreachable" in run 19 was 441
> zero-cost cache refusals on 127 hosts, 5 of which ever served a page: dead hosts, not what
> slowed the run. Run 19's yield a day on: 230 on-topic new pages in the hour (23% of those
> examined; runs 17–18: 607 at 30%, 593 at 35%), 28% still unexamined behind the CPU embedding
> backlog; one proven host supplied 18% of its new pages at 21% on a topic, the within-host
> drift `B-155` left open. **Next:** within-host drift (a recency window on a host's followed
> record, measured by a loop run), `B-163` (with `B-171` and a per-source cap per batch),
> `B-131`/`B-135` on the server.

> **2026-10-07 (night) — `v0.164.7`, pushed to GitHub (GHCR held), all stacks stopped.** Built today:
> `D-01`, `B-147`–`B-149`, `B-53` (ADR 0014), `B-99` (ADR 0013), `B-150` link vouches (ADR 0015),
> `B-151`–`B-161`, `B-155` proven by following (ADR 0016), `B-156` the site review's reader fixes,
> `B-157` map names (ADR 0017), `B-162` resumed synthesis runs. Loop runs 17–19
> (`meridian-calibration/loop/run17`–`run19`): run 17 589 on-topic pages/h against 356 / 310 for
> runs 15–16; run 18 590+ with no search in the window, followed links 35% on a topic; run 19
> labelled 19% of its pages within the window (run 17: 4%). Synthesis ran through the relay
> (operator's go): 34 relations, 5 attribute values from 120 passages; the relay agent row is
> left enabled. Local DB at `b155f011ed00`. **Next:** run 19's yield once labelled; robots.txt
> unreachable rising (443 in run 19); within-host drift (apad.gov.my, data.europa.eu);
> `B-163`, `B-159`; on the server `B-131`, `B-135`. **By hand:** the dev-database cleanup SQL in
> handover §0 (approved, but refused by the session's permission check).

> **2026-10-04 (night) — `v0.162.1`, pushed to GitHub (GHCR held), all stacks stopped.**
> Last: `B-142` web lint and format in `make lint` (oxlint + Prettier, ADR 0012); `make lint`
> now needs `web/node_modules` (`cd web && npm ci`).
> Built since `v0.158.0`: assistants connect over MCP (`B-138`, ADR 0003), the site's views as
> MCP tools with hybrid search (`B-139`), the Growth page (`B-140`, ADRs 0005 and 0010), Admin →
> Assistant access for tokens, which may be set never to expire, flagged (`B-146`, ADR 0011).
> **Before `make local-up`:** the local DB needs migration `b140a0b1c2d3`
> (`docker compose -f docker-compose.local.yml run --rm --build tools alembic upgrade head`).
> **Next, approved:** `D-01` move comment narrative into the feature docs (search done; go
> feature by feature, ticking each). **On the server:** `B-131` GPU override, `B-135` unattended synthesis once a
> model stage is set up.

> **2026-10-04 (late) — `v0.158.0`, all stacks stopped, local DB migrated to head.** Production
> target is a server, maybe with a GPU (ADR 0001). Decided and built: model order
> local → hosted API → Claude → relay (`B-137`, ADR 0002), current Claude models at effort high
> (`B-134`, ADR 0004), stricter triage for very long documents (`B-133`, ADR 0006),
> half-precision vector index after a benchmark (`B-136`, ADR 0007), one display zone, GMT+8
> by default (`B-145`, ADR 0009). Fixed: copies embedded last (`B-127`), GPU batch sizing and
> request splitting (`B-129`, `B-130`), Postgres memory and shm (`B-132`, `B-144`), three
> calendar-expired tests (`B-128`, `B-143`). Conventions: PEP 8/257 enforced by `make test`
> (`B-141`), docs restructured with a document per feature (`D-02`), decisions in `docs/adr/`.
> **Next, approved:** `B-138` MCP tokens (command + Admin, fix the profile/tool drift),
> `B-139` graph/area/gap/growth MCP tools, `B-140` the growth page (draw a mock in
> `docs/design/` first and get it approved), `D-01` move comment narrative into the feature
> docs, `B-142` ESLint/Prettier. **On the server:** run the GPU override and measure (`B-131`).
> **To restart locally:** `make local-up` (the database is already migrated).

> **2026-09-29 night — `v0.156.11`, live locally, pushed to GitHub; GHCR held.** Evening: proven
> hosts first (`B-115`, run 15: 35% of new pages on a topic, 4× run 14's count), sitemaps of
> proven hosts mined hourly (`B-116`, `B-118`; working — 14 read, 2,023 page links queued, but
> only 7 topic-matched and boosted, the rest wait at the bottom), four more search engines
> (`B-117`). Site review fixed (`B-119`–`B-126`): passages read as prose, node page on a phone,
> Gaps kept (3.6 s → 4 ms), Admin opens on Topics, harvesting only on-topic documents, label
> collisions, toggle clearance, neutral zero. **Next:** a 1h run to measure sitemap-sourced yield
> and decide whether a proven host's unmatched sitemap pages deserve more than the bottom of the
> queue; the daily yield report in Admin → Crawl health (proposed, not yet agreed). **Operator:**
> `MERIDIAN_CONTACT_EMAIL` (for `B-52`/`B-53` and to unblock the encyclopedia), `B-109`, `B-99`,
> the 27,729 queued gazetteer terms, a model for the Ask panel.

> **2026-09-29 afternoon — loop runs 12–14, `v0.155.2` live, unpushed.** `B-113` (an off-topic
> site's unjudged subdomains ordered last) raised new pages on a topic from 16% to 28% (run 13).
> `B-114` (domains refusing every request blocked) halved HTTP errors (run 14). Found: pages from
> search are ~40–50% on a topic, pages from followed links ~2%, and the followed links are almost
> all the backlog queued before `B-91` recorded parents; children of on-topic parents ran 4 of 5.
> Search runs dry within the hour (queries every 3h, `B-108`). **Operator decision:** more search
> supply (`B-109`, or a shorter seedsearch interval now some engines answer again) and/or fetching
> less of the pre-`B-91` backlog. Notes: `meridian-calibration/loop/run12`–`run14`.

> **2026-09-29 — `v0.155.0`, live locally, pushed to GitHub; GHCR held by the operator.**
> `B-65` shipped (RUM ranking, broad words 2–3× faster, rankings unchanged or within a row or two),
> `P6-10` shipped (the contested list), `B-53` measured and handed to the operator. Still
> open: `P6-06`/`P6-07` need a model (a local server answers on the host loopback only; the API
> container cannot reach it until it listens on the Docker bridge), `P2-15` paused, and the
> operator's calls: `B-109`, `B-99`, `B-53`, Admin in reader navigation. Details: handover §0.

> **2026-09-27 night — `v0.154.0`.** `P6-06`/`P6-07` "Ask the graph" built and wired to a
> configurable local model (`local-chat`, disabled; `LOCAL_CHAT_LLM_URL`, `LOCAL_CHAT_MODEL`,
> model editable in Admin → Agents), **not yet run against a model** by the operator's
> instruction. Next: point it at a local OpenAI-compatible server, enable, judge answers, then
> tick. Also still open: `P2-15` embedding-model benchmark (paused), `B-109` search API key,
> `B-99` and moving Admin out of reader navigation (operator calls). Details: handover §0.

> **2026-09-27 evening.** Built on the operator's "build all" and "keep refining": `B-83` (floor
> 0.50, operator-approved), `B-97`–`B-104`, `P6-39` (route mode), `P6-42` (this is noise),
> `P6-43` (watched questions), `P6-44` (wording). Loop run 9 (`loop/run9/comparison.md`): search
> ran dry (6% of fetches) → `B-103`, `B-104`. `B-60` and `B-65` were tried and backed out, with
> the reasons in their entries. Later: `B-60` shipped deferred (`v0.152.3`), `B-105` news
> queries repeat after 3 days, `P6-42` research fill, `B-106` field searches. Live at
> `v0.152.6`. Run 10 found every web search engine refusing this client (`B-109`, the operator's
> call: a search API key); `B-107` retries/paces, `B-108` seeding back to 3h, `B-110` daily
> backlog requeue (79k held), `B-111` `!science` queries (≈34 new results each, unthrottled).
> Live at `v0.152.10`. Run 11 (18:08–19:08, `loop/run11/`) measures search share with science
> supply.

> **2026-09-27 afternoon.** Loop run 8 (`loop/run8/`, 1h): search 70% on a topic, followed
> links 4% and two thirds of fetches → `B-90` (robots-refused search results not queued),
> `B-91` (parent page recorded; measure in run 9, then `B-92`). Operator reported the Map
> could not zoom after a level change and lines vanished → `B-93` (real zoom/pan, level
> follows zoom, lines at every level). UX review of the reader journey → `B-94`–`B-96`
> shipped, `B-97`–`B-102` open (`B-97` is the operator's). All deployed at `v0.149.3`.

> **2026-09-27 morning.** `B-89` shipped and deployed (`v0.148.13`): long documents are
> embedded from a sample first and the rest held unless the sample scores ≥ 0.41. The first
> labeller pass held 102 of 179 sampled long documents; the backlog is ~131k, of which ~93k is
> held (not counted by backpressure). Notes in `meridian-calibration/loop/b89/notes.md`. Next:
> once the valuable backlog is under ~20k, loop run 8 (1h) to see whether the crawl, no longer
> paused behind held passages, keeps its on-topic yield; then the runs 6–7 re-measure.

> **2026-09-26.** Both in-flight branches merged (`P6-41` answer page, ticked after screenshots;
> Map semantic zoom). The operator asked for a UX and information audit, then improvement and a
> 1h run. Shipped `v0.145.0`–`v0.148.7`, each its own commit: on-topic shading of Map fields and
> field gaps in Gaps (`P6-42`), plain reader wording on the landing, Find and concept pages
> (`P6-44`), bf16 embedding (`B-77`, 2× throughput), empty neighbourhood panel hidden (`B-78`),
> Markdown link syntax cleaned for display (`B-79`), the Answer view's heading-as-passage
> (`B-80`), the daily duplicate pass failing on >32,767 bound ids (`B-84`).
>
> **Loop run 6** (1h, `meridian-calibration/loop/run6/comparison.md`): 1,480 sources, directed
> share 18%, embedding ~292/min so the backlog nearly holds against the crawl, on-topic yield
> 72% of the 274 examined so far (early; re-measure once the window's chunks are embedded).
>
> **Evening:** `B-82`, `B-84`–`B-88` shipped (`v0.148.12`, deployed). Loop run 7 (1h,
> `loop/run7/comparison.md`): search share 18% → 36%, embedding backlog now falling during a
> crawl, examined window sources 64% on a topic. Every timetable job ok after the window.
> Next: re-measure runs 6–7 yield by seed source once their chunks are embedded. **Needs the operator:** `B-83` — the label floor (0.45) lets in generic
> government pages; raise it, add negatives, or describe topics.

## Earlier resume notes (written 2026-09-24)

> **Stopped 2026-09-25 21:16 (+08) for a shutdown.** Run 5 was stopped after 25 minutes —
> too short to judge; **rerun it in full** (2h, same procedure: `seedsearch --once`,
> `hostscore --once`, lift `MERIDIAN_WORKER_MAX_EMBED_BACKLOG` for the window only,
> `snapshot.sh start`/`end`, restore). Worker ceiling is back at 20000. `main` is at
> `v0.144.3`: committed; the live map carries its names but the images are not rebuilt —
> rebuild and deploy first. Two agent branches hold work in progress, committed at
> shutdown (check `git branch --list 'worktree-agent-*'`): the Map's **semantic zoom**
> (level control, bubbles splitting between levels — working and screenshot-checked, its own
> tests not yet written) and the **`P6-41` answer page** (complete, tests pass, needs screenshots)
> (`/api/explore/answer`, Find's Answer view, "Find more"). Review, finish, merge, bump,
> deploy — neither is merged.
>
> **Live state, 2026-09-25 evening (+08).** Optimisation loop, runs 3 and 4 (2h each,
> backpressure ceiling lifted to 200k for each window only, restored to 20000 after;
> notes in `meridian-calibration/loop/run3`, `run4`):
> - **Run 3** (`v0.140.3`) was not a material improvement: one topic got no page fetches
>   because its top-ranked rows were cited-paper DOIs (fixed, `B-68`), and throttled
>   lookups were written off (fixed, `B-67`; 858 revived).
> - **Run 4** (`v0.140.6`) was: that topic took its share of page fetches, search and
>   directed work rose, the throttle cooldown held. The loop continues; run 3 is the only
>   run without improvement so far.
> - **Embedder finding:** on this CPU a smaller batch is faster (1 → 119/min, 8 → 99,
>   32 → 67); the embedder now uses 1 on a CPU and sizes by memory only on an accelerator.
> - **Next:** `B-75` (first embedding tier newest first, so a run can measure its own
>   on-topic yield — built, deploying), then a per-host pace for hosts judged on-topic at a
>   low share. A Semantic Scholar key (operator) would clear most deferred DOI lookups.
> - Also on 2026-09-25, from the operator's review of the UI: titles cleaned (`B-69`),
>   gazetteer types (`B-70`), Map clusters named by field of work and called fields
>   (`B-71`, `B-74`), a topic web on the Map (`B-72`), saved views with topics (`B-73`),
>   About and Matrix views. Unbuilt screens and their blockers: `P6-40`'s audit note.

**What changed on 2026-09-24, and why.** An audit of what the corpus actually
holds found the crawl had drifted: nearly everything fetched came from following
links, only the cold-start search queries had ever run, and by content
(`P2-21`) the large majority of sources were about none of the topics. Link-following into large academic
sites, ranked first because an academic domain counted as peer review, was the
mechanism. Fixed today, each its own commit on `main` (`v0.118.0`–`v0.124.0`):
`P2-21` (content labels, merged from its branch), `B-46`, `B-43` (boilerplate,
merged and applied live), `B-47` (garbled PDF text
layers), `B-49` (embed text without URLs), `B-48` (host gate + requeue, applied
live to the queued backlog), `B-50` (academic domain ≠ peer review, applied
live), `B-51` (search seeding every 6h, Bing web off),
`B-52` (non-English seeds via Wikipedia interlanguage titles), `B-45`
(soft-404s), `B-44` (document duplicates).

**Loop status (2026-09-25 12:30):** iteration 1 done. Run 1 (12h) was stable
but showed the crawl outrunning search, labelling and embedding. Fixed as
`B-61`, with `B-63` for synthesis. `v0.140.2` is deployed with every backfill
applied (tagging coverage is in `~/Documents/gh/meridian-calibration/loop/run1/`).
The first question-set run exists (`.localdata/eval-runs/2026-09-25.yaml`,
waiting for the operator's grades). **Run 2, a 2h test window, started at
12:30.** Close it, compare with run 1, decide the next optimisation, and
continue.

**First thing next session:**

0. **The P1-16 checkpoint run (12h, operator's choice) started overnight
   2026-09-24**, once the evening's deploy finished (`meridian-p116-begin`, then a
   `meridian-p116-end` timer 12h later; `systemctl --user list-timers`,
   `begin.log`). The script and output are outside the repo
   in `~/Documents/gh/meridian-calibration/p1-16/` (`start.txt`, `end.txt`,
   `report.md` with the pass criteria). It is a first checkpoint, not P1-16's
   48h sign-off: daily jobs run about once in 12h. The timers fire only while
   the operator is logged in, since lingering is off.

1. **Merged overnight (2026-09-24/25), all on `main` at `v0.140.0`**, full suite
   passing: the Map (`P6-30`, `P6-31`, `P6-34`, `P6-35`), Find's neighbourhood
   (`P6-33`), routes (`P6-32`), Gaps and its sources (`P6-36`, `P6-37`), the
   question-set runner (`P2-22`), steering proposals (`P6-38`), places (`P2-23`),
   passage topics (`P2-24`), document kind and listings (`B-59`), diversity
   seeding (`P5-05`), merge folding (`B-41`) and smaller fixes. **None of it is on
   the live stack yet**, because the measurement window was running. Deploy it,
   then run each backfill's report before `--apply`. The document-kind one
   retires about a third of live passages, those of listing pages. `B-58` (DOI
   ranking) is merged too (`v0.140.0`); run `worker.requeue_dois` (report, then
   `--apply`), then enable its timetable row. The Map's route mode is `P6-39`.
2. **Check the live stack finished what was started** (handover §0): the
   embedding backlog and `worker.reembed`, the first `seedsearch` batch, the
   `translate` table (it was empty at handover), the crawler running under the
   new gate. Then run `worker.docdupes` (report, then `--apply`) and
   `worker.retopic` + `worker.hostscore` once the embeddings catch up, and
   measure the difference in what the crawl fetches (share from search vs
   links, share on-topic).
3. `B-53` below — calibrate labelling for non-English pages before the host
   gate judges a non-English host.

**Needs the operator (⚑):**
- **The first question-set run on the live corpus** (`P2-22`): it needs the live
  database's read-only role and the embedder. The delegated agent was refused
  that access, rightly, since it is a production read; run
  `scripts/run_question_set.py` yourself or grant it.
- Gaps thresholds, as built: thin is under 10 labelled sources, weak is under
  3 government or peer-reviewed sources, stale is over 3 years old, and the
  one-click boost is ×2 for 7 days. Keep them, or change them?
- **Reminder: `MERIDIAN_CONTACT_EMAIL` (optional; the operator chose to leave it
  unset for now).** Unset is a working state.
  Without it Wikimedia refuses the translation lookups, so no non-English
  seeds are written (`B-52`), and the DOI resolver skips Unpaywall. Restart the
  scheduler and worker after setting it.
- ~~Topic descriptions~~ **decided 2026-09-25**: every topic now has a short
  broad description (set through Admin with a reason; the operator left the
  choice between seeding and describing to the lead, and seeding was ruled out
  because the question set is held out). Forced re-label run the same day.
- ~~`P2-21` off-topic demotion~~ **decided 2026-09-25** ("ok"): applied at the
  default floor 0.30 after the re-label; 5,522 sources marked junk, none of them
  directed. Nothing deleted; the retention sweep was not run.
- `P0-15` question-set review, as before.
- Whether the default `search_languages` (in the global policy row)
  is the right set.

### Before 2026-09-24

**The direction changed today, and it is the operator's.** The goal is a system that
shows *how topics, terms and documents connect* — "walkability → transport → health →
biology" — and **fewer screens, each of them useful**, rather than views that look
good. The agreed shape, three screens and Admin:

- **Find** — search with a *neighbourhood panel* beside the results (inner ring:
  concepts a passage states a link to; outer ring: near in meaning), and the node
  page (`P6-01`–`P6-03`, built).
- **Map** — the corpus as named, **nested areas** (region › area › sub-area, so any
  number of subjects stays readable), circle area = passages collected (with a size
  key), an outline for weak or stale areas; click a line for its *bridge*; **route is
  a mode** (right-click → "Route from here…"); **steering by right-click** (more, less,
  make a topic, watch) and right-click on empty space to **suggest a new term** to
  crawl. The 3D point cloud (`P6-29`) becomes a toggle here, not a screen.
- **Gaps** — one ranked list of concrete gaps, each with its reason and a one-click
  action: thin or weak areas, routes with no cited link, question-set items that
  score low.
- **Admin** — as is.

**Cut, by decision:** the topic × topic matrix, the coverage grid (it would be
near-empty — nine attribute values in the whole graph — and its axes are arbitrary),
and separate Sources, Timeline, Geography and Frontier screens; those return, if at
all, as map filters. The mockups for the kept views are on the design canvas at
<https://claude.ai/artifact/PJtF8cqTSnUvW7qGd5ozay> (Areas zoomed out and in, Term
neighbourhood; Route and Coverage boards there predate the cut).

**Build order** (tasks below): data quality first — clustering noise into "areas"
makes useless areas — then the Map, then Find's panel, then Gaps.

1. `P2-21` topics from content, multi-label — **partial commit `9c574d8` on its
   branch**; finish (tests), merge, then the operator's two decisions
2. `B-43` boilerplate inside pages — **partial commit `60b025d` on its branch**;
   fix `B-46` first, then wire in and add the re-chunk pass
3. `B-44` duplicate documents, `B-45` soft-404 pages
4. `P6-30` areas, `P6-31` bridges → `P6-34` Map screen and `P6-35` steering from it
5. `P6-32` route, `P6-33` neighbourhood (+ Find panel)
6. `P2-22` question-set runner, `P6-36` Gaps

**Needs the operator:** review `eval/questions.yaml` (`P0-15`, drafted, 30 items,
all `reviewed: false` — scope of robotics/biology/economics, the three gap items,
one or two real questions of their own, the go/no-go threshold).

**The local stack** is up at `v0.117.1` with the **crawler stopped** (`docker compose
-f docker-compose.local.yml start worker` resumes it) and the relay agent
**disabled**. The site is `http://localhost:21116`. The graph: 257 entities, 197
edges, built by the relay through chunk 3,359; the next stretch is mostly
engineering-handbook chapters and legal boilerplate, so resume the relay after
`P2-21`'s off-topic demotion, not before.

The older gates still stand: **`P1-16`** (the 48h run, now likely 24h — see the
handover), **a Cloudflare account** (`P3-05`, `P3-09`), and **an API key or local
model** for synthesis at corpus scale — the relay (`P4-18`) answers one batch of
forty passages at a time.

Done 2026-09-23: `B-33`–`B-38`, `B-40`, `B-42`, `P4-18`, `P6-01`–`P6-03`,
`P6-25`–`P6-29`, `P0-15` drafted; `B-39` decided; `B-41` open. The daily jobs were
run by hand against the live corpus and exited cleanly; `harvest` left 1,035
ambiguous gazetteer terms for review; 307 furniture pages were demoted to junk.

Done this stretch: `P4-16`, `P4-17`, `P6-23`, and twelve defects the first real
corpus turned up (`B-21`–`B-32`). **Six of those were found by watching the stack
run, not by the suite** — `B-21` the nightly harvest dying on its own log line,
`B-24` the vector arm returning a third of its candidates, `B-25` and `B-27`
embedding that could never catch up because the sidecar had never once served a
backfill, `B-26` a crawl that ignored the attention vector, `B-31` a snapshot
that could not read the corpus, and `B-32` a deployment that could not start a
run at all. That is the argument for running the thing before trusting it.

Also: `P4-10`, `P4-13`, `P4-14`, `P4-12`, `P3-06`,
`P3-10`, `P3-11`, `B-07`, `B-09`, `B-11`, `P2-20`, `P5-07`, and phase 4's spine —
`P4-01` through `P4-04`, `P4-06` through `P4-09`, `P4-11` and `P4-15`. `P1-35` (a
Semantic Scholar key, ten minutes) is still ⚑ human and is felt during the 48h run.

**Explicitly parked, with reasons.** `P2-15` is gated by its own text on `P1-16` *and*
on `P2-09` being marginal, and means a second embedding column plus a full re-embed for
a model you may never adopt. `P6-18` and `P6-19` are ⚑ human: published-design decisions
that an agent would be inventing rather than implementing.

**What was verified live** — real government PDFs extracted with page-accurate chunks,
the injection screen clean across every page crawled, `render_js: auto` escalating and
not escalating on real sites, `Crawl-delay` honoured. And, as of `v0.78.1`, the stack
running whole in containers: the crawl storing what it fetches, the embedding sidecar
serving, and **hybrid search end to end outside a test** — which had never happened.
Four defects had to be fixed to get there, every one of them invisible to the suite and
present in the production compose file too (`B-12`, `B-13`, `B-14`, `B-16`), plus a
deploy runbook whose first two commands could not work (`B-17`).
**What is still unverified**: anything needing the server, Cloudflare or scale.
`docs/handover.md` §4 carries that list, and it is the checklist for the first deploy.

---

## Phase 0 · Foundations

*Checkpoint: `make migrate && make seed` yields a clean, empty database ready to crawl.*

- [x] `P0-01` Repo scaffold: layout, compose files, Makefile, `.env.example`, `.gitignore`
- [x] `P0-02` Brand and design system: mark, palette, typography, voice, UI mockups, assets
- [x] `P0-03` Licence, README, roadmap, task tracking, versioning policy
- [x] `P0-04` `meridian_core` package: `pyproject.toml`, `db.py` (engine, session, pooling)
- [x] `P0-05` SQLAlchemy models — queue
- [x] `P0-06` SQLAlchemy models — sources, chunks, figures
- [x] `P0-07` SQLAlchemy models — graph (entities, edges, attributes) with provenance columns
- [x] `P0-08` SQLAlchemy models — gazetteer, `topic_config`, `fetch_policy`, agent registry
- [x] `P0-09` SQLAlchemy models — `runs`, `steering_log`, `enrichment_queue`, `reports`
- [x] `P0-10` Pydantic DTOs in `meridian_core/schemas/` for every service boundary
- [x] `P0-19` Test suite: drift, rejection and completeness tests against real Postgres
- [x] `P0-20` Schema changes from the `P0-14` trace: `observations` table (with JSONB
      `qualifiers`), `edges.valid_from`/`valid_to`, `edges.similarity_dimension`/`disanalogy`
      with a CHECK enforcing §7.2
- [x] `P0-21` Fix: Alembic autogenerate does not detect CheckConstraints on existing
      tables, so two edge constraints existed only in the model. Hand-written into the
      migration; drift test added so it cannot recur
- [x] `P0-18` Fix role bootstrap: `.sql` → `.sh` (entrypoint has no psql var bindings),
      default privileges declared for both writers, pgvector enabled at init
- [x] `P0-11` Alembic setup + initial migration; run as `PG_MIGRATION_URL` (owner), not rw
- [x] `P0-12` `scripts/seed.py` — idempotent `config/*.yaml` → DB, config only, never content
- [x] `P0-13` Structured logging setup (`meridian_core/logging.py`), `run_id` on every record
- [x] `P0-14` ⚑ human — the ten questions, traced against the schema → `docs/design-questions.md`.
      Found five gaps; fixed in `P0-20`
- [~] `P0-15` ⚑ human — held-out question set. **2026-09-24: the operator wrote eleven questions of their own (Q31–Q41, set version 2); they are reviewed by definition, their grading criteria are agent drafts (`criteria_reviewed: false`). Q01–Q30 remain agent drafts to accept, correct or drop.** Deferred through phase 1 by
      decision — §14.1 uses it to measure whether the graph improves month to
      month, and there was nothing to measure until a corpus existed — with
      "re-open when phase 2 starts" as the condition. **Phase 2 has started, so
      it is re-opened here.** Write it before `P2-06` is judged, not after: a
      question set written once results are visible is a description of those
      results, and `P2-09` is then a go/no-go against a target drawn around the
      shot
- [x] `P0-16` ⚑ human — cold-start seeds: 8 authority roots + 5 query seeds. Kept short
      deliberately; tier-upranking of search results does the discovery
- [x] `P0-17` ⚑ human — gazetteer at 64 terms, jurisdiction-scoped, with 20 ambiguous
      surface forms flagged so the resolver disambiguates from context rather than
      guessing

## Phase 1 · Ingestion

*Checkpoint: runs 48h unattended without failing; the result becomes the dev corpus.*

- [x] `P1-01` Queue claim/pop — `meridian_core/queueing.py`. FOR UPDATE SKIP LOCKED,
      a lease rather than a status flip, exponential backoff with full jitter
- [x] `P1-02` Policy resolution — `meridian_core/policy.py`. Per-domain → global → file,
      shallow merge, plus consecutive-failure blocking
- [x] `P1-03` `fetch.py` — httpx for static, Crawl4AI for JS-dependent, `render_js: auto`.
      `auto` short-circuits on any page that already has a paragraph of visible text,
      so the browser is reserved for genuine shells (§6.4 constraint 1)
- [x] `P1-24` **Pin the validated address (closes the SSRF TOCTOU gap).** The request
      goes to the IP literal `netguard` judged, with `Host` and TLS SNI set to the
      original hostname; `follow_redirects` is off at the client level, and each hop
      is re-validated, re-resolved and re-pinned by hand. Verified by a test that
      hands the resolver a different answer on its second call
- [x] `P1-04` Robots handling, per-domain concurrency and delay, conditional requests —
      `worker/robots.py`, `worker/ratelimit.py`, `worker/crawl.py`. robots.txt is
      parsed here rather than by `urllib.robotparser`, which only became RFC 9309
      compliant in Python 3.13 and gives opposite answers on 3.12 for both
      `Disallow: /*.pdf$` and a longer `Allow` — §14.2 makes this a commitment, and
      one that varies by interpreter is not one. Conditional requests needed
      `sources.etag`/`last_modified`
- [x] `P1-05` Blocked-domain marking after N consecutive failures. `domain_signal()`
      decides what each outcome is evidence *of*, which is the part that had to be
      right: three answers, not two — the domain answered (a 404 or a rejected media
      type resets the counter), the domain is unreachable (timeout, 5xx, 429,
      redirect loop, decompression bomb, refused address), or no request went out
      (robots denial, already-blocked domain, so no evidence either way). A newly
      blocked domain is dropped from the limiter
- [x] `P1-06` `prefilter.py` — four gates, cheapest first: normalise, shape
      (scheme/host/extension), blocklists, then one batched query each against
      `queue` and `sources`. Normalisation is conservative — fragment, tracking
      params, credentials and default ports go, trailing slashes and query order
      stay, because anything that changes *which* resource is requested trades a
      visible duplicate for an invisible missing page. Wired into the loop as
      frontier expansion, which is what turns the fetcher into a crawler, and it
      finally gives `priority_for_domain()` (`P1-17`) a caller
- [x] `P1-20` **SSRF guard** — `meridian_core/netguard.py`. Post-DNS address
      classification, per-hop redirect revalidation, scheme allowlist, DNS-rebinding
      rejection, integer-encoded host normalisation, https-final enforcement
- [x] `P1-21` Content safeguards: content-type allowlist checked on the headers,
      streaming abort at `max_page_bytes`, decompression-ratio cap, plaintext final
      response refused unless the domain overrides `require_https_final`. The
      decompression is driven by hand through `zlib` in bounded steps — letting
      httpx decode meant a 64KB read arrived as one 67MB object, so the cap was
      checked after the allocation it existed to prevent
- [x] `P1-23` **Injection pre-screen, mechanical (no LLM)** —
      `worker/extract/injection.py`. Hidden text (seven techniques, each named in
      the finding), imperative HTML comments, and instruction-like phrasing, on
      the raw HTML *and* the extracted text. The design turns on one distinction:
      **hidden is the signal, imperative is not** — an article *about* prompt
      injection quotes the phrases and must not be flagged, or the flag becomes
      one people ignore. Flags only; §2.5 keeps the page stored, extracted and
      chunked, and `P4-06` is what eventually quarantines. 0 false positives
      across the 11 real pages crawled so far
- [x] `P1-17` Tier-derived queue priority — `priority_for_domain()`. Wiring it into
      enqueue happens with the fetcher in `P1-03`
- [x] `P1-18` Randomised per-domain delay — `jittered_delay_ms()`. Wiring it into the
      fetch loop happened with `P1-04` — `Crawler` draws a fresh jittered delay per
      request and takes the larger of it and any `Crawl-delay` from robots.txt
- [x] `P1-19` Record every fetch attempt in `fetch_attempts`, success or failure, and
      derive the health line's fetch success rate from it. Add a retention prune.
      `meridian_core/attempts.py`: `record_attempt()`, `fetch_health()` (rate plus a
      breakdown by outcome — the rate says something is wrong and only the breakdown
      says what) and `prune_attempts()`. Written by `Crawler.fetch` itself rather
      than by its callers, in the same transaction as the policy consequence
- [x] `P1-07` `extract/html.py` — two inputs, not one. Crawl4AI's `fit_markdown`
      where the browser ran, `trafilatura` (`favor_precision`) for everything
      that took the static path, which is most of the corpus. Fills the §5.2
      bibliographic columns, never guessing — a partial date is discarded rather
      than completed. Citations are mechanical (DOI, arXiv both schemes, PMID,
      handle) and read from the text *and* the links; the page's own identifiers
      are kept out of them, with arXiv's DOI derived from its URL because it
      publishes no `citation_doi` tag
- [x] `P1-08` `extract/document.py` — MarkItDown, `convert_stream` on fetched
      bytes only. The load-bearing part turned out to be an **explicit converter
      allowlist**: MarkItDown sniffs bytes with magika and ignores the declared
      media type, and its default registry fetches URLs, shells out to
      `exiftool`, and re-dispatches zip members — which a `.docx` reaches by
      being a zip. `enable_builtins=False` plus four registered converters.
      OOXML `core.xml` supplies the metadata MarkItDown does not return
- [x] `P1-09` `extract/pdf.py` — `pdftotext` over stdin, page boundaries from the
      form feed poppler already writes (exact, not reconstructed). Scan detection
      at ~100 chars/page is §6.6's fork: below it the document is queued for OCR
      and stays metadata-only, and its stray text layer is dropped rather than
      admitted as content. A missing poppler raises loudly — a worker that had
      quietly lost it would store every PDF and extract none of them. Title and
      creation date come from `pdfinfo`
- [x] `P1-10` `extract/figures.py` — figure extraction with captions, `v0.51.0`.
      §6.6's "start with captions, not vision": captions and alt text at
      ingestion, no image bytes, no bbox, no model. HTML has semantics to read
      (`<figure>`, `alt`); a PDF has only the convention that a caption line
      begins "Figure 3:", and the page is exact while the position on it is
      unknown — so no bbox is invented. Adds `figures.image_url`, because
      `file_path` is local and nothing downloads images, so a row would
      otherwise describe a picture nobody could look at. Vision is `P7-07`, the
      panel is `P6-14`

- [~] `P1-25` **Egress restriction** — host-level control shipped in `v0.39.0`,
      the proxy option still open. `deploy/egress-restrict.nft` gives the
      fetching process no route to RFC1918: not in the application, not in the
      container, and not reachable from either. Compose subnets are pinned so
      the rules have a stable target — Docker reallocates them otherwise, and a
      rule against a stale subnet matches nothing, protects nothing and looks
      exactly like one that works. `tests/unit/test_compose_topology.py` fails
      if the pinning is removed or a network moves outside the supernet the
      rules cover, and `docs/guides/deployment.md` §4b has the four verification
      commands that have to behave as stated.
      **Stays `[~]`**: the task names an egress proxy as the alternative, and it
      is not a drop-in one. A forward proxy resolves the hostname itself, taking
      DNS away from the worker and undoing `P1-24`'s address pinning — adopting
      it means deciding the proxy's destination ACL *replaces* pinning, which is
      a design decision rather than a deployment one. ⚑ human decides that;
      until then the host rules are the defence and they are applied, not
      merely written
- [x] `P1-22` **Network topology.** `internal: true` blocks outbound, but worker,
      crawl4ai and searxng all need it — the compose file admits this in a comment
      and never resolves it. Split into `internal` (postgres, api, web) and `egress`
      (worker, crawl4ai, searxng, orchestrator, cloudflared), with worker on both.
      crawl4ai drives a browser against hostile content and must hold no credentials
      and have no route to postgres
- [ ] `P1-16` 48h unattended acceptance run → `make snapshot-corpus`
- [x] `P1-26` **Crawl4AI needs a Dockerfile and a health check the worker trusts.**
      `Crawl4aiClient.from_env()` returns None when `CRAWL4AI_URL` is unset and the
      fetcher degrades to static — correct, but silent. A worker that has quietly
      lost its browser for a week should say so on the health line (§12.5), not just
      extract worse
- [x] `P1-28` **Sitemap discovery from robots.txt.** (Enqueueing was broken until
      `v0.26.0` — `seed_source="sitemap"` was never added to the enum, so every
      sitemap parsed and then raised at the insert. Fetch, parse and settle all
      succeeded, which is why nothing noticed.) `worker/sitemaps.py` parses
      urlsets and indexes; the loop grows a `sitemap` handler so the rows are
      claimed rather than orphaned. Two independent defences against XML entity
      expansion, because lxml expands by default — measured, not assumed. A
      sitemap may not name another site, since a hostile robots.txt would
      otherwise write to the frontier at its target's tier priority. Verified
      against the real seed list: most advertise one, the largest runs to
      several thousand URLs, and all are served as `text/xml` — which is *not* in
      `allowed_content_types`, so `policy_overrides` is what makes the feature
      work at all. Paired with `worker/topicmatch.py`: a sitemap URL gets the
      topic its path implies, not the one the triggering page happened to carry,
      and an unmatched URL is deprioritised to -10 rather than dropped
- [x] `P1-29` **Persist the robots cache across restarts** — `v0.66.0`. A
      `robots_cache` table holding the *raw file*, re-parsed on load, so a parser
      fix reaches everything already cached. Two layers on two clocks:
      `time.monotonic()` in memory, where NTP cannot move it, and wall clock in
      the row, because a stored monotonic deadline would be compared against a
      different clock after exactly the restart it exists to survive. `missing`
      and `unreachable` stay distinct — both store no body and mean opposite
      things, and collapsing them would turn every origin that was down at
      restart into one that granted permission. Plus a per-origin lock, since a
      lane claims many URLs from one domain at once and every one of them used to
      miss the empty cache
- [x] `P1-32` **Replacing chunks orphans the edges that cite them** —
      `v0.67.0`. Decided: **supersede, never delete.** Re-deriving affected edges
      needs the slow loop and produces a different edge anyway, so the old one
      would have to be invalidated regardless; a foreign key is impossible,
      since Postgres cannot enforce one on array elements. Stamping
      `superseded_at` keeps every citation resolvable, keeps the text an edge was
      actually derived from (§2.4 re-derives from source chunks, and the page has
      changed), and leaves reclamation to the sweep — a decision a person makes
      rather than one a crawl makes at write time. The unique constraint on
      `(source_id, chunk_index)` became partial over the live set, or the
      replacement it exists to allow would be refused at write time. Every query
      that serves the corpus filters on it, the novelty gate included: without
      that, a changed page's new chunks are all marked duplicates of the
      generation they replaced
- [x] `P1-31` **The retention sweep exists** (`v0.35.0`).
      `meridian_core/retention.py` plus `python -m worker.sweep`. Measuring the
      real corpus before writing it changed what it is: there was nothing to
      reclaim and no orphans, and **three sources whose `raw_file_path` pointed
      at nothing**. The first is structural rather than lucky — `P1-11` never
      writes the files §5.4 says to drop, and `retention_for` only moves a tier
      *up*, so a file that exists was written under a tier that keeps files and
      cannot have fallen below it. The sweep says that out loud rather than
      reporting as though it did work. Three verdicts and only one deletes:
      `droppable` and `orphaned` go with `--apply`, `dangling` is reported and
      never touched — the row is the only record the fetch happened and its text
      is still in the corpus. Primary is refused when the plan is built and
      again when it is applied. Dry run is the default because a re-crawl
      returns today's web, not the page that was fetched
- [x] `P1-45` **`sources.raw_root`** — `v0.38.0`. Provenance, not a lookup:
      `raw_file_path` stays relative and `MERIDIAN_RAW_ROOT` still resolves it,
      because an absolute path would bake in a container's mount point. The
      sweep now separates `elsewhere` from `dangling`, and `make
      snapshot-corpus` warns when sources reference a store it is not
      archiving — it tars one root, so a multi-root snapshot was silently
      incomplete. Not backfilled: rows written before this do not record their
      root, and guessing would turn "unknown" into a confident wrong answer for
      exactly the rows the column explains
- [ ] `P1-35` **Get a Semantic Scholar API key, or accept the retries.** The
      anonymous quota throttles hard and the penalty outlasts the burst by
      minutes, so under a real crawl a share of `doi` rows will retry rather
      than resolve on the first pass. Correct behaviour — nothing is lost — but
      it spends queue slots. `SEMANTIC_SCHOLAR_API_KEY` is free to request and
      is read already; this is a registration, not code. Measure the retry rate
      during `P1-16` before deciding it matters. ⚑ human
- [x] `P1-43` **The browser path did no boilerplate removal of its own** —
      fixed in `v0.31.0`. It took `fit_markdown` as-is on the reasoning that
      `PruningContentFilter` had seen a rendered DOM this process never had,
      and that premise was simply wrong: the rendered HTML comes back in the
      same response and is already what the extractor receives. So whether a
      page kept its navigation depended on whether the fetcher escalated it to
      a browser — a decision made on how much text the *static* fetch found,
      which is unrelated to how much boilerplate the page carries. Now
      trafilatura extracts from the rendered HTML at the same precision as
      everywhere else, the payload contributes metadata and JS-inserted links,
      and `fit_markdown` is the above-floor fallback for pages with no semantic
      structure to detect. Superseded text: ~~
      `extract/html.py` has two inputs and treats them very differently. The
      static path runs `trafilatura` configured to favour precision — it would
      rather lose a sentence of body than gain a navigation menu. The browser
      path uses Crawl4AI's `fit_markdown` as-is, and `PruningContentFilter` is
      a far more permissive filter than that. The asymmetry is visible in the
      dev corpus: chunks that are repeated station lists, an app promo banner,
      and a footer link block, and their markdown link syntax is what identifies
      which path produced them. This matters more than it looks — boilerplate
      becomes entities, entities become edges, and it also inflates the novelty
      gate's duplicate count with text that was never content. Either run
      trafilatura over the rendered HTML too, or tighten the filter Crawl4AI is
      asked for~~
- [x] `P1-44` **A source now records which extractor produced its text**
      (`v0.31.0`). `sources.extractor`, written at keep time, plain Text rather
      than `constrained()` — the names grow whenever an extractor or a failure
      mode is added, and a CHECK would recreate `P1-28` exactly. Nullable with
      no backfill: rows extracted before the column existed get NULL, which is
      the truth. Superseded text: ~~
      `extra->>'extractor'` is NULL on every source in the dev corpus, so
      answering "did this come through the browser or the static path" means
      inferring it from whether the text contains markdown link syntax. That is
      how `P1-43` was found, and it should not have needed detective work:
      `ExtractedDocument` already carries `extractor`, and it is dropped at
      `upsert_source`. One column, written at keep time~~
- [x] `P1-36` **`make snapshot-corpus` now calls a script that exists**
      (`v0.34.0`). Snapshot and restore, with the database and the raw store
      travelling together — a dump without the files its `raw_file_path` values
      point at is a catalogue, not a corpus. `pg_dump` runs inside the container
      so client and server versions cannot mismatch. The restore verifies
      checksums before touching anything, refuses a non-empty target unless
      `--replace` and then asks for the source count to be typed back, and
      afterwards samples `raw_file_path` to prove the two halves match.
      `tests/unit/test_scripts.py` stops the whole class recurring
- [x] `P1-37` **`make backup` and `make build-push` both work** — `v0.76.1`.
      `scripts/backup.sh` shipped in `v0.42.0` — unattended, asks nothing, fails
      loudly, warns when the backup root shares a filesystem with the data root
      (a backup on the disk it protects survives an accidental delete and
      nothing else), checks the dump is non-empty because `pipefail` does not
      reach across a redirect, and rotates only after the new one is written.
      `build_and_push.sh` landed in `v0.76.1`: one multi-arch manifest per
      application image, tagged by commit SHA. It **refuses a dirty working
      tree** and never tags `latest`, because both would undo the thing the SHA
      tag is for — scaffold §5 pins the SHA in compose so a bad build does not
      roll out on restart and rollback is a one-line edit, and a tag naming a
      commit whose code is not what was built is discovered to be wrong while
      rolling back. `orchestrator` has no Dockerfile yet (`web` gained one in
      `B-05`) and is skipped *loudly*; a drift test checks the image list against the services
      compose actually builds, since one added there and not here never gets
      built for arm64 and fails on the Pi days later
- [x] `P1-27` **Per-domain `render_js` learning** — `v0.70.0`. Built before
      `P1-16` rather than after, because the mechanism is self-tuning: the 48h
      run both benefits from it and produces its evidence, where waiting means
      paying double for two days first. Consecutive escalations, reset by a
      single static success; only ever upgrades `auto` to `always`, so an
      operator's `never` stands; and the conclusion **expires** after a week —
      without that, a domain skipping the static fetch produces no evidence about
      itself, so the first correct conclusion becomes permanent and a redesign is
      invisible

## Phase 2 · Embeddings and search — the go/no-go

*Checkpoint: is searching the corpus already useful with no model involved?*

- [x] `P2-01` `embeddings.py` — bge-m3 through sentence-transformers, lazy-loaded
      and dimension-checked at load rather than at insert. Runs as a **separate
      backfill pass** (`python -m worker.embed`) over `embedding IS NULL` rather
      than inside the fetch loop: the crawler never carries a 2.3GB model, and
      the queue is a predicate so the pass is resumable with no state outside
      the table. `FakeEmbedder` gives `P2-03` and `P2-06` something to build
      against without the download. `sentence-transformers` is an optional
      extra, so the worker image stays lean
- [x] `P2-02` Chunking with `page_or_offset` captured at extraction time —
      **built in phase 1** (`v0.15.0`), because `P1-07` was discarding the text
      it extracted and a `background` source keeps no raw file to re-derive from.
      `worker/extract/chunk.py` cuts on structure (paragraphs, then sentences,
      then a hard cap) and every chunk is a verbatim slice: `text[offset:offset +
      len(chunk)] == chunk`. `meridian_core/chunks.py` writes them in the same
      transaction as the source row. Unchanged content is left alone; changed
      content is replaced, and the new ids are what make §6.3's high-water mark
      re-read the page
- [x] `P2-03` `novelty.py` — cosine gate, drop above 0.95. Built as a **mark, not
      a delete**: `meridian_core/novelty.py` records `novelty_checked_at`,
      `nearest_similarity` and `duplicate_of` on the chunk, and `P1-31`'s sweep
      is what spends the verdict. A gate that deleted could report no pass rate
      (§12.5), could not be re-tuned against the corpus it collected, and would
      leave nothing to audit. A chunk is only compared against chunks written
      *before* it — otherwise two identical chunks are each other's nearest
      neighbour, both clear the threshold, and the text is lost rather than
      deduplicated. `worker/novelty.py` is the pass
      (`python -m worker.novelty`): its own process, because the gate is
      Postgres and arithmetic and needs no model at all. Source-level demotion
      follows §5.4 — `background` → `junk` at ≥90% duplicate chunks, and
      **never** `primary`. Verified against a real crawled corpus: it found the
      boilerplate a site repeats under every URL, at similarity 1.0, and
      demoted nothing
- [x] `P2-04` pgvector HNSW index; measure recall and latency at corpus size — — **measured on the live corpus 2026-09-25 (under crawl load): HNSW recall@10 ≈ 0.97 at every ef_search tried; vector p50 ≈ 25 ms; the lexical arm is the slow one (p50 ≈ 0.26 s, p95 ≈ 0.44 s) and dominates hybrid latency — watch it as the corpus grows (`B-65`)**
      **index built in v0.30.0, measurement outstanding.** `vector_cosine_ops`,
      matching the operator everything here already uses; an index built for
      another operator class is not slower, it is unused, and the planner
      declines it silently. Built before the long run rather than after because
      maintained incrementally it costs nothing per insert, where building one
      over a finished corpus is a single operation wanting more
      `maintenance_work_mem` than the target has. `m`/`ef_construction` left at
      defaults — tuning them is a measurement against a real corpus, and
      re-tuning later is a REINDEX rather than a migration. **Stays `[~]` until
      `scripts/benchmark_search.py` is run against `P1-16`'s corpus**
- [x] `P2-05` `tsvector` index and trigger — shipped as a **generated column,
      not a trigger**. Postgres 12 made the trigger unnecessary and a generated
      column is strictly stronger: it cannot be bypassed by a write path that
      forgot to fire it, cannot drift from `text` after a bulk UPDATE, and needs
      no ordering agreement with other BEFORE triggers. `chunks.search_vector`
      is `to_tsvector('english', text)` STORED, with a GIN index. The regconfig
      is named because the one-argument form reads a session GUC and is
      therefore not IMMUTABLE — and naming it pins the stemming too. Note
      `alembic check` **cannot** guard this: it warns "Computed default on
      chunks.search_vector cannot be modified" and moves on, so the drift test
      compares the model's expression against the database's own record of it
- [x] `P2-06` `search.py` — hybrid retrieval, RRF fusion, **filters before
      vector search**. `meridian_core/search.py`: two arms fused by reciprocal
      rank, because the arms' scores are not comparable — `ts_rank_cd` is
      unbounded and length-dependent, cosine distance is bounded — and RRF needs
      only the ordering, which is the part both agree is meaningful. Filters are
      predicates *inside* both arm queries; the anti-pattern returns a truncated
      set with nothing to say it was truncated, and an empty page then reads as
      a thin corpus rather than a query built the wrong way round. A missing arm
      is reported (`SearchResult.degraded`), not hidden: lexical-only is
      legitimate, since the embedder is a separate pass, but a caller that
      thinks it ran hybrid and ran half will conclude the wrong thing. The query
      vector is supplied by the caller — `meridian_core` is imported by the API
      and the orchestrator and neither should acquire a 2.3GB model dependency.
      Verified live: both arms ran over the real dev corpus and fusion
      reordered rather than rubber-stamping either arm
- [x] `P2-14` **A source records no topic, so search cannot filter by one** —
      `v0.68.0`. `sources.topic_labels`, written at keep time from two kinds of
      evidence: the claim's topic (provenance — why the URL was fetched) and a
      match against the **final** URL. Labels accumulate rather than replace, or
      the label would depend on which crawl ran last. NULL and `{}` stay
      distinct — "never examined" versus "examined, matched nothing" — and the
      topic filter excludes both, since neither has been established as
      belonging to a topic. Already-crawled sources: `python -m worker.retopic`,
      which deliberately records **less** than the live path, because the crawl's
      own topic is not recoverable after the fact and the join that would
      recover it is the one this task rejected
- [x] `P6-24` Topic filter control in Explore — `v0.72.0`. `/stats` now carries
      the configured topics and a count of sources nothing has examined. The
      second is the point: a topic filter excludes those, correctly and
      silently, so a reader who narrows and sees three results cannot otherwise
      tell the corpus holds three hundred nobody looked at. The control says so
      once, while narrowing. Filtering re-runs the search rather than filtering
      results in place, because fusion ranks a candidate pool
- [x] `P2-20` **Age-aware ranking, by document kind** — `v0.90.0` and
      `v0.95.0`. A half-life per source tier, overridable per topic, applied as
      a **decay on the fused score** rather than a filter — a filter removes,
      a decay reorders, and reordering is what "probably less current" means.
      `peer_reviewed` does not decay at all, which is the point rather than a
      detail: a global "newer is better" multiplier buries the foundational
      paper, and for a corpus with an academic spine that is the failure that
      matters. **An undated document is neither old nor new** — around a third
      of crawled pages have no date and whichever default you pick is wrong for
      the other kind, so decay applies only where a date exists and the hit
      says so. **The adjustment is shown**: the hit carries its age, its factor
      and its pre-decay score, because a result silently demoted is one the
      reader cannot audit. Floored at 0.25 so decay cannot become deletion by
      arithmetic, and **off by default** — it changes what search returns, and
      `P2-04`'s benchmark and `P2-09`'s go/no-go are measured against the
      current baseline. **Second half, `v0.95.0`**: `urgency_for_tier` reads the
      *same* table and lifts a fast-rotting source's place in the queue, for two
      reasons pointing the same way — its claim stops being current, and the
      page itself is likelier to be gone. `peer_reviewed` gains nothing, having
      no half-life. Bounded so it reorders *within* a tier and cannot promote an
      informal page above a government one (§5.2); all four of the crawl's
      enqueue sites use it. `P7-06` should read these half-lives too rather than
      inventing a second set — the way two sets diverge is that nobody notices
      there are two
- [ ] `P2-15` **Benchmark embedding models against each other.**
      `scripts/benchmark_search.py` measures the index and the methods over
      whatever vectors are in the corpus; comparing bge-m3 against an
      alternative is a different job, because it means re-embedding the corpus
      into a second column and running both. Worth doing once, after `P1-16`,
      and only if `P2-09` is marginal — swapping the embedder is a full re-embed
      and a migration, so it needs a measured reason
- [x] `P2-07` `/api/explore/*` read endpoints on the read-only session —
      `v0.43.0`. Five routes over `meridian_core.search`, plus `/health`. The
      read-only guarantee is tested twice: through the session's
      `SET TRANSACTION READ ONLY`, and against the catalogue — the first masks
      the second, so a test that only saw the transaction error would keep
      passing if the grants were widened. DTOs live in `meridian_core.schemas`,
      not the API. **No embedder**: depending on `sentence-transformers` puts
      gigabytes in an HTTP path and accepting a client-supplied vector puts an
      unauthenticated float array into a distance operator, so `embed_query()`
      is a seam returning None and every response reports `degraded`. `P2-17`
      is the sidecar that closes it
- [x] `P2-17` **An embedding sidecar, so search stops being lexical-only** —
      `v0.53.0`. `python -m worker.embedserver` from the worker's own image with
      a different command, on `internal`, no credentials. The API must not carry
      the model and must not take a vector from a caller — not mainly for
      security, but because a vector from a different model is *meaningless*
      against this corpus and compares without erroring, ranking nonsense
      confidently. Verified end to end: both arms ran with `degraded: false`,
      and killing the sidecar left the search answering on one arm. Fixed a real
      bug found that way — an absent embedder and a broken one read identically.
      `P2-19` is unifying the backfill onto the same service
- [x] `P2-16` **Explore landing components** — `v0.41.0`. §8's default state as
      components taking typed props: search field with the `hybrid` marker,
      the four counts, §12.5's three entry points as cards, and "where you
      were". Deliberately not a page — `/api/explore/*` does not exist, and a
      page would have to show fabricated numbers, which inverts the one thing
      §12.5's first state is for. `P2-08` is the wiring
- [x] `P2-08` Minimal Explore UI — `v0.46.0`. The page, the results list, and
      the client between them; the components existed from `P2-16`. Every result
      shows source, tier, date and page/offset. The degraded search is
      **rendered**: shown whenever set, escalated when the result set is empty,
      and the test that makes that mean something is its converse — with
      `degraded: false` and no hits the caveat must not appear, or readers learn
      to skip it
- [ ] `P2-09` ⚑ human — run the held-out questions; make the go/no-go call
- [x] `P2-10` ⚑ human — frontend framework chosen: **React + TypeScript + Tailwind CSS**,
      with Sigma.js v3 + graphology for the canvas
- [x] `P2-11` Frontend scaffold: Vite + React + TypeScript + Tailwind v4, `web/`
      structure per scaffold §2. The app only ever calls `/api/...` relative, so
      no environment-specific base URL exists in the source; the proxy target is
      a property of the machine and reads `VITE_API_PROXY`, defaulting to
      `localhost:8000` (a natively-run uvicorn) with `localhost:21114` for the
      compose stack. The landing state states the absence rather than promising
      a feature, per the voice guide — there is no retrieval path yet and it
      says so
- [x] `P2-12` Design tokens in code — both palettes, the type scale and §5's
      surface rules as CSS custom properties in `web/src/styles/tokens.css`,
      wired into Tailwind v4's `@theme` so the token file *generates* the
      utilities rather than being mirrored into a second config that can drift
      from it. "Tokens stay the single source" is enforced rather than asserted:
      `web/tests/tokens.test.ts` parses the palette out of the design system and
      checks it both ways — every published colour defined at its published
      value, and no colour literal anywhere outside the token file. It caught
      two things on its first run, one of them in its own docstring
- [x] `P2-13` `web/src/lib/api.ts` — typed client over `/api/explore/*`,
      `v0.45.0`. No base URL and no environment read, enforced by a test that
      greps for them. The cross-language contract is held by two links —
      `tsc` ties each interface to a runtime field list, and a drift test ties
      that list to the pydantic class — and the second is the one that matters:
      drop a field from both the interface and the list and `tsc` stays green
      while the server contradicts it
- [x] `P2-18` **Four things `P2-07`'s surface made awkward to consume** —
      `v0.49.0`. `page_unit` derived in core so §5.3's rule is applied once
      rather than by every consumer, and None when the media type was never
      recorded — a wrong label on a citation someone opens is worse than an
      honest hedge. `arms` is a `Literal` so it crosses the boundary like
      `SourceTier`. `detail` is always a string, with the structured form kept
      under `errors`. `/stats` carries `as_of`

- [x] `P2-19` **The backfill still loads its own copy of the model** —
      `v0.69.0`. `worker/vectors.py`: `PreferRemote` asks the sidecar,
      `LocalEmbedder` drives the in-process model off the loop. Falls back when
      the sidecar is unreachable — a backfill can afford the wait — and the
      switch is logged with its reason, because a pass that quietly loads a
      second model looks exactly like one using the sidecar and the only symptom
      is memory pressure. One-way within a process: the memory is already spent.
      A sidecar naming a **different** model is refused, checked on every
      response rather than once at startup, since that is the one failure here
      nothing downstream can detect — mixed vectors compare without erroring

## Phase 3 · MCP read surface

*Checkpoint: an external agent can retrieve usefully.*

- [x] `P3-01` MCP server scaffold inside `api` — `v0.44.0`. Mounted at
      `/mcp` on the same app as `/api/explore/*`: same corpus, same read-only
      role, same provenance, and §11.1b is explicit that all three integration
      directions hit one validation layer. DNS-rebinding protection is on and
      defaults to loopback, because it is off by default in the SDK and matters
      the moment a tunnel is in front
- [x] `P3-02` Read tools: `search_chunks`, `get_source_metadata`,
      `list_new_since`, plus `corpus_overview` — `v0.44.0`. The **instructions
      are load-bearing**: they are the only thing a model reads before deciding
      how to treat the results, and they name the three mistakes it would
      otherwise make. The retrieval mode rides on every result, not just at
      connect, because a client summarises individual calls; and when degraded
      the wording says what to *do* about it, since a client told only that a
      flag is true will not think to try synonyms. `list_new_since` is §11.1a's
      entry point and needs no embedder at all
- [x] `P3-03` Scoped tokens: `allowed_tools`, expiry (spec §11.4) — `v0.47.0`
      mechanism, `v0.48.0` enforcement. Secrets are never compared in code: the
      presented token is hashed and the hash looked up. NULL `allowed_tools`
      grants nothing. Every rejection returns None, because naming which of
      unknown/revoked/expired applies confirms a token exists. **Anonymous
      access is an explicit opt-out**, so a deployment that forgets to configure
      credentials refuses rather than serves, and the scope is checked *per
      tool* — at the transport, the tool asked for is the only thing separating
      a read session from a write one. Rate limiting is `P3-11`
- [x] `P3-04` `run_readonly_query` behind the read-only role, statement timeout,
      row cap — `v0.54.0`. Runs as `meridian_guest` (`P3-07`), not
      `meridian_ro`, because the latter can read the table holding every token
      hash. The role is the enforcement; the textual checks only make a refusal
      legible. The timeout is what makes it exposable at all — the role stops a
      query reading what it must not and does nothing about one that reads what
      it may forever. Every query is logged either way, which is §12.4's actual
      request: the queries an agent writes here are the next curated tools.
      Registered only when `PG_GUEST_URL` is set
- [ ] `P3-12` **Two ARM boards, pull-based, auto-updating** — `v0.153.0`, built; not yet run on the
      boards (tick when it has). Operator (2026-09-27): split across two 16 GB arm64 SBCs, a
      deployment guide, auto-redeploy via Watchtower as in their british-shorthair repo.
      Images from GHCR at a `stable` channel moved only by `make promote SHA=…`; Watchtower
      (label opt-in, `autoupdate` profile) on both boards; `migrate` runs on a promoted tools
      image; embedder on board 2 behind a shared token, reached over `lan` through one nft
      exception, and `embed` remote-only so board 1 never loads the model. Guide:
      `docs/guides/deploy-sbc.md`
- [~] `P3-05` Cloudflare Tunnel + Access in front of the API — the code side
      is done and the rest is **your Cloudflare account**. `cloudflared` is in
      compose, `P3-08` verifies assertions, `P3-03` enforces scopes, and
      transport security defaults to loopback so a tunnel is refused until the
      real hostname is named. `docs/guides/setup.md` §8a–8e is the runbook: tunnel,
      Access application, the four environment values, and the verification
      that must be done before trusting any of it
- [x] `P3-07` **`meridian_guest` role** — `v0.36.0`. SELECT on the corpus and
      the graph (eight tables), nothing else. `meridian_ro` can read every table
      including `agent_tokens`, whose `token_hash` is the one secret in the
      schema. Grants are a migration (tables must exist first), the credential
      stays in `init-roles.sh` (§11.11), and the role is created NOLOGIN when no
      password is configured — so a deployment that shares nothing gets correct
      privileges on a role that cannot connect. **No default privileges on
      purpose**: a table added later is invisible to guests until granted, which
      is the fail-closed direction. The privilege matrix is tested over
      `pg_tables`, so a new table forces the decision rather than inheriting one
- [x] `P3-06` `grants` table + `agent_tokens.grant_id` — `v0.87.0`. **The unit
      of sharing is a person, not a credential.** Somebody given access holds
      several — a browser session, an MCP client on a laptop, another on a
      server — and revoking their access has to revoke all of them at once; a
      per-token model leaves you chasing credentials, and the one you miss is
      the one that still works. `revoke_grant` does both in one transaction,
      with a test that proves it. Tokens are marked revoked rather than
      deleted, because an audit entry points at a token row and deleting it
      would leave the history unable to say whose credential made a call.
      **A person grant must have an expiry**, enforced by a CHECK: §3 says an
      access grant with no end is one nobody revisits, and a code path that
      forgot would otherwise create one. A service grant may be open-ended — it
      belongs to a machine somebody is already running. Profiles are named sets
      defined in code, never free-form lists (§3), **no profile carries a write
      tool** including `operator`, and an unknown profile grants nothing rather
      than everything
- [x] `P3-08` Access JWT verification — `v0.50.0`. Verified against the team's
      published keys with audience and issuer checked, on every request, and
      **no path where an unverifiable assertion is treated as
      anonymous-but-allowed**. An unreachable JWKS refuses rather than bypasses:
      letting requests through when keys cannot be fetched turns a dependency
      outage into an auth bypass. Both env vars required — a team domain alone
      verifies that *some* application on the team signed it. Identity comes
      from the verified claims, never from the unsigned
      `Cf-Access-Authenticated-User-Email` header Cloudflare also sends.
      Mapping identity → grant is `P3-06`
- [~] `P3-09` **OAuth is the path for a hosted client, not service tokens** —
      server half done in `v0.52.0`. The surface advertises
      `/.well-known/oauth-protected-resource/mcp` when authentication is on, so
      a client handed only a URL can discover where to authenticate; anonymous
      mode advertises nothing, since offering an endpoint that is not enforced
      sends a client through a flow for no reason. Tokens issued for another
      resource are refused. The Cloudflare side is `P3-05`'s configuration.
      Service tokens for CLI agents (§11.1a) are still unbuilt
- [x] `P3-10` Grant scoping in the tool layer — `v0.88.0`. `filters_for`
      **intersects rather than replaces**, which is the whole property: a guest
      may narrow their own search further and cannot widen it, whatever they
      send. A guest asking only for topics they do not hold gets nothing rather
      than everything they do hold — answering the question they did not ask
      would be the friendlier bug. `max_source_tier` names a floor in authority
      order, and **an unrecognised tier admits nothing**: treating a typo as
      "no ceiling" is a mistake failing in the direction that widens access.
      §5's two defaults are both off — `raw_files`, because serving the raw
      store to somebody else is redistribution of third-party material rather
      than sharing what was extracted from it, and the operator's annotations,
      which §12.5 predicts become the highest-quality layer precisely because
      they are the most personal thing in the system. A guest's search is
      always `cleared_only` (`P4-14`), since this is content going to somebody
      else's model
- [x] `P3-11` Per-grant audit log and per-token rate limiting — `v0.89.0`.
      **Audited by grant**, because "what has this person's model been reading"
      is unanswerable from a per-token log once they hold three clients; the
      token is a column, not the index. **Arguments are kept and results are
      not** — what somebody searched for is the audit, what came back is the
      corpus, and copying it here would be a second store of the same content
      with none of the retention rules the first one has (§5.4). Refusals are
      recorded too: a log of successful calls answers half the question, and a
      grant repeatedly refused a tool is the more interesting signal.
      **Rate-limited per token, not per grant**, even though everything else
      here is per grant — what is being throttled is a client in a retry loop,
      which is a property of one client, and limiting the grant would let one
      misbehaving laptop silence the same person's phone. Refused calls do not
      count against the limit, or one misconfiguration becomes two. An audit
      write that fails never raises: monitoring that takes the read surface
      down is the outage it exists to detect

## Phase 4 · Graph and writes — the loop closes

*Checkpoint: a model can write validated, provenance-bearing edges.*

- [x] `P4-01` Apache AGE setup, graph schema, typed node ontology — **the
      store, `v0.91.0`**. `deploy/postgres/Dockerfile` compiles AGE 1.7.0 onto
      the pgvector image, so `make quickstart` starts a database that already
      has both and nobody installs an extension by hand. §3 chose one store;
      no published image carries both. Verified on the database holding the
      crawl: the image swap preserved every row, and a Cypher `CREATE` and
      traversal run as `meridian_rw`, the role the application actually uses.
      The typed ontology already existed — `NODE_TYPE` has constrained nodes to
      fourteen kinds since `P0-07`. **Four failures on the way in, each naming
      something nobody wrote**, all in `docs/handover.md`: a sample config file
      that `initdb` alone reads, a graph named after the project colliding with
      the role in `"$user"`, `create_graph` needing `ag_catalog` on the path for
      `graphid_ops`, and `ALTER TABLE ... INHERIT` requiring *ownership* rather
      than `GRANT ALL`.
      **⚑ Decided 2026-09-20: the tables are the source of truth and AGE is a
      projection.** The question was whether AGE holds nodes and edges or
      mirrors the `entities` and `edges` tables that already exist. The
      operator's requirement settled it — the MCP surface has to answer "what
      do we know about X" with citations an assistant can follow, and those
      come from `chunks`, `entities` and `edges`, not from a traversal. So
      writes go to the tables and a pass mirrors them into AGE for the
      questions the tables are bad at: paths, neighbourhoods, contested
      subgraphs. Because the graph is derived it can be dropped and rebuilt;
      under either of the other two readings a disagreement between the two
      stores has no cheap resolution, and the MCP surface is exactly where it
      would surface — as a fact citing a chunk the graph does not have.
      **Outstanding**: the projection pass itself. Nothing writes to the graph
      yet, and nothing needs to until a run produces an edge (`P4-16`)
- [~] `P4-02` Entity resolution: normalise → block → score → three-band
      decision — `v0.92.0`. §5.5's four steps as four functions, each testable
      alone. **Decides and does not act**: `merge` is `P4-03`, because §16 calls
      bad merges harder to detect than duplicates and a change to a threshold
      should not be a change to a function that rewrites rows. **Never across
      node types**, enforced in `block` rather than left to callers — an
      organisation and a place sharing a name are two things and no score
      should overturn that. **Context is weighted heaviest** because §5.5 says
      so: "Cambridge" the city and "Cambridge" the university share every
      character and no neighbours, and before edges exist the neighbourhood is
      the set of chunks each was drawn from. **Absent signals are dropped and
      the weights renormalised**, not counted as zero, or a corpus that has not
      finished embedding could resolve nothing. Token-set plus `difflib`
      rather than a new dependency: both are hard to get subtly wrong, and a
      subtle bug here is a silent bad merge. **Outstanding**: nothing calls it
      yet. `P4-04`'s write path exists now, but `add_edge` takes node ids — the
      caller that has to resolve a *mention* to an entity first is `P4-16`'s
      extract stage. The middle band's queue lands as a `merge_adjudication`
      notification once there is a run to raise it in
- [x] `P4-03` Merge reversibility: redirects, `merged_from`, merge log —
      `v0.93.0`. §5.5: "bad merges are worse than duplicates because
      conflation is invisible once done", and that sentence shapes all of it.
      The source is **kept as a redirect, never deleted** — deleting it breaks
      every citation that already named it. Everything pointing at it moves:
      edges both ways, attribute values, observations both ways, aliases and
      supporting chunks. **The log records which rows moved, not just that a
      merge happened**: `merged_from` cannot say which edges came with an
      entity, so reversing one of two merges into the same target would take
      the other's rows. Each merge stores the ids it reassigned and a reversal
      moves exactly those back, with a test that merges twice and reverses the
      second. Four refusals no score may overturn — self, across node types,
      from a redirect and into one. The log row survives reversal and is
      stamped: "merged then reversed" is the signal a threshold is wrong, which
      is what `P7-10` samples for
- [x] `P4-18` **A relay agent: an attended model instead of an API key** —
      `v0.113.0`. Provider `relay` writes each prompt to `MERIDIAN_RELAY_DIR`
      as `<key>.prompt.json` and defers the run until `<key>.answer.txt`
      exists; the answer then goes through the same parser, guards and write
      tools as any model's, stamped with the registry row's model. The key is
      a digest of the question, because a deferred batch is asked again from
      an unmoved mark — **with the fence nonce masked**, which the end-to-end
      test found: every framing draws a fresh random delimiter, so a raw
      digest never matched and every run would have deferred for ever. Chosen
      over putting write tools on the MCP port, which `P4-04` deliberately
      keeps read-only; here nothing new is network-reachable, and supplying an
      answer takes the operator's Docker access. `claude-code-session` ships
      disabled. **First live batch**: chunks 1–40, 20 edges and 2 tags, and
      the re-run it forced found `B-35`
- [x] `P4-04` Write tools: `add_edge`, `tag_entity`, `enqueue_seed`,
      `advance_mark` — `v0.101.0`. §11.6's "narrow, validated, orchestrator
      scope only", where each word was decided elsewhere: narrow is these four
      functions, validated is `P4-05` built first so these are callers rather
      than authors of the rules, and scope is `grants.py` — `PROFILE_TOOLS`
      gives no shared profile a write tool, `operator` included, so none of
      this is reachable over the MCP surface an external agent holds a grant
      for. **The same claim twice is corroboration, not a second edge**: same
      subject, relation and object is one relation with two citations, and
      duplicating would make every edge count — contested pairs, coverage, the
      digest — a count of extraction passes rather than of knowledge. **A
      cheaper model never overwrites a better one** (§11.12), which matters
      because of the schedule rather than in principle: nightly tier-2 tagging
      runs far more often than the frontier sessions producing tier-4 edges.
      A better model replaces the judgement and keeps every citation — evidence
      is never discarded. **A refusal writes nothing**, the queue included, or
      a rejected seed sits in `queue` being retried with backoff and the
      rejection has scheduled what it rejected. Attributes are not created by
      tagging (§7.3's cap, `P7-01`'s gate), and every tool counts what it did
      on the run so §11.9's week-on-week comparison means something
- [x] `P4-15` **The provider client** — `v0.102.0`. `P4-07` said which agent,
      `P4-04` said where the answer goes, and nothing called the model in
      between. **Behind `meridian-core[agent]`, an optional extra**, because
      `meridian-worker` says of itself "never calls an LLM (§2.1, §6.1)" and
      that is an invariant: the fast loop has to keep acquiring while an agent
      is unavailable, which is only true if it cannot depend on one. The worker
      image does not install the extra, so it physically lacks the SDK — the
      rule is mechanical rather than remembered. **The worst case is reserved
      before the call and settled after**: a cap checked afterwards is not a
      cap, and without `settle_tokens` a run would exhaust its allowance on
      answers it never gave. A failed call releases what it reserved, or a
      flapping agent eats the allowance the working one needs. **The chain is
      walked, not retried** — a provider that is down stays down for the
      seconds a retry would take, and only when every agent has refused does
      the caller get something worth deferring on (§13.4). The key is read at
      call time from the variable the row *names* (§11.11); a missing one takes
      that agent out rather than the run. Two call shapes, because §11.7's
      local tier speaks an OpenAI-compatible protocol and Anthropic does not —
      no shim.
      **Found on the way in**: the seeded registry carried a placeholder model
      string and no `api_key_env_var`. The first returns a vendor error hours
      into a run; the second is quieter, because the SDK falls back to its own
      default variable and the deployment works by coincidence wherever that is
      set. Fixed in the YAML and, per the `P4-07` lesson, in a migration
- [x] `P4-16` **The extraction and tagging stages' own middle** — `v0.103.0`.
      `meridian_core/proposals.py` holds the prompt each stage sends and the
      parse that turns an answer into tool arguments; `mentions.py` is the step
      between a name and the `entity_id` `add_edge` demands. **The parse never
      raises**, which was the load-bearing half: everything unusable comes back
      as a rejection with a reason, so one bad response costs a batch rather
      than a run. A truncated answer — the commonest malformation, and
      malformed only at its end — keeps the items that completed, and says it
      was truncated. **The model cites passage numbers, never chunk ids**: an
      id it supplied is an id it could invent, and an invented one that exists
      would attach a fabricated claim to a real chunk. **A dry run calls no
      model**, because it would spend real money and then roll back the record
      of having spent it. Two things were found on the way in. §11.9's "most
      novel first" cannot coexist with a high-water mark, which describes a
      prefix rather than a set — novelty is a filter here, not an ordering. And
      scoring a fresh mention with the batch's chunks as its context counted a
      non-overlap as *disagreement*, sinking an exact name match to 0.43 and
      founding a new node for every passage; §5.5 says an absent signal is
      dropped, and a mention seen once has no neighbourhood to compare
- [x] `P4-17` **The orchestrator has a service and a schedule** — `v0.106.0`.
      `services/orchestrator/Dockerfile` is the image that may call a model:
      the same package as the worker, built `--extra agent` and *not*
      `--extra embed`, entry point `worker.orchestrate --daemon`. **A timetable
      row was impossible rather than merely worse** — the scheduler spawns jobs
      as subprocesses of its own container, which is the worker image, built
      without the SDK so §2.1 is mechanical; a synthesis row would have run in
      the one image that cannot do it. So the daemon carries §6.3's schedule
      itself: a run at startup, daily after that, early when the backlog past
      the mark crosses `--early-at`. Drift tests read the two Dockerfiles
      against each other, because one `--extra agent` in the wrong file either
      ends the invariant or defers every run with a provider error nobody can
      explain. Verified in containers: the worker image raises
      `ModuleNotFoundError: anthropic`; the orchestrator image walked a whole
      cycle over the real corpus. **What is left before an edge exists is a
      key** — no code
- [x] `P4-05` `validation.py` — server-side guards, node existence, domain
      allowlist, caps — `v0.76.0`. Built before the write tools that call it
      (`P4-04`), because §11.8 specifies the rules precisely enough for the test
      to be a transcription. A module rather than checks inside a tool: §11.1b
      has three callers reaching the same writes and says none gets privileged
      access, so a guard inside one path is a guard the other two lack. Every
      function raises rather than returning a boolean — the failure mode here is
      a guard that never ran, which looks exactly like one that passed.
      **`cap=None` refuses**, because "nobody configured a cap" must never read
      as unlimited (§16). Seed reservation locks the run row, since two calls
      reading `seeds_emitted` at 9 against a cap of 10 would both pass. Seeds are
      refused at seed time as well as fetch time, or a rejected injection sits in
      `queue` being retried with backoff. Hostnames are deliberately **not**
      resolved here — a second DNS answer can disagree with the one `P1-24`
      pinned at fetch time — though literal private addresses are refused
- [x] `P4-12` Allowlist growth: `fetch_policy.seed_allowed` + `first_seen_via`
      — `v0.84.0`. A third question beside `status` (may we fetch what is
      queued) and `trust_state` (may a model read what came back): may new URLs
      on this domain be *queued at all*. **NULL is undecided, and undecided is
      not permission** — a boolean defaulting to false would have said the same
      thing worse, since "declined" and "not yet considered" need different
      screens and different messages. A frontier-discovered domain approves
      itself after three **novel** documents (novel, not fetches: a site
      serving one page under a thousand URLs would approve itself on volume);
      an operator's own seed is allowed immediately, because typing a URL is
      consent. **A model-proposed domain never approves itself, however much
      evidence accrues** — evidence gathered after the proposal is evidence the
      proposal caused, which is the exact shape of a model talking the crawl
      into a domain. It queues for a person like a harvested gazetteer term.
      `first_seen_via` is never overwritten, and discovery is recorded inside
      `enqueue` rather than at its four call sites, so a fifth call site cannot
      leave a domain with no provenance and therefore no path to approval
- [x] `P4-13` Refuse to start a synthesis run with no budget configured —
      `v0.81.1`. §16 states the ordering — caps before the first autonomous run
      — as a mitigation, and a mitigation nothing enforces is a sentence.
      `check_can_start_run` refuses three ways, in the order somebody would fix
      them: no budget row at all, a budget with caps missing, and a month
      already at its ceiling. It returns the budget it approved so the run
      enforces the same numbers it was checked against, rather than re-reading
      caps that may have moved in between. **A missing cap refuses even though
      the others are set**, because a run capped on seeds and uncapped on
      tokens is an uncapped run. The ceiling is checked *before* a run rather
      than during it: a run cannot know what it will spend, and killing one
      halfway leaves a half-written graph — so the month's next run is the one
      refused, which is why §11.9 also asks for trend alerting
- [x] `P4-06` Untrusted-data framing for all retrieved content in prompts —
      `v0.94.0`. §11.8 mitigation 1, wired into the surface it is about: the
      MCP tool returns arbitrary crawled text to a model holding tools, and now
      returns a `framed` block beside the structured hits. **The delimiter is
      random per call**, so a page cannot contain it — a fixed marker is one a
      document can simply include, closing the fence early and putting the rest
      of its text back in instruction position. Per call rather than per
      process, since a leaked one would work for every later call in that
      worker's life. **Nothing is stripped**: `P1-23` established that an
      article *about* injection quotes the phrases, so rewriting documents
      would break the corpus's ability to answer questions about them. The
      instruction precedes the payload, attribution travels inside the fence so
      a model reads the citation rather than reconstructing it, and an empty
      result says so rather than presenting an empty fence. **This is not the
      control** and the module says so — §11.8 is explicit that server-side
      validation is load-bearing (`P4-05`) and this is defence in depth
- [~] `P4-14` **Quarantine and screening for unknown domains** — the
      mechanical half, `v0.82.0`. `P1-23` built the pre-screen and deliberately
      blocked nothing; this is what acts on a flag. `sources.trust_state` and a
      domain verdict cached on `fetch_policy`, so **screening is paid once per
      domain** — a site with four thousand pages is not judged four thousand
      times, and a domain cleared on Monday does not have page 3,001
      quarantined on Friday for quoting something. A domain clears on sight if
      it is in the curated tier map (somebody's judgement, already made) or
      after five consecutive unflagged fetches, and the streak resets on any
      flag. **The filter is `IN (cleared)`, not `!= quarantined`**: a page
      nothing has examined is not a page that has been checked. Quarantined
      content stays stored, extracted and chunked (§2.5) and stays visible in
      the operator's own search — that is how a false positive gets noticed —
      while the MCP surface sets `cleared_only` and cannot be asked not to.
      **Outstanding**: the pass that hands a quarantined domain to a model to
      judge. `P4-07`'s routing and `P4-15`'s client are both built, so what is
      missing is the pass itself rather than anything to call — it wants a
      `triage` task type, which the registry already declares. Until then a
      quarantine is lifted by a person, which is the correct failure: the
      alternative is admitting unscreened content because nothing was available
      to screen it
- [x] `P4-07` Agent registry, task-type routing, fallback chains — `v0.97.0`.
      The registry has existed since `P0-07` and nothing read it; this reads
      it. **Nothing here calls a model** — routing answers "who", the caller
      asks, and keeping them apart makes every rule testable against rows.
      **The strongest agent is not the right agent**: §11.3 assigns each task a
      profile, and `TARGET_TIER` encodes it, so attribute tagging goes to the
      mid tier rather than spending frontier money on schema-constrained work
      that would look fine either way. Ties go to the stronger agent, because
      overshooting costs money and undershooting costs quality. **The stated
      fallback is followed, then everything else that could do the task** — the
      seeded chain points the mid tier at a model that does not declare
      attribute tagging, so following `fallback_agent_id` alone would leave
      that task with no fallback at all. A cycle ends the chain rather than
      hanging the run. Disabled is never routed and an empty `task_types`
      declares nothing, and the refusal separates "not seeded" from "never
      filled in" from "nothing declares this task".
      **Found and fixed on the way in**: the seeded registry named a task type
      nothing routes (`tagging` for `tag_attributes`), which is invisible by
      construction — `text[]` accepts anything and the symptom is an agent that
      is never chosen. The seed is insert-only, so the YAML fix does not reach
      an already-seeded database and a migration does
- [x] `P4-08` Orchestrator run state machine + `runs` table resumability —
      `v0.98.0`. §11.10's rejection of workflow frameworks, as a module that
      moves one row. **Nothing here does the work**: which chunks a stage
      reads and which model it asks are the stages' own business, which is
      what lets every resumability rule be tested without a model, a corpus or
      a stage that exists yet. **At most one unfinished run, enforced by two
      unique partial indexes** — two orchestrators means double spend and two
      sets of writes racing one high-water mark, and the application check that
      precedes them is a race the index closes. **A heartbeat**, because a
      crashed run and a live one are both `status='running'` with a stage and
      nothing else separates them; NULL reads as stale, which is right for a
      run that died before its first step and for every row written before the
      column existed. **Deferred is not failed** (§13.4): the stage is kept and
      the next cycle continues from it, so a provider outage costs synthesis
      rather than the crawl. Stages move forward only and the mark refuses to
      go backwards — the stage is a claim about what has already committed, and
      redone work produces duplicates nothing can tell from the originals.
      Counters accumulate, for the reason `budget.py` gives
- [x] `P4-09` `--once` and `--dry-run` modes (print tool calls, apply nothing)
      — `v0.99.0`. `python -m worker.orchestrate`, the thing that calls
      `P4-08`'s state machine. **`--dry-run` is a rolled-back transaction, not
      a promise**: a mode each stage had to remember to honour is one a stage
      will forget, and the way that is found out is by a dry run leaving
      something behind — so the guarantee holds for a stage whose author never
      read the module. **A cycle that changes nothing stops the loop**; the
      exit condition is progress, not emptiness, or an orchestrator with
      unbuilt stages finds work, fails to advance the mark, and spins while
      looking busy. `--stop-after` leaves the run unfinished deliberately so it
      stays resumable, which is what makes stepping through one possible. A
      live run is reported rather than fought over (§13.4 skips the cycle).
      **Stages are named, not absent** — each says which task builds it, since
      "there is no tagging stage" and "the tagging stage did nothing" are
      indistinguishable in a log.
      **In `worker/` rather than `services/orchestrator/`**: that service is
      compose-gated behind `phase4` because its Dockerfile does not exist, and
      creating it now means a Dockerfile, a compose entry, a release line and a
      healthcheck for a process with no stages. Moving the module later is a
      rename. No timetable row either — a scheduled job that does nothing is
      the shape `B-15` found five of
- [x] `P4-10` `budget.py` — per-run token and seed caps, cost logging, monthly
      ceiling — `v0.81.0`. `budget_config` is a **single row the database
      enforces**, because a settings table that can hold two eventually does and
      then "the budget" is whichever one the query ordered first. Every cap is
      nullable and **null means unconfigured, not unlimited** — the same
      position `reserve_seeds` already took, now with somewhere for the caps to
      come from. `reserve_tokens` mirrors it: all-or-nothing, `FOR UPDATE` so
      two concurrent tool calls cannot both fit under one cap, and it returns
      the remainder so a caller can stop before it is refused. Cost accumulates
      rather than being assigned, or the per-run figure §11.9 compares week on
      week would mean "the last call" in some runs and "all of them" in others.
      The ceiling is the **calendar month in UTC**, measured on `started_at` so
      a run spanning the 1st is not invisible while it keeps spending. Admin
      gets `GET`/`PUT /api/admin/budget`, since a refusal that points at a
      screen needs the screen to exist
- [x] `P4-11` High-water mark advances only after writes commit — `v0.100.0`.
      §6.3's rule, enforced rather than documented. **Marking first and writing
      second loses those chunks permanently**: the mark says they were handled,
      so nothing is missing from the corpus — only from the reasoning over it,
      and that is a gap nothing reports. `advancing()` is the window a stage
      runs inside; writes happen, `Progress.reached` records how far they got,
      and the mark moves once on the way out. A stage that raises marks
      nothing and the caller's rollback takes its writes with it.
      **`mark()` refuses while unflushed changes sit in the session** — that is
      precisely the "marked before writing" state, and it is visible from
      inside, so it is a check rather than a convention. One transaction means
      the writes and the mark land together, which is stronger than §6.3 asks:
      the rule still permits a window where the writes are in and the mark is
      not, survivable because the work is merely redone, and removing it costs
      nothing. `Progress` keeps the highest rather than the latest, or a batch
      processed out of order leaves the mark behind the work it did

## Phase 5 · Autonomy

*Checkpoint: it runs itself, and tells you when it can't.*

- [ ] `P5-01` `frontier.py` — outbound links, citations, spaCy NER, TF-IDF
      co-occurrence. **Two halves are already built and the other two have no
      consumer**, which is why this is still open after a session that went
      looking for buildable work. Links and citations land through `P1-06`'s
      frontier expansion and `_seed_citations`; the `EntityRuler` and the
      acronym harvest are `P5-02`. What is left is entity co-occurrence, and
      its only readers are `P5-03` and `P5-04` — both gated on the graph.
      Writing it now means a pass whose output nothing reads, which is the
      exact shape `B-15` found five instances of: code that runs correctly when
      invoked and is never invoked. Build it with `P5-03`, or with a consumer
      named first. **2026-09-24:** still no consumer. The Map's areas (`P6-30`) and
      neighbourhood (`P6-33`) connect terms by embedding and by cited claims,
      which covers what co-occurrence was for. Consider closing it once those land
- [x] `P5-02` Gazetteer into `EntityRuler` at worker startup; acronym auto-harvest
      — `v0.63.0`. §5.6's "do not hand-write it — bootstrap it", built as two
      pure halves plus a pass. `compile_patterns` turns approved rows into
      patterns; `python -m worker.harvest` reads documents nobody has read yet
      and files each `Full Name Here (ACRONYM)`. Because the ruler *overrides*
      statistical NER, three things do not load: unapproved rows, rows flagged
      ambiguous, and any surface form two rows share — the last one observed at
      compile time, because the flag is hand-maintained and will drift. Short
      all-caps forms match case-sensitively, or a three-letter acronym becomes a
      curated entity on every occurrence of the ordinary English word.
      Corroboration is counted in documents, not occurrences. spaCy is the
      optional `ner` extra rather than a dependency: `P5-01` is the task that
      introduces NER, and the patterns are built in `meridian_core`, which needs
      none of it
- [ ] `P5-03` Coverage scoring, schema-aware, topic × dimension — **re-scoped
      2026-09-24**: per-topic coverage (thin, weak, stale) now exists as a Gaps
      source (`P6-36`), and per-place coverage is coming with `P2-23`. What remains is
      the *dimension* half, coverage per attribute (which attributes have evidence
      for which places), and it needs attribute values, which need synthesis at
      scale. Build it as another Gaps source when there are values to count
- [ ] `P5-04` Gap analysis and seed emission, capped and validated — **re-scoped
      2026-09-24**: largely covered. Gaps (`P6-36`, `P6-37`) is the analysis, seeds
      are emitted capped and never repeated by `seedsearch` (`B-51`, `B-52`, `P5-05`),
      and `P6-38` turns gaps into steering that applies by default. What remains is
      validation: measure whether an emitted seed closed its gap on the next run,
      and down-rank query shapes that never do. That is the re-run loop's job
      (see Resume here)
- [x] `P5-05` Diversity seeding — `v0.139.0` (delegated agent): mechanisms 2 (tier imbalance) and 4 (distant walks) from the graph, 1/3/5 covered by `B-51`/`B-52` at topic level; node-level stance imbalance is a hook waiting for per-source stance. Open: should it read `edges.stance` (filled by one model session) meanwhile; `!science` not checked against the live search instance
- [x] `P5-06` Scheduler reads its timetable from the DB — no cron files,
      `v0.59.0`. `scheduled_jobs` plus `python -m worker.scheduler`, reusing the
      queue's `SKIP LOCKED` + lease so two schedulers cannot both run the same
      backup and a dead one releases by expiry. Missed runs run **once**, not
      caught up — rescheduled from now, or a machine that was off returns to a
      burst. Intervals rather than cron, because §13.2 wants these editable from
      a UI. `python -m <module>` with args as a list and no shell, so a row a UI
      can write is not a remote execution surface. Four jobs seeded; sweep
      without `--apply`
- [x] `P5-07` Telegram digest, alerts on sustained conditions only, inbound
      commands — outbound `v0.58.0`, inbound `v0.96.0`.
      `python -m worker.digest`: §12.5's health line plus four sustained
      conditions, suppressed by a cooldown held in `notifications` (the digest
      exits between runs, so in-memory suppression would forget and re-alert
      every timer tick). Findings are recorded before they are sent, so a failed
      delivery loses the message and not the evidence. Inbound is
      `worker/commands.py` (parse and run) and `worker/bot.py` (the loop),
      behind a `bot` service. **Authorisation happens before parsing** and an
      unknown chat gets silence rather than a refusal — an error message is a
      map of the surface, and an unset chat id refuses everyone rather than
      allowing anyone. The backlog is dropped at startup, because a command is
      an instruction about now and Telegram replays a day of them. The offset
      advances before the work, so one bad message cannot wedge the loop.
      Commands needing phase 4 refuse **by name**; nothing reachable deletes
- [x] `P5-08` Health endpoint, watchdog, off-device snapshot job — `v0.62.0`.
      `/health` shipped with `P2-07`; this adds the two that were missing. A
      liveness heartbeat the worker touches each iteration **before** the work,
      so a lane wedged inside a fetch stops beating — `restart: unless-stopped`
      only ever covered a worker that *exits*, and a wedged one looks exactly
      like a busy one. And systemd units for the off-device backup, which is a
      timer rather than a `scheduled_jobs` row because `backup.sh` needs the
      Docker socket, and giving that to the container that fetches hostile pages
      is not a trade worth making. §13.4's remaining item — "no successful
      synthesis run in N days" — waits for runs to exist (phase 4);
      `check_no_recent_success` already covers the fetch half
- [x] `P5-09` Fix: the alert suppression test expired on a calendar date —
      `v0.75.1`. `record_alert` takes `created_at` from the database clock while
      the file's `NOW` is a fixed instant, so "48 hours after the alert" meant
      48 hours after a date that kept receding. It went red five days after it
      was written, with nothing changed. Suppression tests now set the row's age
      explicitly, the way `attempts()` always set `attempted_at`, and the
      cooldown's *holding* half is asserted too — the expiry assertion alone
      passes against a function that never suppresses anything

## Phase 6 · Interface — the payoff layer

### The connections work (agreed 2026-09-23 — see "Resume here")

- [x] `P2-21` **Topics come from what a page says, not why it was crawled** —
      `v0.118.0`. `sources.crawled_for` holds provenance; `topic_labels` is written
      only by `worker.retopic` (hourly, `--apply`) from the source's mean chunk
      vector against each topic's prototype (name, description, approved
      vocabulary), centred on a fixed reference; multi-label, `{}` = examined and
      off-topic. Descriptions editable in Admin → Topics. Calibration (silver-set
      AUC 0.985, ~85% precision on a random sample) is in the commit body of
      `9c574d8` and `~/Documents/gh/meridian-calibration/p2-21/`. **⚑ human, still
      open**: write topic descriptions (they raise recall from 0.70 to 0.82), and
      decide whether to run `--demote-offtopic` (floor 0.30 or 0.25)
- [x] `B-43` **Headers, footers, banners and menus inside extracted pages** —
      `v0.119.0`. Cleaners (navigation affordances, menu runs, PDF running heads,
      per-host repeated lines, extraction debris) wired into the fetch path through
      `worker.cleancut`; `page_lines`/`boilerplate_lines` with a daily
      `worker.boilerplate` pass; `worker.rechunk` for stored sources. Report on the
      live corpus: 1,305 of 3,873 sources change, 34 cited ones left alone, 31 kept
      whole by the guard, 4.8% of readable characters removed. It also found `B-47`
- [x] `B-46` **The superseded-chunk sweep ignores entity citations** — `v0.118.1`;
      the citing tables are derived from the models, one `cited_source_ids` for all sweeps —
      `chunks._UNCITED` checks edges, observations and attribute values but not
      `entities.supporting_chunk_ids`, so `sweep --apply` could delete a
      superseded chunk that only an entity cites. Must be fixed before `B-43`'s
      re-chunk pass runs, since that pass supersedes chunks
- [x] `B-47` **PDFs with a garbled text layer were chunked as noise** — `v0.119.1`.
      Custom font encodings extract to control characters, which pass the
      scanned-page check. Garbled pages are blanked, and mostly garbled documents
      go to OCR, both at fetch and via `worker.rechunk`. Found by B-43's debris
      rule: six sources, 693 chunks. Follow-up done in `v0.125.2`: a re-fetch
      that turns a document into a scan retires its previous chunks
- [x] `B-49` **URLs were embedded as if they were meaning** — `v0.120.0`. One
      character in eight of chunk text sat inside a markdown link target, and one
      chunk in ten was over 30% URL, so pages clustered by link shape. The embedder
      now gets a view with link syntax reduced to its visible text; stored text is
      untouched. `worker.reembed` brings existing vectors up to the view in place
- [x] `B-48` **The frontier followed links into whatever big site a seed touched** —
      `v0.121.0`. 98% of fetched pages came from link-following, each link inheriting
      its parent's topic and ranked by domain tier; by content (`P2-21`) 94% of the
      corpus was about none of its topics. Hosts are now scored from content labels
      (`worker.hostscore`, hourly); off-topic hosts are not followed into or out of,
      unjudged ones are explored up to a cap, every host is capped. `worker.requeue`
      applies it to the existing queue
- [x] `B-51` **The crawl ran five queries and then only followed links** — `v0.122.0`.
      Nothing generated search queries after cold start, so 98% of fetches came from
      link-following. `worker.seedsearch` (6-hourly) queues fresh per-topic queries,
      including news and §7.4 counter-seeds; results outrank same-tier links; Bing web
      search disabled (returned unrelated pages)
- [x] `B-52` **Non-English seeds (§7.4 mechanism 5)** — `v0.123.0`. A `:lang` prefix
      on English words returns English pages (measured), so the words come from
      Wikipedia interlanguage titles (`worker.translate`, daily) and `seedsearch`
      writes queries in them. Not yet measured: how the content labeller scores
      non-English pages against English topic prototypes — bge-m3 is cross-lingual,
      but a lower score would read as off-topic and feed the host gate
- [x] `B-50` **An academic domain is not peer review** — `v0.122.1`. `*.edu`-style
      suffixes (listed in the tier map's `needs_scholarly_evidence`) make a page
      `peer_reviewed` only with its own DOI, else `institutional`; links rank
      accordingly; `worker.retier` fixes stored sources
- [x] `B-44` **Duplicate documents** — `v0.124.0`; `worker.docdupes` (exact passage overlap, or same title + cosine ≥ 0.985 + comparable length; mean-vector cosine alone was measured to merge distinct same-template documents). Not yet scheduled or applied live — 129 near-identical source pairs (mean-
      embedding cosine ≥ 0.96), 81 of them only partly caught by the chunk-level
      novelty gate: a PDF and its HTML page, listing pages under query-string
      variants, `www.`/bare-host twins. Mark the later source as a duplicate of the
      earlier at document level so it is neither searched nor synthesised twice
- [x] `B-45` **Soft-404 pages crawled as content** — `v0.123.1`; title-segment and opening-text rules, junk at fetch, `worker.furniture` for stored ones — pages titled "Page not found"
      served with 200 and chunked. Detect at fetch (title/body shape) and demote
      what is stored
- [x] `B-57` **English version preferred where one exists** — `v0.125.0`. The operator's
      rule (2026-09-24): an English version if the site offers one, the original
      language otherwise. Declared `hreflang="en"` alternates are fetched first and
      the original becomes a `translation` copy via `worker.docdupes`
- [x] `B-58` **DOI resolution has never run** — `v0.140.0` (delegated agent). Live report (read-only replay): about 1,400 of ~5,000 distinct backlog DOIs would rise; the rest were cited by off-topic or about-nothing pages. Run `worker.requeue_dois` report, then `--apply`, then enable its timetable row. Open: resolved copies bypass the host gate; duplicate DOI rows are left in place — every queued `doi` task sits at the
      lowest priority below all links and search results, so none has ever been
      claimed (found 2026-09-24). Raising them wholesale would re-import the drift:
      many were cited by off-topic pages before the host gate, and the queue does not
      record which page cited each DOI. Record the citing source on new `doi` rows,
      rank each by its citing page's topic labels and host score, and requeue the
      backlog by the same rule. Unpaywall is not the bottleneck: without a contact
      email the resolver still uses OpenAlex (which carries most of Unpaywall's
      open-access data), Europe PMC, Semantic Scholar and the preprint rule
- [x] `P6-37` **Gaps per query, and runs on a deployment** — `v0.138.0` (delegated agent). Open: per-query off-topic needs a result→query link on the queue; old failed queries have no "answered at" and stay listed; run files written by the tools container are root-owned — Gaps' search-yield
      source counts per topic; `B-56` now records each query's yield, so a query
      that found nothing can be its own gap. Also `eval/runs` is not mounted into the
      API container, so on a deployment the question-set source reports
      "unavailable" until `MERIDIAN_EVAL_RUNS_DIR` points at a mounted directory
- [x] `P2-24` **Topics per passage** — `v0.130.0` (delegated agent). Calibrated read-only:
      precision on labels a passage adds beyond its source's is about half, so they
      widen search but do not lift a topic out of "thin" in Gaps. Topic descriptions
      would help more than thresholds. Open: should graph filters and the question
      set's topic match read passage labels too (it would move the eval baseline)
- [x] `B-59` **What kind of document a source is; listings as hubs** — `v0.131.0`
      (delegated agent). The backfill's report on the live corpus would supersede
      about a third of live passages (those of listing pages): read it before
      `--apply`. Untested on live: the news and citation-meta rules (the raw files
      were unreadable from the agent's account)
- [x] `P2-23` **Which places a source is about** — `v0.132.0` (delegated agent).
      Calibrated read-only: most tags right on a hand-read sample; the main error is
      a publisher's own country on its listing pages (`B-59` removes most of those
      from the corpus). Open: saved views look like they cannot save a topic filter
      (the web sends `topic`, the server expects `topics`) — read from code, not
      reproduced; comparison *cities* are not listed explicitly, so Gaps reports per
      country; the MCP search tool does not take `places` yet
- [x] `P6-38` **Steering proposals that apply by default** — `v0.133.0` (delegated agent). Open: the "hand-steered in the last day" rule counts any steering-log row, so one manual change quiets proposals for a day (conservative on purpose); the window is set in SQL, not Admin; no Telegram push — the operator's request
      (2026-09-24): the system proposes what to steer, and if the operator does not
      object within a window (default 12h, in the global policy row) it is applied,
      bounded, expiring and logged. Being built by a delegated agent
- [x] `B-60` **No database guard against a duplicate edge** — `v0.152.3`: unique on the claim, *deferred to commit* (see below) — `add_edge` combines a
      repeated claim, but two concurrent calls could still insert two rows: there is
      no unique index on (from, relation, to). Add it after `worker.edgedupes --apply`
      has folded the live graph's existing duplicate (found by the `B-41` agent)
      **First try 2026-09-27, backed out:** an immediate unique constraint on (from_node, relation_type,
      to_node) broke 24 tests — a merge repoints the merged entity's edges one UPDATE at a
      time, and the moment two rows hold the same claim (before folding) the constraint
      refuses it; reversing a merge re-creates rows the same way. The merge must fold
      duplicates *before* repointing, and the reversal restore in an order that never
      duplicates, before the constraint can go on. The live graph has no duplicates today.
      **Shipped deferred instead:** checked at commit, after the merge has folded; `add_edge`
      makes it immediate around its own insert to catch a lost race
- [x] `P6-39` **The Map's route mode** — `v0.152.0`. "Route from here…" on a field, then a
      click on another: the shortest chain of the lines on screen, fewest similar-only hops
      among equals, drawn over the map with both ends ringed, and a panel of hops (cited or
      similar only, each opening its bridge). No chain is said as a finding. Among the areas
      drawn, not through concept nodes (`route()`'s `HopSource` stays the way to join the two)
- [x] `P6-41` **An answer page for a question** — `v0.145.0`, screenshot-checked on the live
      corpus 2026-09-26 (and `B-80`). UX priority 1 (agreed 2026-09-25). People come
      with a question, not to browse clusters: evidence grouped by country (places are
      already tagged), coverage shown honestly (strong / thin / none), trust markers on every
      item (government, peer-reviewed, date, contested), and "Find more" on a thin country
      queuing a search. No model; the synthesis panel is a separate decision
- [x] `P6-42` **The Map as a tool, not a picture** — UX priority 2. Done: semantic zoom
      (`v0.146.0`), fields shaded and labelled by on-topic share with a per-topic breakdown
      (`v0.147.0`), on-topic fields with thin evidence listed in Gaps with a link back to the
      Map (`v0.148.0`, `v0.148.1`), the dead "Route from here…" item removed; "This is noise"
      on a field (`v0.150.0`): its sources read whole and about none of the topics go to junk,
      undoable by mark; fill by research share as well as topic share (`v0.152.5`). Freshness
      was measured and not built: every field's newest source was from the same week
- [x] `P6-43` **Watched questions** — `v0.151.0`. UX priority 3. Each saved view on the
      landing shows how many sources that answer it arrived since it was last opened
      ("7 new", "Nothing new"), counted by its words (as search's lexical arm matches them)
      and its topic filter, junk and duplicates left out, capped at 200. The bell's generic
      notifications stay; they are about the system, these are about your questions
- [~] `P6-44` **Plain language in reader views** — UX priority 4. Done 2026-09-26: the
      landing's counts and cards, Coverage opening Gaps, Find's summary line, concept pages
      offering only built views (`v0.148.2`–`v0.148.5`); the concept page's own labels and the
      Map's topic names (`v0.149.10`). Left, the operator's call: separating reader from
      operator navigation — the mocks put Admin in the top bar beside Explore, Map and Gaps. Reader screens say sources,
      government, peer-reviewed, fields; nodes, edges, passages, tiers and basis stay in Admin.
      Reader and operator navigation separated
- [ ] `P0-18` ⚑ **Task-based check with the operator's own questions** — for five of Q31–Q41,
      time to an answer the operator would stand behind, and dead ends met. Before and after
      each of P6-41..44
- [x] `P6-40` **The About page** — `v0.141.0`, reached from Settings. The design canvas has an About board (tagline, what the
      build holds: topics, sources, version) and there is no route or task for it. Found by
      the 2026-09-25 audit of the build against the mocks, which also lists as unbuilt: the
      synthesis panel (`P6-06`/`P6-07`), reports (`P7-09`), Explore's Matrix, Timeline and
      Coverage views (only node-link and table render), the landing Coverage card (`P6-10`,
      needs `P5-03`), Admin's enrichment queue (`P7-07`), notifications' "Mark all read" and
      digest settings, and the Map's route mode (`P6-39`)
- [x] `B-61` **The crawl outran search, labelling and embedding** — `v0.140.1`. Found by
      the first 12h run: followed links outnumbered search results by orders of
      magnitude, unjudged institutional hosts were explored 50 links each, and the
      embedding backlog grew several-fold. Directed claim share, smaller exploration
      budget, and embedding backpressure
- [x] `B-63` **Synthesis reasoned over whatever came next, on-topic or not** — `v0.140.2`,
      a deployment setting, on locally
- [x] `B-64` **Steering proposals cut the productive topics** — `v0.140.4`. Cuts now go
      to topics taking at least their share of fetches at under half the crawl's
      average yield per fetch, never to thin ones; boosts only where more crawl would
      help (under-drawn, or yielding)
- [x] `B-65` — `v0.154.1`, a RUM index ranks the best 1000 matches, `ts_rank_cd` orders those. **Measured live (2026-09-29, default filters):** broad single words 2–3× faster; top 100 identical for most questions and within one or two rows for common two-word ones; narrow questions a little *slower*, the price of the second step. Open: the planner now answers plain `@@` from RUM too, so the GIN index (≈330 MB, cheap inserts) may be redundant — drop it only after checking watches and area views still plan well. **The lexical arm is the slow half of search** — measured at corpus size
      it is roughly ten times slower than the vector arm and sets hybrid latency.
      **Diagnosed 2026-09-25:** the GIN index is used; cost is linear in the number
      of *matching* passages (about 4 µs each), almost all of it reading each match's
      `search_vector` out of the heap and TOAST to rank it. Multi-word questions are
      ANDed and match few passages (2–5 ms); a single common word matching tens of
      thousands costs 150–200 ms, and that is what the benchmark's probes are. It will
      grow with the corpus. Options, not yet chosen: a RUM index (ranks inside the
      index; an extension to add to the Postgres image), or ranking only the first N
      matches of a very broad query and letting the vector arm carry it. Low urgency
      while real questions are multi-word
      **Measured 2026-09-27, cap rejected:** ranking only the first N matches (N = 10k)
      cut a broad word from 80–370 ms to about 55 ms, but the lexical top 50 kept only
      14 ("transport"), 19 ("health") and 20 ("city") of 50; at N = 20k, 100–140 ms and
      21–24 of 50. And a common two-word question ("public transport", 14k matches)
      exceeds either cap. Faster and noticeably different is the wrong trade here; the
      RUM index (ranking inside the index) is the option left
- [x] `B-83` ⚑ **Re-calibrate the topic label floor on today's corpus** — `v0.149.5`, floor 0.50 (operator: "ok you can try", 2026-09-27; judgement in `meridian-calibration/b83/`). — found by the
      2026-09-26 information audit. Over half of all labelled sources (about 1,300 of 2,500)
      sit in the 0.45–0.48 band just above `LABEL_FLOOR`, and judged by title only about a
      third to two fifths of a sample of 25 there were on their topic: generic government
      landing and event pages, speeches)
      and off-subject pages that share vocabulary. At 0.55 and above 15 of 15 were right.
      The floor was calibrated on a silver set with no generic government pages. Tried and
      rejected: a flatness rule (best score minus the median of the others) does not
      separate right from wrong in that band. Options, the operator's trade-off (recall for
      precision, and every filter, Gaps count and Map shade moves with it): raise the floor
      to about 0.48–0.50; add generic-page negatives to the prototypes; or describe the
      noisiest topics. Measure with `worker.retopic`'s report before moving it
- [ ] `B-109` ⚑ **Web search engines refuse this machine** — found 2026-09-27 (run 10): brave,
      google cse "too many requests"; duckduckgo, startpage, qwant CAPTCHA; mojeek "access
      denied". Only news engines answer. `B-107`/`B-108` stop us making it worse; recovery is
      the providers' timetable (SearXNG suspends a CAPTCHA'd engine for a day). Lasting fix is
      the operator's: a search API key (e.g. Brave Search API) as the §6.4 fallback path
- [x] `D-02` **A documentation structure, and a document per feature** — docs only. The
      operator asked that every feature have a document and that documentation live in
      `docs/`, not in code. `docs/README.md` maps the folders (Diátaxis: guides, reference,
      explanation, plus ADRs); `docs/features/` holds twenty feature documents distilled from
      the module docstrings and the handover; `docs/reference/` gains environment variables,
      scheduled jobs and commands; the how-to guides moved to `docs/guides/` and the licence
      audit to `docs/reference/`. Writing them found: the MCP grant profiles name three tools
      the server lacks and miss two it has (`B-138`); `search_service.py` still said the vector
      arm was unbuilt, long after `P2-17` built it (fixed under `D-01`); and the `B-127`
      reasoning about the translation rule was wrong (corrected)
- [x] `D-01` **Move narrative comments into the docs** — every feature, the data model, the
      scripts and the web package. Comment and docstring lines in the Python went from 30% to
      21% of the code; rationale, history and measurements now sit in `docs/features/`,
      `docs/reference/data-model.md` (new) and `docs/spec/external-acquisition.md` §3.3, and
      the code points at them. Checked as comments-only: Python syntax trees and minified web
      output compare equal before and after. `tests/unit/test_doc_pointers.py` resolves every
      `docs/…md#anchor` pointer in the code. Found and corrected on the way: more than twenty
      comments that had drifted from the code (wrong counts, the wrong constant, "not built
      yet" for things built, the old single-board-computer target). Left on purpose: MCP tool
      docstrings (assistants read them as instructions), argparse usage text, and short
      caller contracts. New rule from here: when a file is touched, keep it to the standard
- [x] `B-142` **Linting and formatting for the web package** — `v0.162.1`, ADR 0012. oxlint
      rather than ESLint: the package's TypeScript 7 has no compiler API for typescript-eslint.
      Hooks rules, unused vars, no `any`, TSDoc syntax, correctness; Prettier in its own
      formatting commit; both in `make lint`, with a test that each rule still fires. Found:
      9 hook dependency findings (none live; 3 tidied, 6 deliberate and marked), TSDoc broken by reST double backticks and
      by vitest pragmas inside doc blocks. Left off: the React Compiler rules (ADR 0012)
- [x] `B-139` **The site's views as MCP tools** — `v0.160.0`. `find_nodes`, `get_node`,
      `find_route`, `term_neighbourhood`, `list_areas`, `get_area`, `list_gaps`,
      `list_contested`; each calls the page's own function (a test compares the map and
      contested answers), and the instructions gain "cited is not similar" and "the map and
      gaps are measurements". Found: `search_chunks` never embedded the query, so assistants
      got word-matching only while Find was hybrid; fixed. The growth tool comes with `B-140`
- [x] `B-140` **How the corpus grew** — `v0.161.0`, [ADR 0005](docs/adr/0005-growth-page-for-readers.md),
      [ADR 0010](docs/adr/0010-growth-page-placement-and-range.md), mock
      `docs/design/CorpusGrowth.dc.html`. `meridian_core.growth` counts everything by calendar
      day in the display zone, as of one instant: pages per topic (a multi-topic page once),
      gaps for days with no fetch, passages, sources, new sites, the graph, and the map from
      the new `area_build_history` (builds are pruned; the migration seeds it from the builds
      that remain). `GET /api/explore/growth` (kept per window and filter), `corpus_growth`
      (MCP), and `/growth` in its own top-bar section, opening on 30 days, filterable by topic,
      with a table view. Checked against the mock with a built page and canned data
- [x] `B-166` **A GMT+8 zone labelled GMT+7:59** — `v0.164.10`. Seen on Gaps' "Checked" line
      in a screenshot pass. `zoneOffsetMinutes` rounded a seconds-long difference; every test
      instant had been a whole minute. Now held against `Intl`'s `shortOffset` at every second
- [x] `B-165` **Dead code in the title fallback** — `v0.164.9`. An unreachable older copy of
      `title_from_text`'s loop sat after its return; removed. Found checking whether untitled
      sources (9%) could be titled: their openings are mostly forms and fragments, so not
- [x] `B-164` **Link syntax showed where a link was cut** — `v0.164.8`. Seen on a node page
      (2026-10-08): a passage began `(2019)](https://…#bib41) modeled`. Passages are cut at a
      length; about 1% begin inside a link and 0.7% end inside one, and escaped footnote
      markers and wrapped link words were left as stored. `readable()` reads them as words
      when the address plainly is one; 275 → 34 of 20,000 sampled passages still show syntax
- [x] `B-171` **Synthesis read bibliographies** — `v0.165.3`. A relay batch was 24 reference-list
      passages of 40. `pull` now leaves out passages mostly made of entry lines (`references.py`,
      60% of characters, 3 entries) and reads on to fill the batch; the mark moves over what it
      left out. 1% of a 20,000-passage sample qualify, all bibliographies or bare number columns
      in a sample of 25
- [x] `B-170` **The tagging prompt asked for consistent wording without showing it** —
      `v0.165.2`. After three relay batches the graph's 24 attribute values already had
      "road" beside "public road" and four wordings of autonomy. The prompt now lists each
      attribute's wordings in use, most used first (8 at most); a test reads the prompt the
      model received
- [x] `B-169` **A crowded graph label was dropped rather than moved** — `v0.165.1`. Labels
      tried only below their node (`B-124`); now above, right and left too, before leaving
      one out. Seen on a screenshot pass: a fourteen-neighbour node lost one label on desktop
      and about a third on a phone; all drawn after
- [x] `B-168` **The answer page filed passages under countries they never name** — `v0.165.0`.
      Seen 2026-10-08: a question listed a market report and two papers about other places as
      one country's evidence, and called that country strong. Places are per
      document; a passage counted for every one. Measured on 8 questions: of several-country
      placements, 24% of shown passages named the country, 18% more had another matching
      passage that did. Now a several-country source counts only where a matching passage
      names the country, shown by that passage; the rest go unplaced, counted
- [x] `Q-01` **Coverage measured** — branch coverage for Python (`make coverage`) and V8 for
      the web (`npm run coverage`); baseline Python 90.7%, web 78.8% statements. A dry-run test
      for the sweep job, the one path that deletes. See docs/guides/testing.md
- [ ] `Q-02` **Mutation testing** — mutmut and StrykerJS configured, neither trustworthy yet:
      mutmut's tests import the installed package instead of its mutated copy; Stryker's scores
      are implausibly low on well-tested modules. The guide says what to try next
- [ ] `Q-03` **Property-based tests and CI** — Hypothesis installed; fuse, cap_per_source,
      group_hits, is_reference_list and readable() first; then a CI workflow for lint and tests
- [ ] `B-167` **Rerank search with a cross-encoder, on a GPU** — measured 2026-10-08
      ([search.md#reranking](docs/features/search.md)): `bge-reranker-v2-m3` over the fused top
      30 brought answer-stating passages into the top ten on most of 18 test questions, but
      took 20–35 s a question on CPU; small cross-encoders were fast and did not reproduce
      the gain. Build with `B-131`: a `/rerank` route on the embedding sidecar, off unless the
      sidecar is on a GPU, applied to Find, the answer view and `search_chunks`; measure
      latency on the card first
- [ ] `B-163` **Which passages synthesis reads** — measured in the 2026-10-07 relay session
      (3 batches, 120 passages, 34 relations, 5 attribute values). Synthesis reads a passage when
      it *or its source* is labelled on a topic, so one borderline page reaches the model whole:
      arXiv's category taxonomy (labelled at 0.555, 31 of its 37 passages labelled nothing) took
      five passages of a batch. Requiring the passage's own label would cut batches by 60% and
      lose about a third of the claims (10 of the 28 cited passages had no label of their own —
      a study's methods and results inside a labelled paper): roughly 65% more claims per model
      call. Decide with a paid model and more batches (`B-135`); a middle rule (own label, or a
      labelled neighbour in the same source) is worth measuring. 2026-10-08, three more relay
      batches (29 relations, 12 attribute values): the third was 40 consecutive passages of one
      essay, references and footnotes included, for 9 claims. Batches run in chunk order, so a
      long document fills them; a cap on passages per source per batch is a second rule to
      measure beside the label rule
- [x] `B-162` **A resumed synthesis run had no batch** — `v0.164.7`. Found running extraction and
      tagging through the relay (operator's go, 2026-10-07): a run deferred at `extract` or `tag`
      resumed with no batch, finished, and the next run re-asked the model for the stages already
      answered. Re-pulls from the unmoved mark now; a test drives `cycle()` through a deferral at
      each stage and counts the model calls. The session wrote 11 relations and 2 attribute values
      from 40 passages, all cited
- [x] `B-161` **The novelty gate gave up after 20,000 index entries** — `v0.164.6`. Seen as an
      intermittent novelty failure in a leak-check run (fresh database, index full of near-identical
      test rows); boilerplate copies are the production case. Same scan limit as search
- [x] `B-160` **Exploration pages waited out the crawl to be embedded** — `v0.164.5`. After
      run 18, 1,179 window pages on unjudged hosts had no passage embedded: the `then` tier is
      oldest first, behind a `first` tier the crawl refills as fast as it is embedded. Their
      samples are first-tier now, so vouched-for and promising hosts get judged within the hour
- [x] `B-173` **Find's filter rail** — `v0.166.0`. Source type and publication years had no
      control (the API always took them); places and every filter but topics fell out of the link
      and out of saved views; the landing carried rows of chips the design does not. The Explore
      design's left rail on results, one filter model (`lib/find.ts`) for link, request and view
- [x] `B-174` **More passages** — `v0.166.4`. Passages stops at 20 with no way on, though the API pages by
      `offset` up to the candidate pool. A "more" row at the foot of the list
- [x] `B-175` `v0.166.5`. **The answer's "N more sources not shown" is a dead end** — make it open Passages
      narrowed to that country
- [x] `B-176` `v0.166.6`. **The neighbourhood panel on a whole question** — "No concept is called “how do
      cities …?”" and concepts matched on single common words. Quiet unless a concept is named
- [x] `B-177` `v0.166.7`. **Where you were** — recent nodes never listed (`recentNodes={[]}` though
      `graph/recent.ts` records them); a saved node view on the landing is a dead click
- [x] `B-193` `v0.166.8`. **A node view with a filter cannot be saved** — the node workspace stores
      `topic`/`tier`/`published_from`, which the server's `SearchFilters` check refuses (422,
      verified on the live API). Validate a node view against the graph's own filter model
- [x] `B-194` `v0.166.9`. **Watched counts ignore a view's filters** — `watch.py` reads `topic`, but views
      have stored `topics` since `B-73`, so "N new" on a saved search ignores its topics, and
      places, source types and years were never applied. Count with the search's own predicate
- [x] `B-178` `v0.166.10`. **Open a source at the passage** — a hit links to the top of its source, and the
      source page loads 20 passages, so a hit on page 24 is often not on the page at all. Link
      `?chunk=`, load the window around it, mark it; earlier/later at both ends. Same for node
      evidence, the Map's theme card and notes' passage chips
- [x] `B-179` `v0.166.11`. **Source page for citing** — per-passage copy-citation, original URL in a new tab,
      "Explore" breadcrumb returns to the results; rename the passage "cite" checkbox (it selects
      for a note, while "Cite" on a node copies)
- [ ] `B-180` **Manage views and notes** — no rename/delete for views (the API has both), notes
      cannot be edited or removed, the landing shows 5 notes and 6 views with no list of all
- [x] `B-181` **Notification links go to the right place** — `v0.166.2`. approvals, run summaries and
      alerts all open `/admin` (Topic weights); the status pill too. Map type → section
- [ ] `B-182` **Possible-duplicate approvals have nowhere to go** — most of the bell is
      `merge_adjudication`, and nothing can decide one. Until a merge screen exists, "Compare"
      opens the two nodes; a superseded proposal still reads as pending
- [x] `B-183` **Ask panel with no model** — `v0.166.3`. the composer offers "citations checked" and fails only
      after asking, in operator words. Say so first, and point to the Answer tab
- [ ] `B-184` **Gaps: reader gaps first** — search-yield diagnostics fill the first eleven rows;
      the off-topic gap's action contradicts its advice; `!bang` syntax in prefills; the tab is
      lost on Back
- [ ] `B-185` **Admin small fixes** — add-topic dialog has no description (which drives seed
      searches) and no next step; weight drafts dropped silently on section change; crawl health
      has no links and raw enums; runs list has no paging, "1 edges", "done · done"; agent models
      shown as `${…}`; fetch policy "Not being crawled. 0 failures in a row"; nested `<main>`;
      boost undo text names the wrong section; phone tab strip hides 8 of 12 sections
- [ ] `B-186` **Map small fixes** — theme card terms inert (make them Find links); child names
      repeat the parent; hover card over the level controls; the jump box finds no concepts and
      gives no hint that it needs Enter
- [ ] `B-187` **Growth "+N in 30 days" equals the total** on a young corpus — say "all since …"
- [x] `B-188` **`list_new_since` returns quarantined and unscreened passages to assistants** — `v0.166.1`. the
      one MCP passage path without the cleared-only filter search applies (§2.5). Verified on the
      live corpus. Highest priority of this batch
- [ ] `B-189` **MCP citations and arguments** — `page_unit` dropped from citations, `framed` has
      no title or page; a bad date or tier gives "Error executing tool"; `limit` unbounded
      (444 KB at 500); parameters undescribed; empty results without a note
- [ ] `B-190` **Runt passages** — 23.5k live chunks under 40 characters, most embedded; a nonsense
      query's nearest neighbours are "z", "terms". Runts are absorbed per kept stretch, so page
      furniture strands fragments (`chunk.py`). Absorb across, keep runts out of the vector arm
- [ ] `B-191` **Tables cut mid-row and headerless** — 4.6% of chunks sit at the 2,000-character cap
      (80% of spreadsheet chunks); a pipe table is one "paragraph" and is cut mid-number. Split at
      rows, carry the header into the embedding view, back the hard cut off to whitespace
- [ ] `B-192` **Figure captions are not searchable** (suspect value) and PDF tables lose columns
      without `-layout` (suspect); lexical search is English-only for ~30k chunks (suspect)
- [x] `B-172` **Two regions both named "Data"** — `v0.165.5`. After `B-159` more areas take
      their terms, and names from terms were never told apart. Areas listed together whose
      term names coincide now carry their next own term: "Data (health)", "Data (research)"
- [x] `B-159` **Near-tied Map names** — `v0.165.4`, ADR 0018. Judged 80 live areas by hand: ~41% of
      names did not fit; a name now needs 0.33, or 0.25 and a 0.03 lead over the third; on a
      fresh 30, wrong 9 → 3, right 21 → 19. Was: — after `B-157`, an area of statute text still reads "Small
      Animals": its centred similarities to the subfields are nearly tied (0.213 / 0.209 / 0.207 /
      0.194 across Small Animals, Equine, History, Classics), so the winner is noise that clears
      the 0.20 floor. On the local build 48 of 420 named areas have under 0.02 between first and
      third. A plain margin rule would also drop plausible sibling names (Ecology vs Environmental
      Engineering for an energy and climate area), and requiring the top candidates to share a
      field keeps others wrong (Speech and Hearing for job statistics). Needs a rule measured on a
      hand-judged sample of areas, not a threshold guessed from the margins
- [x] `B-158` **A NUL in extracted text lost the page** — `v0.164.4`. Run 18's one worker error:
      a PDF text layer with 0x00, which Postgres refuses in text and JSONB; removed at the write
- [x] `B-155` **Proven by following** — `v0.164.0`, ADR 0016. Run 17 spent ~900 of 2,300
      fetches on five hosts (~4% on a topic) proven by `B-115` on pages search had picked.
      Backtested on runs 15–17; hosts are now proven or thin on their followed record, and
      *promising* until they have one. `requeue` hourly. Found: `requeue` dropped every score
      field but the counts, so vouches were lost on each run since `v0.163.0`; fixed. Left: drift
      within a host (a recency window), if loop runs keep showing it
- [x] `B-157` **Map fields named off-corpus; reported overlap** — `v0.164.3`, ADR 0017. The
      fields do not overlap (measured: every pair keeps its 14 px gap at 1440 and 390 px); links
      drawn across translucent fills read as overlap, left as a design question. The names were
      wrong: the list had no names for public records (legal clusters took "Speech and
      Hearing", "Accounting"), the similarity floor (0.05) accepted almost anything, and a
      plurality of a seventh named a mixed region. Added a "Public Records" group, floor 0.20,
      regions named by a majority or a pair. Live build simulated: legislation and budget
      regions named as such, three regions on plain term names. Applies on the next `areas`
      build or `worker.areas --name-only`
- [x] `B-156` **The site review's reader fixes** — `v0.164.1`. Neighbourhood panel and Answer
      chip in reader words; the source page's and result cards' bookkeeping folded into "record
      details" and tooltips, with a page shown only when it is citable (`SourcePageRead.page_unit`);
      logos and icons left out of figures and file names not shown as captions (rules measured on
      40,000 stored figures); a stalled crawl in the status pill; the Ask button stepping aside on
      a phone's scroll down; the 3D map kept and its vectors decoded in C; the landing counting
      kept documents as Growth does
- [x] `B-154` **Empty pages counted as worker errors** — `v0.163.4`. All 40 errors in run 17
      were trafilatura reporting empty or unparseable pages at ERROR, which the extractor already
      records; it is held at CRITICAL now
- [x] `B-153` **Pages with no declared language** — `v0.163.2`. Run 17: 24% of new pages
      (mostly PDFs) had no language, and unknown is scored as English, so `B-53`'s correction
      missed non-English ones. py3langid reads it from the text (≥ 200 letters, p ≥ 0.9; 98.5%
      agreement with declared languages on 2,000 stored pages); `worker.relanguage` backfills
      and re-labels those found in another language
- [x] `B-151` **The novelty gate missed copies of much-copied passages** — `v0.163.1`. Found
      when a leak-check run failed a novelty test: HNSW offers its nearest candidates and the
      filters (earlier, not a duplicate, not retired) run after, so when the nearest to a new
      copy were all marked copies the gate found nothing and kept it. `vectorindex.
      scan_past_filtered` turns on pgvector 0.8's iterative scan in strict order; a test with
      a hundred nearer copies fails without it
- [x] `B-152` **Filtered searches came back nearly empty** — `v0.164.2`. Measured on the
      live corpus: with one topic as the filter the vector arm returned ~1 of 100 (official
      sources 43, peer-reviewed 27), so topic-filtered Find was lexical-only. Iterative scan
      (relaxed, sorted after) and `hnsw.max_scan_tuples` 100k: 100 of 100, recall 0.87–0.96,
      p50 8 ms unfiltered to ~600 ms for the narrower topic. Neighbourhood too
- [x] `B-150` **Followed links follow the evidence** — `v0.163.0`, ADR 0015. The operator asked
      for discovery that does not wait on one search method (`B-109`). Measured first on the
      live corpus: a link from an on-topic page lands on a topic about half the time (as search
      results did), from a page about nothing about one in ten; an unjudged host linked from an
      on-topic page elsewhere turns out on a topic several times as often as one that is not.
      But re-ranking only moved links down, and the queue kept only a link's first page. Now
      every page's links to other hosts are recorded (`link_vouches`), unjudged hosts with an
      on-topic voucher are explored first, and `requeue_links` raises the links of on-topic
      pages and of vouched-for hosts. Next: a loop run to measure the on-topic share of fetched
      followed links against run 15; graded vouches if one is too generous
- [x] `B-149` **Queued queries made policy rows** — `v0.162.2`;
      found by `B-148`. `enqueue` recorded a first sighting for every task, so each queued
      search query became a `fetch_policy` row named after its words, and each DOI one named
      after its prefix. Now only `url` and `sitemap` tasks record a domain; a unit test makes
      every queue task type be classified as an address or not. Existing rows are left for the
      operator (preview SQL in handover §0)
- [x] `B-148` **Integration tests leave the database as they found it** — test tooling, no
      bump. `make leak-check` runs the integration suite once on a fresh seeded database and
      prints every changed row count (all tables, from the catalogue) and configuration field
      (topics, agents, budget, the global fetch policy, the timetable). Leaks found and fixed:
      sources and history of map builds, a test graph's entities, policy rows created by
      queueing, a withdrawn seed's log row, the global fetch policy's `timeout_s` and editor
      (an attribute-set restore on a stale row wrote nothing), the editor after a display-zone
      change, and a seed test that "restored" a topic weight to a constant. That last one
      unbalanced the weights, so the next `add_topic` renormalised and logged every topic;
      fixtures that add topics now snapshot and restore them (`tests/cleanup.py`). A teardown
      that deleted every saved view named "Area: …" now deletes only its own. Two tests that
      passed only on a database other tests had filled now make their own rows. **Found, not
      fixed (operator's call):** queueing a search query records the query text as a
      `fetch_policy` domain (`trust.record_discovery` runs for every task type), so the policy
      table holds one row per query; and the dev database holds thousands of rows leaked
      before this (counts and cleanup SQL in handover §0)
- [x] `B-147` **Which tests expire with the calendar** — test tooling, no bump. `make
      clock-check DAYS=n` runs the Python and web suites today and `n` days ahead on every
      clock (Python via `time-machine`, the web's `Date` via a Node preload, Postgres via
      libfaketime in a throwaway container on its own port) and prints what fails only ahead.
      Nothing does at +400 or +1500 days. Moving only Python's clock is misleading: five
      tests failed that way, all from comparing a database `now()` with Python's. Found on
      the way: a guest-query test that passed only on a database other tests had filled, and
      the RUM planner test, which a small table can satisfy with a bitmap scan and a sort;
      both fixed. Tests waiting on a Postgres timer are marked `server_timer` and left out
- [x] `B-146` **Admin → Assistant access** — `v0.162.0`, ADRs 0003 and 0011, mock
      `docs/design/AdminAssistantAccess.dc.html`. Lists tokens (never secrets), issues one with
      a profile and an expiry (or none, flagged) and shows it once with setup for Claude Code,
      Gemini CLI and other clients, revokes; shows where `/mcp` is reachable from the browser's
      own address and warns harder when that is public. `/api/admin/tokens`
- [x] `B-138` **Assistants can connect over MCP** — `v0.159.0`,
      [ADR 0003](docs/adr/0003-external-assistants-over-mcp.md). Found while building it: nginx
      had no `/mcp` route, so the tunnel served the web app there; token verification needed
      `MERIDIAN_MCP_ISSUER_URL`/`…_RESOURCE_URL` or every tool refused; production published
      no port, so "server or LAN" needed a way in (`deploy/lan/publish-web.yml`, local-only by
      default); and the grant profiles named three tools the server lacks and missed two it
      has. Built: `python -m api.tokens issue|list|revoke`, the nginx route, default URLs,
      the override, fixed profiles with a drift test, an end-to-end test that a token
      initialises an MCP session, and `docs/guides/connecting-an-assistant.md`
- [x] `B-145` **Times shown in one display zone** — `v0.158.0`,
      [ADR 0009](docs/adr/0009-times-stored-in-utc-shown-in-a-display-zone.md). Storage was
      already right (all `timestamptz`, ISO 8601 on the wire); display was not: some screens
      used the browser's zone (`getHours`), others UTC (`getUTCDate`, sliced ISO strings), so
      one page could show two clocks. Now `display_timezone` (global policy row, default
      `Asia/Singapore`) drives `web/src/lib/time.ts` and `meridian_core.timefmt`; Admin →
      Display changes it (an IANA name, validated). A web test fails on any formatter outside
      `time.ts`; boost expiries picked as a date now start at midnight in the zone. Admin →
      Display has no mock yet
- [x] `B-144` **Postgres could not build a vector index** — deploy config, no bump. A parallel
      index build allocates `maintenance_work_mem` in `/dev/shm`, which Docker caps at 64MB, and
      `B-132` had raised the default to 512MB: on the server any HNSW build or rebuild would
      have failed with "could not resize shared memory segment". Found when the `B-136`
      benchmark failed exactly that way. Postgres now has `shm_size: ${PG_SHM_SIZE:-1g}` in every
      stack, and a test requires each stack's shm to exceed its `maintenance_work_mem`
- [x] `B-143` **A web test expired with the calendar** — test only. The boost form refuses an
      expiry in the past, and the test typed 2026-10-01, which became the past. The same trap
      as `P5-09` and `B-128`; the date is now 30 days from the test's own clock
- [x] `B-141` **Python standards enforced** — `v0.156.15`. PEP 8 and PEP 257 through ruff
      (`E`, `W`, `N`, `D2`–`D4`, convention `pep257`; `D401`, `D400` and `N818` off, with the
      reasons in `pyproject.toml`), and `make test` now runs `make lint` first. `ruff check .`
      had 33 failures nobody saw, because nothing ran it; 26 files were also unformatted.
      Docstring summaries were reshaped to one line followed by a blank line; tests and
      migrations are exempt from the docstring rules
- [x] `B-137` **Models tried in the operator's order** — `v0.157.0`,
      [ADR 0002](docs/adr/0002-model-routing.md). `agents.route_order` (migration
      `b137a11ce0de`): ordered rows first, lowest first, skipping what is disabled or does not
      declare the task; unordered rows follow by the old tier rule, so a registry without
      orders routes as before. Seeded order: local (10) → `hosted-compatible` (20, new,
      `HOSTED_LLM_*`) → hosted Claude (30) → relay (40). The local row now declares relation
      extraction. Tests read the seed and assert the ADR's order for synthesis and that the Ask
      panel never reaches the relay. Two integration tests that encoded §11.3's quality-first
      rule were rewritten to the decision
- [x] `B-136` **A half-precision vector index** — `v0.157.1`,
      [ADR 0007](docs/adr/0007-half-precision-vector-index.md). Benchmarked first on a
      200,000-passage sample against exact top-10 (`meridian-calibration/loop/b136/`): a third
      of the size, recall@10 within a point, faster median and tail. Migration `b136ba1f0000`
      builds `ix_chunks_embedding_hnsw_half` concurrently and drops the full-precision index.
      `meridian_core.vectorindex.indexed_distance` is the one expression; tests check Postgres
      plans it through the index and that no passage query orders by the plain column. The
      first benchmark run found `B-144`
- [ ] `B-135` **A model on the server, live or scheduled** — decided (ADR 0002); the order is
      built (`B-137`). Remaining: run synthesis unattended once a stage is configured. Two model paths exist
      and neither has run. (a) **Live:** the Ask panel (`P6-06`/`P6-07`) calls `local-chat`
      (`${LOCAL_CHAT_LLM_URL}`), but in production the API has no route out, so the model
      server must be on `lan` with an nft rule. On one GPU server a model server (vLLM or
      llama.cpp, OpenAI-compatible) could instead be a compose service on `internal`, profile-
      gated like `orchestrator`, sharing the card with the embedder. Size the card for both.
      (b) **Scheduled:** synthesis (`python -m worker.orchestrate --daemon`, daily by default)
      runs under the profile-gated `orchestrator` service; no timetable row starts it, and
      `hosted-*` and `local-llamacpp` are seeded disabled. Operator decisions: which model where,
      the budget caps (Admin's budget screen; the local stack has $25 a month and 750k tokens a run), and whether the local or hosted rows go first
- [x] `B-134` **Hosted rows on the current Claude models, at an explicit effort** —
      `v0.156.16`, [ADR 0004](docs/adr/0004-hosted-claude-generation.md). Seed and a data
      migration (only rows still on the previous seeded strings) move to `claude-opus-5-5` and
      `claude-sonnet-5-5`; `provider._call_anthropic` sends `output_config.effort` from
      `MERIDIAN_MODEL_EFFORT` (default `high`, invalid values refused before any call). The
      API's server-side refusal fallback is deliberately not enabled: the registry chain
      already handles refusals, and a server-side fallback would answer from a model the
      edge's provenance does not record
- [x] `B-133` **A higher sample bar for very long documents** — `v0.156.17`,
      [ADR 0006](docs/adr/0006-stricter-triage-for-very-long-documents.md). From
      `LONG_DOCUMENT` (1,000) live passages the rest is held unless the sample scores
      `LONG_TRIAGE_FLOOR` (0.48). Measured before building
      (`meridian-calibration/loop/b128/size_bands.py`): in that band 0.48 held no on-topic
      document and deferred clearly more off-topic text than 0.46. Length is one index probe;
      tier queries unchanged, the backlog count about 2× slower (once a minute).
      `triage_floor()`/`long_sources()` are shared with the `retopic` report so the two agree
- [x] `B-132` **Postgres sized for a board, fixed in compose** — deploy config, no bump. The
      production command hard-coded `shared_buffers=2GB` and left `effective_cache_size`,
      `maintenance_work_mem` (64MB) and `random_page_cost` (4) at their defaults. On the local
      corpus the vector index alone is 5.8 GB, and the cache hit rate was 81%. Each setting is
      now `${PG_…:-default}`, with guidance in `.env.example`; tests require a default for each
      and an `.env.example` entry. **On the server:** size `PG_SHARED_BUFFERS` to hold the
      vector index
- [ ] `B-131` **An override for a GPU server** — deploy config, no bump. The operator moved
      production from single-board computers to a server and may add a GPU (2026-10-04).
      `deploy/gpu/gpu-embedder.yml`: the sidecar reserves one NVIDIA device with
      `MERIDIAN_EMBED_DEVICE=cuda`, 8G RAM; the backfill draws 1024 a batch with
      `MERIDIAN_EMBED_REMOTE_ONLY`. Held by `tests/unit/test_gpu_override.py`; documented in
      `docs/guides/deployment.md` §3b. **Open until run on a real card:** confirm `memory_of: device`,
      measure passages/s, then decide `MAX_AUTO_BATCH`, bfloat16 on the card, and whether the
      backlog ceiling (20k) should rise
- [x] `B-130` **A bigger embedding batch moved the model off the sidecar** — `v0.156.14`.
      `MERIDIAN_EMBED_CHUNK_BATCH` is documented and the sidecar client refuses more than
      `MAX_TEXTS` (256) per request with ValueError, which `PreferRemote` reads as "sidecar
      unusable": it logged once and loaded a second copy of the model in-process, or with
      `MERIDIAN_EMBED_REMOTE_ONLY` failed every batch. Found preparing the GPU override, which
      wants bigger batches. The backfill's batch is now sent in requests of at most `MAX_TEXTS`
- [x] `B-129` **A GPU was sized from the container's RAM** — `v0.156.13`. `auto_batch_size`
      read `visible_memory()` (cgroup limit or `MemTotal`) on an accelerator too, so the
      embedder's 4 GiB limit gave a card of any size a batch of two. CUDA now reads the card's
      total with `torch.cuda.mem_get_info`; MPS (shared memory), a CPU, or a card that cannot be
      read keep the old path. Found reviewing the stack for a server with a GPU. Not yet run on
      a real card
- [x] `B-128` **A budget test expired at the month's end** — test only. `_budget_read` (the
      Admin screen) reads the real clock while the test placed its run at the file's fixed
      `NOW` in mid-September, so on 1 October the run fell outside "this month" and the screen
      said ready. The `P5-09` trap again; the test now places the run now and asks the refusal
      at the same instant
- [x] `B-127` **Copies were embedded like originals** — `v0.156.12`. The embedding tiers never
      read `sources.duplicate_of`, so a source `worker.docdupes` had marked as a copy was
      embedded in full and counted in the backlog that pauses the crawl, though search, the map,
      Gaps and synthesis all leave it out. Found 2026-10-04: about a fifth of the waiting
      passages, and about a tenth of everything ever embedded, belonged to copies. A copy now
      waits in the last tier (not none: the mark is re-judged daily, and the near rule compares
      mean vectors)
- [x] `B-126` **A zero drawn in the attention colour** — `v0.156.11`. The landing's contested
      count was brass with the dagger at 0, drawing the eye to nothing. Brass and dagger now appear
      together only above zero; unknown and zero are neutral
- [x] `B-125` **The question toggle covered content** — `v0.156.10`. Site review: the toggle
      (bottom-right, where the artboard puts it) sat on the node panel's footer and, on a phone,
      over the last lines of a page; the search box's "hybrid" marker took the placeholder's
      room. Reading pages keep room at their foot, the panel footer keeps its right corner clear
      and wraps, and the marker is hidden below `sm` (removing it outright is `B-99`)
- [x] `B-124` **Labels ran into each other** — `v0.156.9`. Site review: on the node graph two
      neighbours' names overprinted ("stopping sight distances|uired sight distances"), worst on
      a phone; on the Map long links were painted over the circles they crossed, through names and
      captions. The graph now places labels greedily each frame — one that would overlap a label
      already drawn is left out, the focus always drawn, every node still named on hover — and
      the Map paints links under the circles
- [x] `B-123` **Terms harvested from off-topic pages** — `v0.156.8`. The site review's first page
      of gazetteer approvals was radio and telecom acronyms. `worker.harvest` read every document
      with text; 27,729 terms waited, all harvested, none carrying a topic. Now only documents
      labelled on a topic are read; unlabelled ones wait for their label, off-topic ones are never
      read. The queued backlog is left for the operator to judge
- [x] `B-122` **Admin opened on a backlog** — `v0.156.7`. Bare `/admin` landed on Gazetteer
      approvals (the weekly task, §5.6) while `AdminLight` draws Topics; with the queue at tens
      of thousands of terms it read as the system being behind. Opens on Topics now
- [x] `B-121` **Gaps took seconds on every visit** — `v0.156.6`. Site review: "Loading the gaps"
      for 3.5–3.8 s each time. Timed per source: topic coverage counts every on-topic passage in
      the corpus (a union over every live passage), most of it, and it grows with the corpus. The
      list moves with the crawl, over hours, so the route keeps its answer for 15 minutes and
      refreshes behind the reader after that (`api/cache.py`, stale-while-revalidate, one refresh
      at a time, a failed refresh keeps the old list); only the first read after a restart
      waits. At the API, not in `gaps.py`, so the core's own tests still read fresh counts
- [x] `B-120` **The node page on a phone** — `v0.156.4`. Site review: the three fixed columns
      (filters 236px, graph, panel 384px) left a 390px screen no graph and a clipped panel. Below
      `lg` they now stack and the page scrolls — graph (62% of the height), panel, filters — with
      room under the last controls for the question toggle. Desktop unchanged; screenshot-checked
      at 390, 820 and 1440 wide, no horizontal overflow
- [x] `B-119` **Passages read as a PDF's columns and raw tables** — `v0.156.3`. Found by the
      2026-09-29 site review: text extracted from PDFs kept every layout line break, so a source
      page showed a ragged column of three or four words a line and Find broke sentences mid-way;
      Markdown tables showed as pipes and dashes. The display view (`readable`, `B-79`) now joins
      breaks that plainly continue (next line in lower case, or a trailing comma) in a passage
      whose breaks mostly fall mid-sentence, rejoins hyphenated words, and flattens table rows to
      cells joined by a middle dot. Headings, labels, list items and rows keep their lines. Run
      over 300 stored passages before shipping: a fifth rejoined, none merged a heading. The
      source page now uses the same view; stored text is unchanged (§2.4). `v0.156.5`: a line
      ending on a joining word ("of", "the") also continues, as the source page showed
- [x] `B-118` **Mined sitemaps were never claimed** — `v0.156.2`. Run 16: all 103 sitemaps
      `B-116` queued were still pending an hour later, at a priority above everything. Every
      claim draws a topic and takes only tasks filed under it; the sitemaps had none, so only
      the last-resort fallback could claim them, and it never runs while any topic has work.
      Now filed under the proven host's commonest topic, and a pending sitemap with none is
      refiled on the next pass. Also found: the hourly job failed with "No module named
      worker.sitemapmine" because the scheduler has its own image and only the worker's was
      rebuilt
- [x] `B-117` **More search engines** — `v0.156.1`. Searched how self-hosted SearXNG deals
      with engines refusing a single address: keep the engines that answer from it and spread
      queries over more of them. From this machine brave and startpage were suspended, qwant
      and baidu asked for a CAPTCHA; yep, yandex, naver and mwmbl (off by default) answered,
      98–100% relevant on `B-51`'s test. Enabled; bing web stays off (67%)
- [x] `B-116` **Mine proven hosts through their sitemaps** — `v0.156.0`. `P1-28` built the
      sitemap parser, its defences and the claim handler, and nothing ever queued a sitemap:
      the robots.txt sitemap list rode on every fetch result and was read by no one, so the
      `sitemap` seed source had zero rows. `worker.sitemapmine` (hourly, after `hostscore`)
      queues the same-host sitemaps a proven host's cached robots.txt names, or
      `/sitemap.xml` when it names none; once per sitemap. Entries go through the host policy
      like any followed link — capped per host, topic-matched paths boosted as proven, the rest
      at the bottom. The handler had queued matched entries at plain tier priority, ignoring
      the host decision; fixed. First pass: of 35 proven hosts, 24 advertise sitemaps
- [x] `B-115` **Proven hosts first** — `v0.155.3`. Tested before building: judged only on
      their pages from before loop runs 12–14, hosts at or above `FULL_SHARE` had 52% of their
      next pages on a topic, unjudged hosts 14%, borderline 10%; a read of sampled titles found
      the verdicts right at both ends, with some false negatives on general pages. Yet proven
      hosts took 3.5% of those runs' fetches, because priority is the source tier and a proven
      host in a low tier queued below every unjudged government link. A proven host's links now
      get `PROVEN_BOOST` on top of the tier, above any tier priority; caps still hold, search
      keeps its reserved claims, and the daily requeue applies it to the backlog. First of the
      operator's "tap proven sites" plan: then sitemaps of proven hosts (`B-116`), more search
      engines, and the pre-`B-91` backlog only when nothing better waits
- [x] `B-114` **A domain that refused every request was never blocked** — `v0.155.2`. Run 13:
      HTTP errors doubled as scholarly search supplied half the fetches, nearly all 403s from
      publishers that refuse crawlers outright. A 403 counts as "the domain answered", which
      resets the failure counter, so a domain refusing every request could never reach the
      auto-block. Now the hourly `hostscore` pass blocks a domain whose every request in 30
      days (at least 20) was a 403, with a note; a single answer of any other kind spares it.
      Not a consecutive rule: domains the corpus reads return unbroken runs of 403s too. The
      block lifts itself after 30 days and the domain is judged afresh; a block or unblock
      set by hand is respected. First pass: two dozen domains
- [x] `B-113` **An off-topic site's unjudged subdomains were each explored afresh** — `v0.155.1`.
      Run 12: two thirds of fetches came from followed links, and the new government pages
      examined were almost all off-topic. Large sites that serve every office, county or blog
      from its own subdomain were the cause: each subdomain started unjudged with ten links
      to explore, so a site judged off-topic over thousands of pages was re-explored through
      dozens of siblings. An unjudged subdomain now takes its registrable domain's verdict
      (public-suffix list, so a national suffix is never one site) when that is off-topic:
      queued last and capped as unknown, never dropped, and replaced by its own verdict once
      it has one. The daily requeue applies it to the backlog: about a tenth of it moved
- [x] `B-112` **One slow host held the whole worker to its pace** — `v0.152.11`. Run 11: 245
      fetches in an hour (runs 8–10 ≈1,500). The science results topped the queue on two hosts
      (503 on a preprint server with a long crawl delay); every lane claimed there and waited on
      its politeness limit. The claim now skips page tasks on hosts this worker is already
      waiting on (a request queued, or next start >5 s away); they keep their place
- [x] `B-111` **Ask the scholarly engines too** — `v0.152.10`. With every web engine refusing
      (`B-109`), SearXNG's science category still answered, on topic (Google Scholar 28/30,
      Semantic Scholar 30/30). Every subject now also gets a `!science …` query, taken early
- [x] `B-110` **Apply host verdicts to the queued backlog daily** — `v0.152.9`. `worker.requeue`
      (`B-48`) had run by hand only; on 2026-09-27 it held ~79k of a 233k frontier backlog
      (arXiv listings, a statute library, a university repository) and raised ~21k. Daily now
- [x] `B-108` **Search seeding back to every 3 hours** — `v0.152.8`. `B-104`'s hourly pass
      tripled the day's queries and was the likely trigger of `B-109`
- [x] `B-107` **Throttled search engines lost queries for good** — `v0.152.7`. Found in run 10:
      search 28 of 1,445 fetches. Every web engine behind SearXNG was suspended (rate limit,
      CAPTCHA); only news answered. A query with no results because engines refused was
      settled as done, and never asked again. Now retried after ≥30 min (6 times), and the
      client leaves ≥15 s between queries so an hourly batch is not a burst
- [x] `B-106` **"Crawl more of this" searched a field's commonest words** — `v0.152.6`. At the
      top level those were "shall public information"; now the field's name, the topic that
      holds it, and its first two-word term
- [x] `B-105` **News queries may be asked again** — `v0.152.4`. A news query added 29 new
      URLs on average against 11 for other shapes, but the no-repeat rule allowed each once
      ever. Now askable again 3 days after it was last asked
- [x] `B-104` **Search seeding hourly** — `v0.152.2`. Run 9 began with 8 search results
      left: 6% of fetches from search against run 8's 32%. Live timetable row set by hand
- [x] `B-103` **Topics with no vocabulary ran out of search queries** — `v0.152.1`. Found in
      run 9: biology, economics and robotics have no approved gazetteer terms, so each had
      about ten possible queries, all long asked; their search stopped. Queries now also
      come from the subjects a description lists ("costs, pricing, fares") and their pairs
- [x] `B-97` ⚑ **Gaps leads with empty topic × place pairings** — `v0.149.9`. Operator
      (2026-09-27): rank them below the other kinds. Place gaps now weigh at most 0.19,
      under every other kind's floor; still listed, last
- [x] `B-98` **Search snippets that are site navigation** — `v0.149.7`: the passage was prose; its first line was a widget prompt, now dropped from display. — a result's snippet can be a
      page's menu text ("I'm looking for…"). Prefer the best-matching non-boilerplate
      passage for the snippet
- [x] `B-99` **Reader-surface jargon on Find** — `v0.162.3`, ADR 0013. The operator left the
      call to the build: reworded, keeping both facts. The badge reads `words + meaning` (the
      summary line's vocabulary) and the note "Filters narrow what is searched, not what is
      shown."; mocks and design-system §8 changed with it, and tests keep both free of
      `hybrid`/`vector`/`lexical`/`embedding`. Screenshot of the built landing checked against
      the updated `ExploreLanding.dc.html`
- [x] `B-100` **Neighbourhood graph names cut to a dozen characters** — `v0.149.6`, screenshot-checked. — the small ring
      graph truncates most node names; show full names on hover or wrap two lines
- [x] `B-101` **Map on a phone** — `v0.149.4`, screenshot-checked at 390px. — the size key covers a fifth of the canvas at 390px;
      collapse it to a one-line key below 640px
- [x] `B-102` **Themes repeat their field's name** — `v0.149.8`. The live build predated the level-wide de-duplication (400 themes, 252 names); renamed, and names that carry a phrase drop the second field. Field-of-work naming (the operator's choice, `B-71`/`B-74`) kept. — many themes are named from the fixed
      field list (`B-71`) and read the same as their siblings ("Transportation & Automotive
      Engineering" several times); name the finest level by its distinctive terms instead
- [x] `B-94` **A question offers the nodes its words name** — `v0.149.3`. UX review
      2026-09-27: a question in Find got "no node is named <the whole question>"
- [x] `B-96` **"No place named 32" reads as "32 name no place"** — `v0.149.2`
- [x] `B-95` **A search is in the URL** — `v0.149.1`. UX review 2026-09-27: Find read
      `?q=` but never wrote it; a search could not be shared and Back left the page
- [x] `B-93` **The Map zooms like a map, with lines at every level** — `v0.149.0`. From
      the operator (2026-09-27): after changing level by zooming the map could not be
      zoomed, and cross-field lines were not visible. The wheel only stepped the level;
      lines were drawn at the root level only. Now zoom/pan with the level following the
      magnification; sibling and field lines at every level. Screenshot-checked live
- [x] `B-91` **A followed link records its parent page** — `v0.148.15`. Loop run 8:
      search results 70% on a topic, followed links 4% (English alone 4%), and followed
      links were two thirds of fetches — a fan-out into sibling subdomains of large sites,
      ten exploratory links per new host. The parent was never recorded, so whether the
      parent's label predicts the child's could not be measured. Next: measure it in run 9
      and, if it does, gate link-following on it (`B-92`)
- [x] `B-92` **Follow links from pages that earned it** — `v0.151.1`. Run 9 could not measure
      parent → child yield: every link queued since `B-91` was still pending behind a ~211k
      older backlog. Built on run 8's 4% instead, reversibly: `worker.requeue_links` (hourly)
      moves pending followed links whose page was read and found about none of the topics to
      the host gate's floor priority. Down only, nothing deleted, idempotent. Measure its
      effect once links queued after 2026-09-27 12:40 are being fetched
- [x] `B-90` **Search results a cached robots.txt refuses are not queued** — `v0.148.14`.
      Found in loop run 8: a third of the window's search claims went to one site whose
      robots.txt refuses everything, returned again by every search pass. The prefilter
      now reads the crawl's fresh cached file (followed links too); a savepoint keeps a
      cache error from poisoning the caller's transaction
- [x] `B-89` **A long document is embedded from a sample first** — `v0.148.13`. Found
      2026-09-27: over half the embedding backlog sat in about a hundred very long
      documents (omnibus legal texts, spreadsheet dumps, index pages), unlabelled because
      labelling waited for every passage. Now the sample (first 16, then every 16th) is
      labelled first and the rest waits in the last tier unless the sample scores
      ≥ 0.41. Measured on fully embedded live sources: sample best within ~0.015 of the
      whole text's at the median; at 0.41 about 2% of on-topic sources of 40+ passages
      are held (none of 300+), for about a third less embedding. Sample labels are re-read
      once the whole text is embedded
- [x] `B-88` **The duplicate pass marked a page a copy of itself** — `v0.148.12`. A
      translation and a near copy disagreed about direction and made a loop
- [x] `B-87` **Search seeding every 3 hours** — `v0.148.10`; the live timetable row was set
      by hand (config seeds first boot only)
- [x] `B-86` **Search results get a third of claims** — `v0.148.9`. Since the crawl fixes,
      search-found sources were on a topic about three times as often as followed links
- [x] `B-85` **The nightly harvest timed out** — `v0.148.8`. Quadratic boundary scan, a text
      read that could not use the live-chunk index, and unindexed gazetteer lookups;
      200 documents went from 45 s to under 7 s on the live corpus
- [x] `B-84` **The duplicate pass failed daily past 32,767 sources** — `v0.148.7`. It bound
      every source id in one IN list; now a join, and the update is batched
- [x] `B-82` **The embedding sidecar finishes a request its client abandoned** — `v0.148.11` — a batch is
      encoded in one thread call, so a backfill restarted mid-batch (every deploy) leaves
      the sidecar at full CPU for up to a few minutes on work nobody will store. Encode in
      slices and stop when the request is disconnected
- [x] `B-80` **The Answer view showed a source by its heading** — `v0.147.4`. A passage of
      120 characters or more now outranks a shorter one for its source
- [x] `B-79` **Passages showed raw Markdown link syntax** — `v0.147.3`, `v0.148.3`. About a
      quarter of live passages carry `[text](url)`; cleaned for display only
- [x] `B-78` **An empty neighbourhood panel beside every question** — `v0.147.2`
- [x] `B-77` **Embedding in bfloat16 on a CPU that has it** — `v0.147.1`. 145 → 297
      passages/min measured on the live machine; vectors within 0.998 cosine of float32's.
      int8 measured and rejected (cosine 0.905)
- [x] `B-76` **A joint restart loaded the model twice** — `v0.144.1`. The backfill asked the
      sidecar once at startup; it now waits for it, and compose orders them
- [x] `B-75` **The first embedding tier newest first** — found closing loop run 4: new sources
      are labelled only once embedded, and the window's passages queued behind older
      first-tier ones, so no run could measure its own on-topic yield. The other tiers stay
      oldest first; a failed batch is stepped past by id
- [x] `B-74` **Area names aligned to fields of work** — `v0.143.0`. The operator: "align to
      fields of work", after licence strings ("bync", "free article") still named areas.
      Named by the nearest OpenAlex field/subfield (`config/fields.yaml`) by centroid;
      terms stay as detail
- [x] `B-71` **Map area names were keyword lists with furniture in them** — `v0.141.0`.
      Reported by the operator ("arxiv title arxiv model", "nan"). One phrase per name;
      furniture dropped at build and at read
- [x] `B-72` **A web of topics** — `v0.142.0`, a tab on the Map. The operator's idea: topics overlap, so let a person pick
      several and see the sources where they all meet. Server side `v0.141.0`
      (`topic_match=all`, `/api/explore/topic-overlaps`); picker UI `v0.142.0`
- [x] `B-73` **Saving a view with a topic filter always failed** — `v0.142.0`. Client key
      `topic`, server field `topics`; found by the B-72 agent
- [x] `B-69` **Titles that are not titles** — `v0.140.7`. Reported by the operator: placeholder
      PDF titles, site names as page titles, thousands missing. `meridian_core.titles`
      at write time; `worker.retitle` for what is stored
- [x] `B-70` **Every harvested gazetteer term was a `concept`** — `v0.140.7`. Reported by the
      operator. Typed by head word where unambiguous; spelling repaired; `harvest --retype`
      for the existing rows
- [x] `B-68` **A topic's crawl share went to cited-paper lookups** — `v0.140.6`. Found in
      loop run 3: one topic got no page fetches in two hours because its top-ranked rows
      were DOIs at priority 60. The ordinary draw claims page work only; the directed slot
      alternates lookups and search results
- [x] `B-67` **Throttling wrote papers off** — `v0.140.5`. A DOI held back only by a
      rate-limited provider retried within seconds and failed after three refusals;
      hundreds were lost in a single run to an anonymous shared quota. Provider cooldown
      (Retry-After, else doubling 1 min → 30 min), throttled DOIs retry after it with a
      larger budget, and `requeue_dois --revive-throttled` recovers the ones already
      failed. A Semantic Scholar key (⚑ operator, free) would remove most of the
      throttling at source
- [x] `B-66` **Embed by value, not by age** — `v0.140.3`. Three tiers, re-checked every
      batch: directed sources and on-topic hosts first, the rest next, off-topic hosts
      last, junk never; backpressure counts the first two only. Measured on the live
      backlog when it shipped: about a fifth first-tier, three quarters from hosts too
      little examined to judge, a few per cent off-topic hosts. So the tiers fix the
      *order*, not the rate — the crawl still out-produces the embedder about two to one
      and backpressure will duty-cycle it. Labels are written after embedding, so no
      per-page signal exists sooner; the rate is the next lever (`P2-19` sidecar, a
      smaller model, or embedding fewer passages per page)
- [x] `B-53` **Non-English pages label lower** — `v0.162.4`, ADR 0014. Rescale chosen: a
      non-English page's scores are stretched from a pivot (0.20, ×1.20, a least-squares fit over
      918 paired topic scores) as they are computed and stored, so every floor sees one scale;
      the lost labels come back, one pair gains one. Revisit with per-language prototypes once
      `B-52` writes translations. Originally: — the same paragraph scored several
      hundredths lower in translation than in English against the topic prototypes
      (another paragraph showed no gap). Near the 0.45 floor that turns
      a relevant non-English page into "off-topic", which then feeds the host gate.
      Measure on real non-English sources once `B-52` seeds have fetched some;
      options are per-language prototypes from `translation_lookups` or a
      language-aware floor
      **Measured 2026-09-29** (`meridian-calibration/b53/notes.md`): the same pages
      in English and other languages, paired by URL. The gap is proportional, not
      constant: none for off-topic pages, a few hundredths in the middle band, and
      enough at the top that an on-topic page lost its label in every translation.
      Topic order survives translation; height does not. The lowest-labelled language
      is mostly generic landing pages, so its rate is largely real. Thin because `B-52` has never written a seed
      (`translation_lookups` empty; needs `MERIDIAN_CONTACT_EMAIL`). ⚑ Operator's
      choice: a non-English floor near 0.46, a proportional rescale, per-language
      prototypes after `B-52` runs, or wait for more pages
- [x] `B-54` **Schedule `worker.docdupes`** — `v0.124.1`, timetable row shipped
      *disabled*: read the first live report, then enable it in Admin
- [x] `B-55` **Seeds and watches are not in the steering audit** — `v0.125.1` for seeds (a "watch" does not exist yet; it arrives with `P6-35`) — `POST /api/admin/seeds`
      writes no `steering_log` row, so the map's "suggest a term" and Gaps' actions
      would be steering nobody can see or undo from the log (found by the Map and Gaps
      agents)
- [x] `B-56` **A search's yield is not recorded** — `v0.124.2`, `queue.search_results`/`search_queued`; `_settle` stores no result count and
      result rows do not link to their query, so "a query that found nothing" cannot
      feed Gaps; add the count to the query row (a migration)
- [x] `P6-30` **Areas: the corpus as nested clusters** — `v0.134.0` (delegated agent; calibrated on a read-only copy of the live corpus: regions of a few hundred to a few thousand passages, stable positions across rebuilds, ~1.5 min per build). Open: some region names are still source furniture; English-only stop words — hierarchical clustering
      of passage embeddings (2–3 levels), each area named by its most
      distinctive terms, with per-area stats (passages, sources, tier mix,
      recency) and a stable layout position; recomputed on a schedule; honest
      about what an area is (a cluster, not a topic)
- [x] `P6-31` **Bridges between areas** — `v0.135.0` (delegated agent) — cited claims whose evidence spans the
      two areas, plus the most similar cross-area passages and the terms both
      share; the two kinds kept apart everywhere they are shown
- [x] `P6-32` **Route across claims and areas** — `v0.129.0` (delegated agent), backend and API; its screen is the Map's route mode (`P6-34`); area hops plug in when `P6-30` lands — extends `P6-03`'s path search
      to areas and terms; every hop labelled cited or similar; "no cited route
      within N hops" is a result and feeds Gaps
- [x] `P6-33` **Neighbourhood** — `v0.128.0` (delegated agent; screenshotted dark and light against the Term neighbourhood board with live responses). Open: the board's caption says CLAIMED where the build says CITED — term → nearest entities by claim and nearest
      passages/terms by embedding; shown as a panel beside Find's results
- [x] `P6-34` **The Map screen** — `v0.136.0` (delegated agent; screenshotted dark and light against the Areas boards, in `~/Documents/gh/meridian-calibration/p6-30/`). Route mode ships disabled pending wiring to `P6-32`, which is now on main — areas zoomable by level, size key, weak/stale
      outline, bridge panel, route mode, 3D toggle (`P6-29` moves inside it),
      "jump to an area or term"
- [x] `P6-35` **Steering from the map** — `v0.137.0` (delegated agent). Open for the operator: is "watch" as a saved view enough (no notification)? Should "less" work on areas no topic holds (most of the old drift), which would need host or term demotion? — right-click an area or term: more, less,
      make a topic, watch; right-click empty space: suggest a term, queued as a
      search seed. Through the existing boost/topic/seed machinery, so it is
      reversible and in the steering audit
- [x] `P2-22` **Run the question set** — `v0.126.0`, `scripts/run_question_set.py` (built by a delegated agent). **First live run not yet made**: it needs the live database's read-only role and the embedder, which the agent was not permitted to reach — run `eval/questions.yaml`, record graded
      scores per run in `eval/runs/`, compare runs; an agent's grades are a
      proposal, the operator's are the score
- [x] `P6-36` **Gaps** — `v0.127.0` (delegated agent). There was no mock; it follows the pre-cut Coverage board's gap panel and was screenshotted against a test database only, so **look at it on the live corpus before calling it done**. Areas and routes are pending sources — a ranked list from thin/weak areas, failed routes and low
      question-set scores, each with its reason and an action
- [-] Topic × topic matrix and a coverage grid — **cut** (2026-09-23): the grid
      would be near-empty on nine attribute values and its axes are arbitrary; the
      matrix does not scale and its question is answered by bridges and routes


*Checkpoint: reading the graph is genuinely better than reading the sources.*

- [x] `P6-01` **Sigma.js canvas, focus + expand, depth-1 neighbours capped and ranked** — `v0.117.0`.
      `/nodes/{id}` is the workspace: a deterministic radial Sigma canvas of the
      focus and its depth-1 neighbours in both directions, capped (30, up to
      100) and ranked by supporting passages — there is no weight column, so the
      UI says what it ranks by — with second-hop hints, hover cards, breadcrumb,
      Fit/zoom, a table view, and the node panel from the Explore artboard. The
      canvas stays dark in both themes. Built by a delegated agent
- [x] `P6-02` **Canvas filters: topic, attribute, source tier, date, contested-only** — `v0.117.0`.
      Applied server-side to the evidence — one passage must satisfy them all,
      an undated passage fails a date bound — with facets counted before
      filtering; state lives in the query string
- [x] `P6-03` **Path mode between two nodes** — `v0.117.0`. Bounded
      breadth-first search (default 4, at most 6 hops), best-supported route
      among equal lengths, never through a merged node; "no route within N
      hops" is a result
- [x] `P6-04` Node detail panel: grouped tags with overflow, attribute list with
      confidence — `v0.73.0`. Built before the graph on purpose: the hard parts
      are about how a claim is presented, and waiting for rows does not make them
      easier. Tags group by §7.1's scope, because a flat row asserts that a
      corpus-wide dimension and a topic-local one are the same kind of claim.
      Confidence is a number on the tag, since rounding to "high" throws away the
      difference between 0.61 and 0.94. And this is the **only** read path that
      shows superseded chunks — the tag was derived from that text, so the
      citation has to resolve even after the page changed
- [x] `P6-05` Annotation as first-class nodes — `v0.75.0`. A note is an
      `entities` row with ordinary `annotates` edges, not a side table: §12.1's
      traversal, path mode and canvas filters all read `entities` and `edges`,
      and a notes table would make the layer §12.5 calls the highest-quality one
      in the system the only layer the graph cannot see. **Authorship is
      assigned by the server and cannot be claimed** — `AnnotationCreate` has no
      `produced_by` and forbids extra keys, and `seed.py` refuses to register an
      agent under the reserved `human` id. `quality_tier` stays null, because
      §11.12's tier is an ordinal over models and a person is not on it. A note
      may be about nothing, which with phase 4 unbuilt is every note — the
      thought that has not found its node is the one the corpus cannot
      re-derive. Its citations ride on the edges too, since every edge here
      names the chunks behind it. Composer on both reading surfaces, never in
      Admin (§12.6)
- [ ] `P6-06` Synthesis panel: collapsible toggle, thread, node chips, inline citations —
      **built in `v0.154.0`, not yet run against a model.** Panel, `chat` task type, the
      disabled `local-chat` row, server-side citation checks and a daily token cap are
      in; what remains is pointing `LOCAL_CHAT_LLM_URL`/`LOCAL_CHAT_MODEL` at a real
      OpenAI-compatible server, enabling the row, and judging answers. Tick then
- [ ] `P6-07` Conversation history within the synthesis panel — built with `P6-06`
      (`v0.154.0`): threads persist, "earlier questions" reopens them. Ticks with it
- [x] `P6-08` Notifications panel, filterable by type — `v0.60.0`. Reads the
      rows `P5-07` writes before it delivers, so a deployment with no bot token
      still sees what would have been sent. By type rather than read state, per
      the model's own reasoning — a read/unread split turns findings into an
      inbox, and an inbox gets cleared without being read. Counts cover every
      type so the filter cannot hide what the reader came for
- [x] `P6-09` Saved views — `v0.74.0`. A table rather than `localStorage`: a
      saved view is research method, so it survives a cleared cache, reaches a
      second device and travels in the snapshot. Reads sit on `/api/explore` and
      writes on `/api/admin`, which looks inconsistent and is the right split —
      views are shared state with no per-viewer scoping, so a guest should open
      the owner's and not add to them. Filters are validated against
      `SearchFilters` before storing, because a view that silently drops a filter
      when reopened hands back a result set the reader believes is narrowed
- [x] `P6-10` Coverage grid and contested list as entry points — `v0.155.0`, the contested list: `/api/explore/graph/contested` (each pair once, newest first, one-sided marks included) and `/contested`, opened from the landing's Contested card. Screenshot-checked light, dark and phone with injected pairs; the live graph has no contested pair yet, so live shows the stated absence. The coverage grid half stays cut (2026-09-23); Coverage opens Gaps
- [x] `P6-11` Explore landing state with since-last-visit delta — `v0.61.0`.
      `stats?since=` plus a `localStorage` stamp read once per session and
      advanced immediately, so the delta means "since you were last here" and
      does not vanish as you look at it. Three states kept distinct: `null` for
      a first visit (no moment to measure from), `0` for nothing arrived (worth
      saying, or a silent panel reads as a failed load), and the delta itself
- [x] `P6-12` Admin: topic management — add, pause, archive with re-normalising
      weights — `v0.65.0`. §10's vector, with the three places the obvious
      implementation is wrong. Normalising is **not** dividing by the total: the
      floor is a guarantee ("nothing fully stalls"), and a violated floor is the
      guarantee not existing while the vector still sums to 1.0 and looks fine.
      Bounds relax on read and are refused on write — pausing every topic but one
      leaves a single topic with a 0.6 ceiling, and the seeds still have to come
      from somewhere. A boost is applied at read time and never cleared, because
      "steer back later without remembering" breaks the moment it depends on a
      cleanup job. `steering_log` records the weights that moved because somebody
      steered a *different* topic, which is the question §10.1 exists to answer
- [x] `P6-13` Admin: gazetteer approvals — `v0.64.0`. Split: this ID is now the
      Admin shell plus §5.6's "approve in the UI", and the other three surfaces
      it used to name are `P6-22` and `P6-23`. Every row reports whether the
      matcher will **actually load it**, computed against the whole approved set
      — an approved term whose wording another row claims is withheld, so it
      reads approved and matches nothing, and nothing else in the system says
      so. It found a real one on the first page, which is `v0.63.1`. Rejection
      is a tombstone (`rejected_at`) rather than a delete, because the harvest
      re-reads the same documents and would re-create the row. `/api/admin/*`
      fails closed: 503 unless Access is configured or
      `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` says the instance is not exposed
- [x] `P6-22` Admin: fetch policy per domain — `v0.71.0`. Delay, concurrency,
      timeouts, render mode and status. The editable set is an **allowlist**, and
      what it excludes is the point: the SSRF guards and `respect_robots` and
      `user_agent` are deployment settings, because a form that could switch off
      private-address blocking would sit one click from controls about
      politeness. Editing the global row needs a server-checked `confirm`, since
      a client-side dialog is a promise. Three layers shown separately — set,
      resolved, learned — because a value a reader cannot find anywhere to change
      is this screen's characteristic failure. `seed_allowed` is `P4-12`'s column
      and is not built yet
- [x] `P6-23` **Admin: agent registry and run history** — `v0.109.0`. Held
      open on its own argument — both tables are empty until phase 4 runs
      something, and "an empty screen teaches nothing about what the full one
      should look like" — and the argument held: what the screens needed was
      decided by what the first runs actually did, which is mostly *stop for
      reasons*. So the registry leads with `unserved_tasks`, the failure that
      lives between the rows rather than in one: routing picks by task type, an
      undeclared type defers every run, and every row looks correct while it
      happens. Each row carries routing's own reasons for skipping it, because
      an enabled agent with an unset key is indistinguishable from a working
      one until a run defers hours later. Enabling is the only write; models
      and endpoints stay reviewable configuration with migrations behind them.
      Run history shows counters rather than a verdict (§11.9) and explains
      that deferred is a retry, not a failure (§13.4). **No key is returned or
      rendered** (§11.11)
- [x] `P6-25` **Crawl health dashboard in Admin** — `v0.112.0`.
      `meridian_core/crawlhealth.py`, `GET /api/explore/crawl-health`,
      `web/src/admin/CrawlHealthPanel.tsx`. A day of hourly fetches, the
      outcome mix, queue and embedding backlog, the last hour's busiest
      domains, and a liveness verdict computed on the server by one pure
      `judge()`. Stalled means no attempt for one claim lease while work is
      ready; `waiting` keeps a queue that is only backing off from reading as
      an outage — which is also what a `B-33` retry held behind robots.txt
      looks like. Read-only role, next to `/progress`, so it still works when
      the admin gate is closed. Every query is bounded by `now`, which is how
      the tests assert verdicts against a shared database. Built by a
      delegated agent in a worktree and reviewed on merge
- [x] `P6-28` **Admin, as designed** — `v0.116.0`. Section nav with a URL per
      section, paper by default (an explicit dark choice wins), topic weights
      staged and previewed by a rolled-back server write before Apply, add and
      archive dialogs showing the re-normalisation, a steering-audit rail,
      table-first restyle of every section, bulk and keyboard gazetteer
      decisions (paged, all-or-none), paged and searchable fetch policy. Two
      places where the server disagrees with §8 are shown as the server does
      them and are open: pause re-normalises rather than holding the share,
      and pins are not held on a user re-normalisation. Built by a delegated
      agent
- [x] `P6-29` **The corpus map in three dimensions** — `v0.115.0`. Three PCA
      components (subspace iteration widened and pinned against the exact
      decomposition on all three axes), drawn as one three.js `Points` cloud on
      a dark-pinned canvas: orbit, zoom, pan, fit, idle rotation, screen-space
      picking for hover cards, click to the source. The flat view stays as a
      toggle and as the fallback when WebGL is missing or lost. three.js loads
      only on this page. The caption names the three variance shares. Topic
      colours hash from the name so a new topic cannot repaint the rest — at
      the cost of the strongest first slots, a trade left open. Built by a
      delegated agent after the operator expected a 3D view of the vectors
- [x] `P6-27` **The application shell and the Explore landing, as designed** —
      `v0.114.0`. One 54px top bar on every screen with the §5 cluster: a
      status pill (queue, fetch health, a brass dot and the words "run failed"
      on a failed run, hollow when run history cannot be read), a bell and a
      notifications panel grouped by day and filtered by type, and settings
      holding the theme. ⌘K from anywhere. Pages set their own width. The
      landing follows its artboard; where the mock shows numbers nothing
      produces yet (coverage cells, weekly and edge deltas, recent nodes) the
      screen says so instead. Results and source page restyled; long passages
      clamp. Built by a delegated agent after the operator judged the previous
      UI "not as per what we designed"; checked by screenshot against the mock
- [x] `P6-26` **The corpus map: the vectors, seen** — `v0.111.0`. `/map`
      draws a sample of searchable passages placed by their embeddings,
      coloured by topic, with the passage on hover and its source on click.
      **PCA, not UMAP**: linear and deterministic, so distance on the map is
      distance in the space and the same corpus draws the same picture — and
      the caption says how much of the space two axes carry (about 9% on the
      first real corpus), so the picture cannot pass for more than a shadow.
      Drawn through search's own filters, so it shows exactly what search can
      return. Two measured fixes before it was usable: sampling sorted whole
      rows, embeddings included, and a full eigendecomposition took seven
      seconds to produce the two components drawn — replaced by seeded subspace
      iteration that agrees with it to four decimals in a tenth of a second.
      Series colours are new to the design system and were validated for
      colour-vision separation in both themes; light mode's three low-contrast
      hues are why the legend is labelled and a table view exists
- [x] `P6-14` Figures panel with page-accurate raw file links — `v0.56.0`.
      No thumbnails, because nothing downloads figure images (`P1-10`) and a
      placeholder grid would promise what the corpus cannot keep; the caption is
      the content, per §6.6. Two links with different meanings — the publisher's
      live image, and this corpus's own copy at `#page=N`, which is what §5.4
      keeps raw files for. **Raw serving is off unless `MERIDIAN_SERVE_RAW` is
      set**, because the store holds third-party material and serving it is
      redistribution; the link is then absent with a line saying why, rather
      than broken. The raw path comes from the row, never the request
- [x] `P6-15` Export: Markdown and BibTeX — `v0.55.0`.
      `meridian_core/export.py` plus `/api/explore/export/{bibtex,markdown}`.
      Nothing is generated: a field the document did not carry is omitted, since
      a fabricated year is wrong in a file somebody pastes into a paper. TeX
      escaping, because an unescaped `&` fails in *their* document rather than
      here. Keys are stable across exports and carry the source id. The entry
      type is a format decision and the tier rides verbatim in `note`, because a
      bibliography is exactly where `@article` vs `@misc` would read as the
      credibility verdict §8 refuses to compute. A UI action is still to come
- [x] `P6-16` Shared UI primitives from the design system — `v0.33.0`. The
      17-icon set on §7's grid (11 interface icons, 6 node glyphs), source-tier
      and data chips, and the contested mark in its three forms. `Icon` owns
      every shared attribute rather than repeating it per icon, including §7's
      optical-size rule that drops interior detail below 20px. Tier chips take
      no variant, tone or colour prop on purpose: §2 says the palette has no
      green and no red and that colour must not imply a verdict, so there is
      nowhere for one to go. The drawings follow the published grid and are a
      first pass — the geometry is right, the draughtsmanship is where a
      designer should still put hands on
- [x] `P6-17` Theme switching, honouring the system preference by default —
      `v0.32.0`. Three states, not two: `system` is the default and is expressed
      by the *absence* of `data-theme`, so the `prefers-color-scheme` block
      applies. Two-state theming is the common bug and is invisible in the
      working case — a toggle that only ever writes `light` or `dark` looks
      correct to whoever built it and silently overrides the preference of every
      reader who never touches it. Restructured `tokens.css` into palettes
      (each published colour written once) and per-theme role mappings, which is
      what makes the mapping drift testable
- [x] `P6-21` **Source detail page** — `v0.57.0`. `/sources/{id}`: the header
      with tier, date, DOI and `extractor`, the passages in document order, the
      figures panel and both exports. The first screen where the corpus reads as
      documents rather than results, and where `P6-14`/`P6-15` finally have
      somewhere to live. A textless source is a finding, not a failure (§6.5)
- [ ] `P6-20` **Replace the hand-rolled router when the screen count justifies
      it.** `v0.57.0` added forty lines rather than a dependency, because two
      routes do not justify inheriting an upgrade path — and URLs had to be real
      so a source page can be linked into a citation. Phase 6 has fifteen more
      screens; when nested layouts or route-level data loading arrive, replace
      it rather than growing it
- [ ] `P6-19` **Three lockup values are inferred, not published.** §1 gives the
      mark's geometry exactly and the lockup's gaps (22px) and wordmark weight,
      but not the cap-height ratio the hairline rule sits at, nor the
      mark-to-wordmark size ratio. Both were picked to look right in `Lockup`
      and neither is a design decision anyone made. Also: §1 states "Bounding
      box 76 × 86.2" immediately after the *compact* table, and it matches the
      **full** mark's extents exactly — the compact variant computes to 76 × 88
      because its zenith and base nodes are larger. Publish the values, or
      confirm the inferences. ⚑ human
- [ ] `P6-18` **Four light-theme roles are inferred, not decided.** The design
      system publishes nine light tokens against thirteen dark, and the four it
      omits are all canvas roles — its graph-canvas table is headed "Dark" and
      has no light column. `tokens.css` maps them to values the design system
      *does* define rather than inventing hexes (recolouring outside the palette
      is an explicit Never), but a mapping made by the person wiring the tokens
      is not a design decision. Decide whether the canvas stays dark in both
      themes, and publish the light values if it does not. ⚑ human

## Phase 7 · Full design

*Checkpoint: the analytical layer the whole thing was for.*

- [ ] `P7-01` Attribute proposal gate: evidence requirement, discrimination test, hard cap
- [ ] `P7-02` Monthly attribute audit with hysteresis before retirement
- [ ] `P7-03` Schema evolution backfill, amortised across runs
- [ ] `P7-04` Analogical expansion with disanalogies recorded on every comparison edge
- [ ] `P7-05` Contradiction tracking and contested-pair marking
- [ ] `P7-06` Temporal decay flags in gap analysis
- [ ] `P7-07` Enrichment queue: figure VLM, quality-tier OCR, chart OCR — user-triggered
- [ ] `P7-08` Reprocessing, downgrade guard, old-vs-new disagreement logging
- [ ] `P7-09` Report generation with coverage pre-flight (spec §11.13)
- [ ] `P7-10` Edge precision sampling and entity resolution audit as routine

---

## Backlog — unscheduled

Things worth doing that don't belong to a phase yet.

- [x] `B-10` `figures.linked_entity_ids` is an array, not a JSON document —
      `v0.75.2`. It was the only `json` column in the schema and the only list of
      ids not stored as `ARRAY(BigInteger)`. Two probes rather than one assertion,
      both derived from the live schema so they grow with it: every `*_ids` column
      is an array of bigint, and nothing uses `json` where `jsonb` was meant.
      **The migration is four statements**, because there is no cast from `json`
      to `bigint[]` and supplying one needs a subquery, which Postgres rejects in
      `USING` — autogenerate's version would have failed on the server and passed
      here, since `figures` is empty locally. Caught by migrating rows put there
      on purpose, and the downgrade round-trips
- [x] `B-11` **Licence audit** — `docs/reference/licences.md` and a gate, `v0.83.0`.
      Every verdict read off the installed artefact rather than recalled:
      distribution metadata, image labels, the model card on disk. **Nothing
      blocks commercial use.** All 116 Python distributions are permissive — no
      GPL, no AGPL, no non-commercial terms — and the weights the task called
      the likeliest problem are clean (`BAAI/bge-m3` is `license: mit`, read
      from the snapshot). Two things needed a judgement rather than a reading.
      **SearXNG is AGPL-3.0-or-later** (confirmed from its image label): run
      unmodified, in its own container, over HTTP, with no published port —
      aggregation, not derivative work, and the document states exactly which
      changes would turn §13 on. **`tld` is tri-licensed** MPL-1.1 / GPL-2.0
      / LGPL-2.1+, and we take MPL-1.1; the choice is recorded in code, and a
      test checks the package still offers it. Also found: Meridian's own three
      packages declared no licence at all, which is the worst case rather than
      a neutral one — no licence is no grant. **Both ⚑ human calls
      accepted 2026-09-20** and recorded in the document beside the evidence:
      the SearXNG boundary, and `tld` under MPL-1.1. `en_core_web_sm` and
      Apache AGE are named but unverified — neither is installed yet, and both
      should be confirmed from an artefact when they arrive (`P4-01` brings
      AGE)
- [x] `B-12` Fix: `docker-compose.yml` set `MERIDIAN_EMBEDDER_CACHE` and the code
      reads `MERIDIAN_EMBED_CACHE` — `v0.76.3`. The `/models` volume was
      therefore never used and 2.3 GB re-downloaded on every recreate, silently,
      because the whole point of `os.environ.get(name, default)` is not to
      raise. A misspelt variable cannot fail loudly, so the test is the only
      mechanism available: every name any compose file sets must appear in some
      Python source, with a short exemption list for the ones read by the
      Postgres entrypoint and the upstream images — and a second test that the
      exemptions are all still set, so the list cannot become a museum
- [x] `B-13` Fix: a workspace package must declare what it imports — `v0.76.4`.
      The third time, and every time found by *running* a container rather than
      building one: `meridian_core.embedder` imports `httpx`, declared on
      `meridian-worker`; `services/api` imports `jwt`, which arrived
      transitively through `mcp`; and `worker.embedserver` imports `fastapi`
      from the `embed` extra the worker image did not install, so `P2-17`'s
      sidecar had never once started from a container. `P1-30` wrote the rule
      down and the handover predicted the recurrence; predicting it was not
      enough. Now derived from the AST on both sides, per workspace member
- [x] `B-14` Fix: the embedding sidecar could never obtain its weights —
      `v0.76.5`. `embedder` is on `internal`, which has no DNS and no route out,
      and the weights are not in the image — the compose comment claimed they
      were, two lines above the mount that exists because they are downloaded at
      runtime. So a fresh stack's sidecar answered `loaded: false` for ever and
      timed out every request, and search stayed lexical-only in *production*
      too, which puts this on `P2-09`'s critical path. `python -m
      worker.fetchmodel` is the one-shot with egress that writes into the volume
      the sidecar reads, profile-gated so `up` never waits on it. It loads and
      encodes rather than only downloading, because a cache missing one file
      fails at the first real batch instead. The isolation is kept deliberately:
      the sidecar runs corpus text through a model, and a route out from there is
      a route out for anything that ever gets in
- [x] `B-16` Fix: every fetched page was being thrown away — `v0.76.6`. Docker
      creates a missing bind-mount source on the host as **root**, and every
      application image runs as `meridian` (uid 1001, `cap_drop: ALL`, read-only
      root). So the first containerised crawl fetched a handful of real pages,
      could not create `/data/raw/<domain>/`, logged a traceback per page and
      settled each task `"outcome": "success", "stored": null` — because the
      *fetch* had succeeded. It kept crawling and kept nothing, and the same
      would have happened on the server: the deploy docs chown `/srv/meridian/
      app` for the checkout and say nothing about `raw`, `figures` or `models`.
      A `chown` one-shot on `network_mode: none`, in both compose files, of the
      three top-level directories only — everything created beneath them
      inherits the owner, and a `chown -R` over a 100 GB raw store is a
      different mistake. The uid is now tied to the Dockerfiles by a drift test,
      since three copies of 1001 is three chances to move one
- [x] `B-17` Fix: the documented way to create the database could never have
      worked — `v0.76.7`. `docs/guides/setup.md` and `docs/guides/deployment.md` both said
      `docker compose run --rm worker alembic upgrade head`, and the worker
      image has no `alembic` — it is in the root project's `dev` group and every
      application image syncs `--no-dev` — and never copies `scripts/`, so the
      seed line failed too. Both are the *first* commands an operator runs, on
      the server, and neither had been run there. `deploy/tools/Dockerfile`
      (written for `B-05`) is promoted to production compose, profile-gated, and
      the docs name it: the thing worth preventing was a stack that migrates
      *itself* on boot, and the profile is what prevents that, not the image's
      absence. `make migrate` is not the answer on the server — docs/guides/setup.md §2
      never installs `uv`, and `postgres` resolves only inside the compose
      network. New test walks every `docker compose run` in the deploy docs and
      checks the named service's Dockerfile actually carries what is invoked
- [x] `B-18` Fix: `docker compose up -d` could not bring up the production
      stack — `v0.78.2`. `orchestrator` is phase 4 and its Dockerfile does not
      exist, and **compose does not skip a service it cannot build**: it fails
      the whole command with `lstat ...: no such file or directory`. The same
      shape as `web`, which had no Dockerfile either until `B-05`; that one was
      written, this one profile-gated until the image exists. Also removed
      `api`'s `ports: 127.0.0.1:21114:8000`, which published nothing — a
      container attached only to an `internal: true` network has no gateway for
      the host to forward to, so the line was inert under a comment promising a
      port to curl. Both generalised rather than patched: every non-profiled
      service must build from a Dockerfile that exists, and nothing may publish
      a port on networks that are all internal. Each verified by reintroducing
      the bug
- [x] `B-15` **The timetable now has something reading it** — `v0.79.0`.
      `P5-06` was ticked and its code worked, and no compose file had ever run
      `python -m worker.scheduler` — so `seed.py`'s five `scheduled_jobs` rows,
      all `enabled`, all with `next_run_at` already past, were a timetable
      nobody read. A stack left alone fetched, extracted, chunked and stopped:
      nothing embedded, deduplicated, swept or harvested, ever, in either
      stack. Found by bringing the stack up for `B-05` and watching
      `embedded_chunks` sit at zero against a growing backlog.
      A `scheduler` service in both files — the worker image under a different
      command, like `embedder`, because the jobs it spawns are `python -m`
      entry points from the same tree and inherit its environment. **It is a
      second service on both networks**, which `P1-22`'s boundary previously
      allowed only `worker` and `orchestrator`: one of the five jobs
      (`worker.digest`) reaches Telegram and the other four want nothing
      outside `internal`. The trade is argued in the test rather than hidden —
      the same image already makes it, and a scheduled send that fails into
      `last_error` is the failure mode this task existed to remove
- [x] `B-20` Fix: the sidecar spent two and a half minutes per cold start
      asking a host it cannot reach — `v0.79.1`. `embedder` sits on `internal`
      by design and reads its weights from the cache `modelfetch` filled, but
      `huggingface_hub` does not know that: every load issued HEAD requests for
      the optional config files the cache does not hold, got `Temporary failure
      in name resolution`, and retried five times with backoff *per file*
      before proceeding from cache anyway. Correct, slow, and logged as a wall
      of warnings that look exactly like the failure they are not — which is
      how `B-14` was nearly mistaken for still broken. `HF_HUB_OFFLINE=1` says
      the true thing about where that container is standing: **144.6s → 4.9s**
      to first embed, measured both ways on the same populated cache, and no
      warnings. It is also the honest failure mode, since a genuinely missing
      required file now raises "not in cache" instead of timing out against a
      host that was never reachable. Asserted both ways — a model loader on an
      isolated network must be offline, and the fetcher must never be
- [x] `B-19` **The scheduler can be probed** — `v0.80.0`. `B-15` left it
      unprobed and, worse, *silently* unprobed: omitting `healthcheck:` does
      not give a container none, it inherits the image's — and the worker
      image's imports `worker.main` and checks poppler, so a wedged scheduler
      would have read `healthy` for ever while suppressing the restart that no
      probe at all would have left to `restart: unless-stopped`.
      The loop now writes `P5-08`'s heartbeat itself, and the two placements
      are the design. **After each claim, not before it** — `worker.main` beats
      before its work because a lane wedged inside a fetch should stop beating
      within the iteration, whereas here the database round trip *is* the thing
      that hangs, so a beat before it would be refreshed by a scheduler that
      never gets an answer. **And throughout a running job**, because a
      backfill legitimately takes half an hour and a probe that fired during
      normal work would restart the scheduler in the middle of the job it was
      reporting on. That second beat proves less — a job is in flight and has
      not hit `--timeout-seconds` — and the timeout is what keeps it honest.
      Generalised: a long-running service overriding its image's command must
      declare a healthcheck or disable one explicitly
- [x] `B-21` **The nightly acronym harvest raised on every run** — `v0.103.1`.
      `HarvestStats.created` became an `extra` key, and `logging` raises rather
      than dropping a key that shadows a `LogRecord` attribute — `created` is
      the record's timestamp. The pass settled `failed` in half a second every
      night, in the one line that reports what it did. Renamed to
      `terms_created`. The existing guard reads literal `extra={...}` dicts out
      of the source and could not see a dataclass arriving through `as_dict()`;
      it now sweeps those field names too, which is the hole all three
      recurrences of this came through. Found in a live scheduler's log, not by
      the suite
- [x] `B-24` **The vector arm answered short and nothing said so** —
      `v0.103.2`. pgvector's HNSW index returns at most `hnsw.ef_search` rows
      per scan, default 40, so the arm asked for 100 candidates and got 33 on a
      live corpus while the lexical arm returned 100. Two consequences, both
      silent: RRF sees arms of different depths and under-weights the vector
      side, and `scripts/benchmark_search.py` at k=100 could not have exceeded
      40% recall whatever the index did — a fourth way a benchmark lies, and
      the only one that does not need a small corpus. `_vector` now sets the
      depth from the pool it asks for, doubled, because the index cannot see
      `_conditions()` and spends candidates on rows the filter discards. The
      test pins the mechanism rather than the row count: a few hundred vectors
      is too few for the planner to use the index at all, so the obvious
      assertion passes against the bug
- [~] `B-30` **A page can be spread across sources, and is not yet** —
      `v0.110.0`. Measured: 45% of a page's hits come from the probe chunk's
      own document, and ten hits span about four and a half sources. Not a
      broken arm — adjacent chunks genuinely *are* its nearest neighbours — so
      the mechanism ships and the decision does not. `search(max_per_source=)`
      is off by default, exactly as `P2-20`'s decay is: `P2-04`'s benchmark and
      `P2-09`'s go/no-go measure the current baseline, and turning it on first
      would mean judging something else. Held-back hits are backfilled rather
      than dropped, and `held_back` is reported for the reason the decay's
      adjustment is shown. The trade is a number now — a cap of 2 spans 77%
      distinct sources and displaces about four hits per page. **Stays `[~]`
      until `P0-15` exists**: which setting is better is only answerable
      against questions somebody wrote down first. 2026-10-08, on 18 questions written
      before any result was seen, with `bge-reranker-v2-m3` as the relevance judge (score ≥ 0.5):
      relevant hits in a top ten were 5.33 uncapped, 5.17 at a cap of 2, 4.78 at 1, 5.33 at 3;
      a cap of 2 was better on one question and worse on three. 8 of 18 tops had one source three or more
      times, but those repeats were relevant. Stays off; diversity would have to be wanted for
      its own sake
- [x] `B-29` **Arm agreement was measuring two different questions** —
      `v0.105.4`. `scripts/benchmark_search.py` paired a sampled chunk's vector
      with an unrelated frequent word, so the arms could not agree and the
      figure was 0% by construction — and it blamed the text-search config for
      it, which would send somebody after a bug that does not exist. Both arms
      now answer one probe, with the probe chunk excluded so it cannot match
      itself in both. **Two wrong versions before one right one**, each looking
      like a finding about the corpus: the second ANDed its terms, which
      co-occur only in the probe chunk, and reported the lexical arm finding
      nothing at all. With `or` the real figure is 13–16%: fusion earns its
      second query
- [-] `B-39` **A rephrased attribute value replaces the last one** — decided,
      no change. `tag_entity` keeps one value per entity and attribute, and a
      later passage that words it differently replaces it. The operator's
      call (2026-09-23): replacement is acceptable. Recorded so the behaviour
      reads as a decision rather than an oversight
- [x] `B-40` **Near-duplicate names never reached adjudication** — `v0.113.4`.
      §5.5 weighs meaning (embedding, 0.3) as well as words, and `block` can
      search by nearest vector — but no entity had ever been embedded and
      `resolve_mention` took no vector, so the meaning half had never run.
      "autonomous shuttles" / "driverless shuttles" scored 0.38 on words alone,
      below the middle band, and became two nodes nobody was asked about;
      with the vector the pair scores 0.66 and reaches a person. Mention names
      are embedded per batch through the sidecar, new entities keep their
      vector, older ones are backfilled a batch per stage, and an unreachable
      embedder degrades to words alone as before. The orchestrator is given
      `MERIDIAN_EMBEDDER_URL` in both compose files
- [x] `B-42` **Privacy policies, terms and search pages reached the corpus** —
      `v0.117.1`. `B-23`'s furniture rule matched whole path segments only, so
      `/privacy` was caught and `/privacy-policy`, `/terms-of-use`,
      `privacy_policy.html`, `/contact-us/` and `/sitemap` were not; nor were a
      search engine's result pages. Segments are now also read as words, and
      match when the *whole* word sequence is a furniture phrase — so
      `/terms-of-reference-for-the-review` stays a document — and a query-
      carrying `search`/`results` page is furniture. `python -m
      worker.furniture` demotes what is already in the corpus to the junk tier
      (report by default, `--apply`, `--domain`), and **never demotes a page
      the graph cites**: the rule reads an address, and evidence outranks it.
      Synthesis no longer pulls junk. Chunk-level boilerplate inside content
      pages (footers, banners) is a separate problem, not addressed here
- [x] `B-41` **A merge leaves identical edges side by side** — `v0.129.1` (delegated agent); the live graph's one duplicate is left for `worker.edgedupes --apply` after the measurement window — merging #27 into
      #9 moved edge 21 onto the same subject, relation and object as edge 6,
      and nothing combined them. `add_edge` treats that triple as one claim
      with two citations; `merge` should too, keeping every citation and the
      moved edge's id in the merge log so `reverse` can split them again
- [x] `B-38` **On a fresh database, `merge_log` was created inside AGE's catalog** —
      `v0.113.3`. The graph-store revision set `ag_catalog` first on the
      search path with `SET LOCAL`, and Alembic runs a whole `upgrade head`
      in one transaction, so the next revision's table landed in
      `ag_catalog`: present, ungranted, and invisible to the application roles
      — every merge failed with "relation does not exist". The revision's own
      comment warned of exactly this and reasoned that `SET LOCAL` avoided it.
      The dev database, migrated a revision at a time, never showed it; the
      live stack, migrated from empty, did. Now the revision restores the path
      after `create_graph`, a repair migration moves and grants the table where
      it is misplaced, and **a new test migrates a scratch database from empty
      in one run** — the first test of what a first deployment actually does
- [x] `B-37` **Two nodes with one name resolved to either, by heap order** —
      `v0.113.2`. With two exact matches, `block` returned them in table-scan
      order and the first won the tie; updating a row moves it in the heap, so
      the choice flipped between reads. A resumed batch then resolved a name
      differently from the first pass, the entity list in its `tag` prompt
      changed, and the batch could never complete. Candidates are now ordered
      by id and a tie goes to the oldest. Found by the relay agent after
      fourteen batches, on the duplicate `B-35` had left behind — which was
      then merged into the older node with `resolution.merge` (reversible)
- [x] `B-36` **Every synthesis run started again from chunk 1** —
      `v0.113.1`. §6.3 keeps one mark for the system, but `begin_or_resume`
      created each run with none, and `pull` reads no mark as "from the
      beginning" — so every run re-read the first forty passages and none
      got further. With a paid model, the same batch bought on every run.
      The tests set the mark by hand, which is why none noticed. A new run now
      inherits the furthest mark any run reached. That broke the loop's
      progress check ("the run has a mark"), which would then have run every
      wake-up to `max_cycles`; progress is now the mark moving within the
      cycle, and a deferral ends the wake-up instead of opening and closing
      throwaway runs — five per tick, seen live. Found by the relay agent's
      second batch
- [x] `B-35` **Re-reading an acronym founded another node for it** —
      `v0.112.2`. With a gazetteer expansion, `resolution.block` searched only
      the expanded words, so "ODD" looked for "operational design domain" and
      never found the node literally named "ODD"; the expansion's node scored
      into the middle band and a new "ODD" was created, with an adjudication
      notice, on every re-read. A name of two letters had no token long enough
      to search by at all. Blocking now unions the written and expanded
      tokens and always includes the exact name, sorted first. Found on the
      first live synthesis run (`P4-18`), where a resumed batch re-ran
      `extract` and changed the entity list the `tag` prompt carries. One
      duplicate made before the fix is on the live stack, queued for a person
- [x] `B-34` **Two Admin panels drew borders in the text colour** —
      `v0.112.1`. `RunsPanel` and `AgentsPanel` used `border-border`, which
      names no role. Tailwind does not reject a utility it cannot resolve; it
      drops the colour, and the border fell back to `currentColor` — full
      text-colour outlines on every card, on screens that passed review and
      tests. Now `border-line`, and a drift test fails any colour utility with
      no `--color-*` role behind it. Spotted by the agent building `P6-25`
- [x] `B-33` **An unreadable robots.txt was filed as the site saying no** —
      `v0.110.1`. Found on the local stack after a reboot: the worker came up
      before DNS, every robots.txt it asked for was unreachable, and RFC 9309
      §2.3.1.3 correctly refused those origins — but the refusal was recorded
      as `robots_denied`, which abandons the task, with an error claiming the
      site had disallowed a URL it never saw. Now `robots_unreachable`, retried.
      **The obvious fix would not have been one**: the retry backoff is seconds
      and the cached refusal lasts ten minutes, so all three attempts would have
      been told the same cached "no". Retries are held back past the cache, and
      the refusal carries no domain signal — one failed read is served to every
      task on the origin, and counting each would block the domain over a blip
- [x] `B-32` **Nothing ever created a budget, so nothing could ever run** —
      `v0.108.0`. `P4-13` refuses a synthesis run without a budget row and
      §16 requires caps "before first autonomous run"; the ordering was
      enforced against a row that no code path created, so every deployment
      deferred at `extract` saying so. Seeded from `config/budget.yaml` at
      first boot, with a migration for existing databases, and neither touches
      a row that is already there — a null cap means *unconfigured* and
      refuses, so re-filling one would turn "stop until I think about this"
      into "carry on with the default". **Proven on the live stack**: with the
      budget in place a run reaches the provider and defers with
      "hosted-frontier reads its key from ANTHROPIC_API_KEY, which is unset",
      which is the last step before an edge and is not code
- [x] `B-31` **The snapshot could not read the corpus it was snapshotting** —
      `v0.107.1`. `P1-16`'s stated deliverable is `make snapshot-corpus`, and on
      a real deployment it produced a 15KB `raw.tar.gz` beside a 113MB dump:
      the raw store belongs to uid 1001 (`B-16`) and host `tar` could not read
      a file of it. Every citation in that snapshot would have dangled, and the
      script says so in its own header — "a dump whose `sources.raw_path`
      values point at files you did not keep is not a corpus, it is a
      catalogue". Archived from a container now, with the file count checked
      against the database so an empty archive cannot pass quietly. Also fixed
      `P1-45`'s warning, which compared the container's path to the host's and
      therefore fired on every containerised snapshot — a warning that is
      wrong every time is one nobody reads
- [x] `B-23` **The frontier routes identifiers and skips site furniture** —
      `v0.107.0`. A `doi.org` link is an identifier wearing a URL's clothes:
      fetched, it redirects to a publisher and yields a paywall or a landing
      stub, which is why 134 of 244 such sources held no extractable text. The
      citation channel had been queueing the same identifiers correctly as
      `doi` tasks the whole time, so this is a route rather than a drop — the
      resolver deduplicates the two channels for free, because both arrive as
      the same bare identifier. Site furniture is a shape, not a topic: a path
      segment that is exactly `about`, `help`, `privacy` and a dozen more,
      matched segment-wise because `/about-congestion-pricing` is an article
      and nothing else distinguishes them. Measured on the live frontier of
      56,487 url tasks: 1,850 routed, 831 dropped. **Three existing tests used
      `/about` as a sample link** and had to move — worth knowing, because the
      next shape gate will do the same thing
- [x] `B-22` **A backlog larger than one job window never drains** —
      `v0.104.0` and `v0.108.1`. The embedding pass ran hourly inside a
      thirty-minute scheduler ceiling, was killed at the ceiling, and left a
      backlog no later window could clear; `B-25` made it a continuous service
      and `B-27` found why the sidecar was never used. The half that remained
      was the signal: falling behind left `last_status='timeout'` in
      `scheduled_jobs` and nothing else. `check_embedding_backlog` now raises
      §12.5's alert on either an absolute count or a share of the corpus —
      two thresholds because the count misses a small corpus that is mostly
      unembedded. It says what a reader will see, since search cannot:
      `degraded` reports an arm that is *absent*, and one covering a third of
      the corpus just returns less
- [ ] `B-01` Qdrant migration path, if pgvector recall becomes the measured bottleneck
- [ ] `B-02` App-level auth and roles, when Cloudflare Access stops being sufficient
- [ ] `B-03` Multimodal embeddings for figure similarity search
- [ ] `B-04` Offline corpora (OSM extract, filtered arXiv) — selective, storage-hungry

### Running it locally

For someone who wants to *run* Meridian rather than develop it. Today's quickstart
assumes `uv`, `npm`, and three terminals; this is the path that doesn't.

- [x] `B-05` `docker-compose.local.yml` — the full stack from source, so a fresh
      clone needs no registry access — `v0.77.0`. Also the two images
      `docker-compose.yml` had always referenced and nobody had written:
      `web/Dockerfile` (node builds, nginx serves, no Node in the runtime) and
      `deploy/tools/Dockerfile` for Alembic and the seed, which no service image
      carries because they sync `--no-dev`. **The network split is kept**: it is
      `P1-22`'s boundary, and a local stack that flattened it would let someone
      develop against a topology production does not have. One deliberate
      difference — `api` and `web` sit on a third `frontdoor` network, because a
      container on an `internal: true` network cannot publish a port at all and
      the `ports:` line is silently inert rather than an error. Two defects found
      by running it: `api` was never given `MERIDIAN_EMBEDDER_URL`, so every
      search reported "this deployment has no embedder" next to a healthy
      sidecar; and `.localdata/` was not gitignored
- [x] `B-06` `make quickstart` — one command from a fresh clone to a running,
      seeded stack — `v0.78.0`. Preflight, build, Postgres alone first, migrate,
      seed, fetch the weights, start the rest, wait for the API, print the URL.
      **Idempotent by construction**, because "run it again" is the only thing
      anybody tries: compose converges, `alembic upgrade head` is a no-op at
      head, `seed.py` skips what it has written and the weights resolve from
      cache. Postgres starts alone so a failed migration is readable rather than
      interleaved with six services' startup logs
- [x] `B-07` First-run experience — seeds editable from the UI — `v0.85.0`.
      §16 calls cold-start seed quality "worth spending an evening on", and
      until now that evening had to be spent editing `config/seed_sources.yaml`
      **before** the first boot, because the file is read once and never again
      (§13.1) — by somebody who has no idea yet what belongs in it. Now a Seeds
      section in Admin lists what is still pending, takes new URLs and search
      queries, and drops ones nobody wanted. **Not a wizard and not a gate**:
      the crawl has already started by the time anyone opens it, so the screen
      shows what is still changeable *and* what has been reached, and the
      second is not styled as an error. Admin opens on Seeds when nothing has
      been crawled yet, because the default section on a fresh machine is
      otherwise an empty gazetteer queue with no hint of what to do. A typed
      URL is still validated — a private address is no safer for having been
      typed than proposed — and a seed cannot be removed once claimed, which is
      two conditions rather than one: **claiming is a lease, so a seed being
      fetched right now is still `pending`**, and checking only the status
      would delete a row out from under a worker mid-fetch
- [x] `B-08` Preflight check script — `v0.76.2`. `scripts/preflight.sh` checks
      Docker, Compose v2, cores, memory and free disk against the README's
      minimums and names the *consequence* of each shortfall, not just the
      number. **Warnings are not failures**: only a missing daemon or Compose v1
      exit non-zero, because those mean nothing can start — being under the
      stated minimum is a legitimate choice on hardware somebody already owns,
      and the figures are sized for a 50k-document corpus. `MemTotal` rather than
      `MemAvailable` (available is mostly page cache and says nothing about
      whether the stack fits), disk measured at `DATA_ROOT` rather than the
      checkout, colour only on a TTY. A drift test ties the numbers to the README
      table, which is where a user reads them
- [x] `B-09` **What a first run shows — decided and built**, `v0.86.0`. The
      task offered two ways out and the decision is recorded where it is
      enforced: **make the first hour legible rather than ship a demo corpus.**
      A snapshot of a real crawl is third-party content, and whether it may be
      redistributed is the question §14.2 keeps separate from everything else —
      the same reasoning that keeps `MERIDIAN_SERVE_RAW` off by default, and
      `B-11` reached it independently the same week. Shipping a corpus in the
      repository would have answered that question the other way without saying
      so. Synthetic fixtures were ruled out by the task itself. So
      `GET /api/explore/progress` reports the queue by status, both halves of
      the last hour's fetch rate, and the domains most recently fetched — every
      number true of that machine right now. **The two failures that both look
      like "no results" are separated**: a crawl with nowhere to begin says so,
      and a crawl where every fetch failed says that rather than reporting
      twenty attempts as progress
