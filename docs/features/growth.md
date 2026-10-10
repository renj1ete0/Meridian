# Corpus growth

The Growth page shows how the corpus grew, day by day: pages kept on each topic, passages,
sites, the knowledge graph, and the map. It answers "is the crawl still finding things, and
where?" at a glance, and makes the days the crawl was off visible instead of hiding them.

- **Code:** `packages/meridian_core/meridian_core/growth.py`, `schemas/growth.py`,
  `areabuild.record_history`; `services/api/api/routes/growth.py`; the `corpus_growth` MCP
  tool; `web/src/explore/GrowthPage.tsx`, `web/src/lib/growth.ts`
- **Tasks:** `B-140`
- **Decisions:** [ADR 0005](../adr/0005-growth-page-for-readers.md),
  [ADR 0010](../adr/0010-growth-page-placement-and-range.md),
  [ADR 0009](../adr/0009-times-stored-in-utc-shown-in-a-display-zone.md)
- **Design:** `docs/design/CorpusGrowth.dc.html`

## How it works

`GET /api/explore/growth?range=30d&topic=…` returns one answer for a window (`7d`, `30d`,
`all`) and an optional set of topics. Both are kept in the page's URL, so a filtered view can
be linked.

| Figure | Counted from |
|---|---|
| Pages kept on a topic, per day | Sources created that day, not junk, not copies, labelled with a shown topic. A page about one shown topic counts for it; a page about two or more counts once, as "two or more", so a day's stack adds up to pages |
| A gap | A day with no fetch attempt and no kept page: drawn as a dashed baseline, never as zero |
| Passages on a topic | Live passages whose own labels carry a shown topic (`chunk_topics`) |
| Sources kept | Kept sources (labelled with a shown topic when filtered) |
| Sites | Hosts by the day their first kept page arrived |
| Knowledge graph | Concepts (not notes, not merged away) and links. Not narrowed by topic |
| The map | `area_build_history`: one row per map build, kept after the build is pruned |

Days are calendar days in the display zone (ADR 0009): the query converts each timestamp to
local time before taking its date. Every figure is "as of" one instant, so nothing created
after it counts. "All time" counts the whole total as in the window.

Each topic has a stable colour slot: its place in the sorted list of every topic, so filtering
never repaints the others. Past eight topics, a topic is drawn in the neutral "other" colour
(design system §2, series). The chart has a hover tooltip per day and a table view.

## Design choices

- **Counted, not logged.** Growth is derived from timestamps the corpus already holds, so it
  needs no new tracking and cannot drift from the data. The one exception is the map, whose
  builds are pruned, so each build now writes a history row.
- **Kept for five minutes per window and filter** (`KeptByKey`, at most 32 combinations),
  refreshed in the background. Counting passages per topic takes seconds on a large corpus.
- **Fetch attempts are pruned after 30 days**, so for older days a day counts as crawled if a
  page was kept that day.

## Operating it

- The page is `/growth` (top bar, beside Map and Gaps).
- An assistant reads the same answer with the `corpus_growth` MCP tool.

## Tests

`tests/integration/test_growth.py` (bucketing in the display zone, multi-topic pages, junk and
copies, gaps, the "as of" bound, new sites, passages, map history),
`tests/integration/test_growth_api.py`, `tests/unit/test_api_cache.py`;
`web/tests/growth.test.tsx`, and the drift checks in `web/tests/api.test.ts`.


### A window that holds everything

On a corpus younger than the chosen window every count is new, and the tiles read "+91,850 in
30 days" beside 91,850. A tile whose window count equals its total now says "all since"
the first day instead (`B-187`), formatted as a calendar date, not an instant.
