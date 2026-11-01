# What's new

"What is new in the evidence on my topics since I last looked?" Growth answers how much the
crawl collected; the landing's since-last-visit line answers how many. This page and its MCP
tool answer *what*: the sources that arrived (or were published) in a window, grouped by topic
and kind, each with its title, site, date and one passage, and the links and tags the knowledge
graph gained in the same window, each with the passage it was derived from.

- **Code:** `packages/meridian_core/meridian_core/whatsnew.py`, `schemas/whatsnew.py`;
  `GET /api/explore/new` in `services/api/api/routes/explore.py`; the `whats_new` MCP tool in
  `services/api/api/mcp/server.py`; `web/src/explore/WhatsNewPage.tsx`, `web/src/lib/lastVisit.ts`
- **Tasks:** `B-237`
- **Design:** `docs/design/WhatsNew.dc.html`

## How it works

`whats_new(sess, since=…, as_of=…, basis=…)` is the one function behind the route and the tool,
so the site and an assistant cannot disagree.

1. **The window.** From `since` to `as_of` (now). `basis=added` keeps sources that entered the
   corpus in the window (`created_at`); `basis=published` keeps sources whose publication date
   falls on or after `since`'s calendar day in the display zone and not after `as_of`'s (a later
   date is a parsing error, as on the topic cards). A window reaches back at most 366 days; an
   earlier `since` is moved up, and the answer's `since` says so.
2. **Which sources.** Kept sources (`stats.kept_sources()`: not junk, not a copy), the one
   definition every count uses. Unfiltered and by `added`, the topics' totals plus `elsewhere`
   equal the landing's `new_sources` for the same instant; a test holds it.
3. **Grouped by leading topic.** The shown topics are those not archived, heaviest first (or the
   ones asked for). A source is placed once, under the first of its `topic_labels` (best first)
   that is shown, and names the others as `other_topics`, so the groups add up. A source on none
   of them, labelled off-topic or not yet labelled, counts in `elsewhere`. A topic with nothing
   new is kept, with zero.
4. **By kind.** Each group counts its sources by `doc_kind` and lists the newest few (5 by
   default, at most 20). A `listing` is counted but never listed: it is followed for links and
   never chunked, so there is nothing in it to read.
5. **The excerpt.** No model. Among the source's passages that search would return (search's own
   predicate), the one the passage labeller scored highest for the group's topic
   (`chunk_topics.topic_scores`), then the earliest; unscored passages come after scored ones. Of
   the best four, the first that is not a reference list (`references.is_reference_list`) and is
   at least 60 characters (a heading or caption otherwise) is cut to one line of 240 characters.
   No readable passage: no excerpt, and the page says so.
6. **New findings.** Links (`edges`) and tags (`attribute_values`) created in the window, newest
   first, 10 by default. Left out: `annotates` edges, and anything touching a note, a merged-away
   or a withdrawn concept. Each carries its first supporting passage, in the order synthesis cited
   them, that a reader may open; a finding with none is not counted. Placed by its own topic
   labels (a tag by its concept's); with topics asked for, one on none of them is left out.

## Design choices

- **By leading topic, once.** Listing a two-topic source under both made the totals overcount
  what the landing counts; Growth's stack has the same rule for the same reason.
- **"Since last visit" is the visit the landing counted from.** The landing advances the stamp
  when it opens (`openSession`), so by the time a reader clicks through, `meridian.lastVisit` is
  *now*. `openSession` now keeps the stamp it replaced as `meridian.previousVisit`, which What's
  new reads and never writes; the landing's link also carries the instant itself (`?since=`).
  With no earlier visit in the browser, the page falls back to a week and says so.
- **A chosen window is not a stamp.** `?window=7d` means the last seven days from whenever the
  link is opened; only "since last visit" carries an instant.
- **Readers see what Find shows; a model sees what is cleared.** The page lists unscreened and
  quarantined sources as Find does. The tool passes `cleared_only=True`: sources, excerpts and
  citations are narrowed to what screening cleared (§2.5), as `search_chunks` is.
- **The MCP tool is new, not `list_new_since` changed.** `list_new_since` walks passages by id
  from a mark so a synthesis session misses none; it has no date or topic, which is what the
  review found wanting. Changing its contract would break that walker; `whats_new` answers the
  reader's question beside it.
- **Not cached.** Each query is bounded by the window and an index (`created_at`,
  `publication_date`); the page is opened, not polled.

## Operating it

- The page is `/new`: in the top bar beside Explore (not on a phone, where five sections fill
  the bar), and linked from the landing's since-last-visit line when something arrived.
- `GET /api/explore/new?since=&basis=&topic=&doc_kind=&per_topic=&findings=` (read-only role,
  `explore.read`). `since` must carry an offset; without it, the last week.
- An assistant asks the same with the `whats_new` tool (`since` or `days`, `basis`, `topic`,
  `doc_kind`).

## Failure modes and traps

- **Empty topics on a fresh corpus** are expected: sources count under a topic only once the
  labeller has examined them. Until then they are in `elsewhere`.
- **Findings without a readable citation** disappear from the list rather than showing a claim
  with nothing to check; a burst of synthesis over quarantined pages shows as fewer findings
  for an assistant than for a reader.

## Tests

`tests/integration/test_whats_new.py` (window bounds both ends, junk and copies, leading-topic
grouping and archived topics, kinds and listings, publication-date basis, the excerpt rule,
cleared-only narrowing, findings and their citations, the landing-delta agreement, the route's
refusals); `tests/integration/test_mcp.py` (the tool returns the page's answer narrowed to
cleared material); `web/tests/whats-new.test.tsx`, and the drift entries in `web/tests/api.test.ts`.
