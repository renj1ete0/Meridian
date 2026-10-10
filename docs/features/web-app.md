# The web app

A React and TypeScript single-page app, built with Vite and served by nginx in production. It
is the reader's way into the corpus (Explore, sources, nodes, the Map, Gaps, contested pairs,
the Ask panel) and the operator's control surface (Admin). Every screen is judged against its
mock in `docs/design/*.dc.html` and the design system in `docs/design/design-system.md`.

- **Code:** `web/src/` (`App.tsx`, `explore/`, `admin/`, `about/`, `ui/`, `lib/`, `styles/`)
- **Tests:** `web/tests/` (Vitest and Testing Library); `npm test`, `npm run typecheck`
- **Tasks:** phase 6 (`P6-*`) and the site reviews (`B-94`–`B-102`, `B-119`–`B-126`)

## Surfaces

| Path | Page | Shows | Design |
|---|---|---|---|
| `/` | `ExplorePage` | Find (hybrid search), the answer view, entry points, corpus counts, what is new since the last visit | `ExploreLanding.dc.html`, `Explore.dc.html` |
| `/sources/:id` | `SourcePage` | One source: metadata, passages in reading order, figures | `Explore.dc.html` |
| `/nodes/:id` | `NodePage` | A node's neighbourhood (graph, table or matrix view), its evidence, notes | `Main.dc.html` |
| `/map` | `MapPage` | Areas and topics as nested circles, zoom by level, bridges, steering | `Main.dc.html` |
| `/gaps` | `GapsPage` | The ranked list of gaps with their actions | `Main.dc.html` |
| `/growth` | `GrowthPage` | How the corpus grew: pages per topic per day, passages, sites, graph, map; 7 days, 30 days or all, filterable by topic | `CorpusGrowth.dc.html` |
| `/contested` | `ContestedPage` | Pairs of claims that disagree | `ContestedMark.dc.html` |
| `/admin/*` | `AdminPage` | Topic weights, pins and boosts, proposals, seeds, agent registry, gazetteer approvals, enrichment queue, run history, fetch policy, crawl health, assistant access (MCP tokens), display (time zone) | `AdminLight.dc.html`, `AdminAssistantAccess.dc.html`, `AdminDisplay.dc.html` |
| `/about` | `AboutPage` | What the project is | `About.dc.html` |

The **Ask panel** (`explore/AskPanel.tsx`) is a toggle on every reading surface except Admin.
See [ask-the-graph.md](ask-the-graph.md).

## How it is organised

- `lib/api.ts` holds every wire type and fetch helper. `web/tests/api.test.ts` compares its
  field lists with the API's Pydantic schemas, so a field added on the server and forgotten
  here fails a test.
- `lib/route.ts` parses the path into a typed `Route`; there is no router library.
- `ui/` holds shared pieces (top bar, icons, the tier chip, the contested mark).
  `styles/tokens.css` holds the design tokens; a test checks that colours are used only
  through tokens, in tests too.
- The node graph uses graphology and sigma; the 3D corpus map uses three.js.

## Conventions

- Times are formatted only through `lib/time.ts`, in the deployment's display zone
  (ADR 0009). A test fails on `getHours()`, UTC getters, or sliced ISO strings anywhere else.
- TSDoc on exported components, hooks and functions (AGENTS.md, "Code and comment
  standards").
- Pages own their width (`P6-27`); `main` does not force a column.
- Linted by oxlint and formatted by Prettier (`B-142`, ADR 0012); both run in `make lint`.
  The hooks rules are errors. Where an effect deliberately leaves a dependency out (a scene
  built once per mount, a callback recreated every render), the comment above the
  `eslint-disable-next-line` says why.
- Before calling a UI change done, screenshot it and compare it with the mock.

## Design choices

### Phone widths

At 390px the top bar holds the mark, five sections, the bell and settings, so below `sm` it
drops its dividers and tightens its gaps; above, it is as the mocks draw it. Two traps made
every page scroll sideways on a phone:

- A table that scrolls inside its card still widened the page through an `sr-only` header
  cell: `sr-only` is absolutely positioned, and with no positioned ancestor its containing
  block is the page. `TableCard` is `relative` for that reason.
- A flex row inside a wrapping toolbar does not wrap with it; the Map's shade and level
  controls wrap themselves.

There is no phone mock; a page is checked at 390px by measuring
`document.documentElement.scrollWidth` against the window in a headless browser.

**The Ask button steps aside while a reader scrolls down** (`B-156`). On a phone it floats over
the content rather than beside it: at 390px it covered Find's term box, a source page's "Write
a note" and Growth's table toggle as they scrolled past, and the last lines of a short screen's
results. Below `sm` it slides away on a scroll down and returns on any scroll up or at the top;
it listens in the capture phase because some pages (the node workspace) scroll an inner
element, not the window. Checked by scrolling each page at 390×844 and 390×667 in a headless
browser and testing every control's box against the button's.

### The shell

§12.6 splits the interface into Explore and Admin, the same split the API draws:
`/api/explore/*` reads through the read-only role, `/api/admin/*` writes. Keeping the seam in
the same place means a reader always knows whether the screen can change anything, which is also
why the bar is opaque on Admin and translucent everywhere else (§5: "translucency means floating
above your work; opacity means this is the thing you are reading").

**Pages own their width** (`P6-27`). `main` used to force every screen into a 48rem reading
column, which drew the map and graph at thumbnail size and made a dense Explore surface
impossible; now `main` is the full width under the bar and each page sets its measure. A source
or node page marks Explore in the nav, since marking nothing two clicks into the corpus would
say the bar does not know where the reader is. About marks nothing: it is reached outside the
nav, and a tab for it would claim a section it is not in.

The top bar is 54px and drawn identically across `ExploreLanding`, `Explore`, `AdminLight` and
`Notifications`: lockup, a divider, the nav as tab pills, then, pushed right, the **top-right
cluster**, "designed once, used on every screen, in this order": status pill · divider ·
notifications · settings. No greeting (§5): one person owns the system, and a welcome line would
address them on behalf of nobody. Pages that need controls in the bar (the graph view's search
and view switcher) render them through `<InTopBar>`, which portals into the space between nav and
cluster, so the bar stays one component from `App` rather than a copy per page. The cluster's
three reads fail independently: a closed Admin (503 on `/api/admin/runs`) still leaves queue
depth and notifications worth showing.

### About

design-system.md §1: the tagline "appears on the About screen and nowhere else"; repeated, it
becomes decoration. `AboutPage` is the one component holding it, and `tests/about.test.tsx`
checks no other source file does. Everything on it that could go stale is read, not written:
the build is the version the bundle was built at (`package.json`, mirroring `VERSION`), and the
topics and counts come from `/api/explore/stats`. The two statements not read (public sources
only, self-hosted) are properties of the design, not of a deployment.

### The contested mark

**†** (U+2020) is the typographic mark for "a qualifying note is attached to this", which is
what contested means; it exists in both type families, so it needs no icon system and inherits
weight and colour from the type around it. §6 states the rule and its test together: the dagger
is always paired with the brass tint and the tint never appears without it, *"strip the colour
and the reading must survive"*; `tests/contested.test.tsx` is that test. It matters beyond
aesthetics: §9 makes contradiction a result, and contested nodes the highest-value ones, so a
reader who cannot see them (colour-blind, printed page, sunlight) loses the finding, not the
decoration. The dagger is rendered as text rather than a pseudo-element or background image, so
it survives copying, printing, reading aloud and a page without CSS.

### Tier chips

Two rules from the design system meet in `ui/Tier.tsx`. **"A tier is a bordered mono chip, not a
coloured dot" (§5)**: state is carried by form, since a dot says nothing to a reader who cannot
tell its hue, and nothing in print. **"There is deliberately no green and no red in the palette.
Nothing in this system is pass/fail, and colour must not imply a verdict" (§2)**: every tier chip
is drawn alike and differs only in text. A palette ranking tiers (peer-reviewed green, informal
red) would assert a credibility judgement the system refuses to make: §8 extracts funder, stance
and hedging and scores nothing. So `TierChip` takes no variant, tone or colour prop; there is
nowhere to put a verdict. It is metadata beside a quote, so it has no fill, since a filled chip
would outweigh the passage. Labels are not a ranking and are not abbreviated to initials. The
tier list mirrors the database enum and `tests/tier.test.ts` compares them, because a tier added
to Postgres and not to the list renders as an unstyled fallback nobody notices. Data chips
(an extractor, a chunk id, the search mode) share the hairline form but are lower case: they are
values, and capitals would turn a value into a heading.

### Icons and the mark

**The mark** (§1) is a globe outline crossed by one meridian, with three nodes on it (zenith,
hub, base) and the arc between them the edge; one straight edge leaves the hub on a bearing and
ends on the rim: the graph continues past the reference line. The meridian stroke is heavier
than the globe's, and that is the idea, not a detail: the reference line is the subject and the
sphere is context; equalise them and it becomes a globe with a line on it. The app once had no
mark; the README's exported PNGs cannot follow the reader's theme, scale without resampling, or
honour §1's optical-size rule, which is why it is a stroke drawing from published geometry. The
numbers are transcribed from §1's tables, not eyeballed from the artboards, and
`tests/mark.test.tsx` checks the bounding box against the published one, which is how a
fat-fingered coordinate is caught. Below 32px the compact mark is used: at small sizes the
bearing edge and its node collapse into the rim and the meridian closes up, so the compact
variant drops the edge and widens the meridian rather than shrinking an illegible drawing.
`monochrome` drops the accent, a supported state rather than a degradation: §1 requires the mark
to read without it ("a stroke drawing, not a colour composition") for print as 100% K and for a
knockout over unpredictable ground. In the lockup, the hairline rule is the height of the
wordmark's cap, not the mark's, which keeps the three elements reading as one line of type with a
drawing at its head.

**Icons** (§7) are path data only; `<Icon>` applies the 24-unit grid, both stroke weights and
the round terminals, so no icon can quietly stop matching the others (a set whose strokes
disagree reads as amateurish before anyone can say why), and `tests/ui.test.tsx` checks the
geometry. They are drawn in the mark's language, circles and arcs before rectangles, with
silhouette 1.6 against detail 1.35, the mark's own 2.8:2.4 meridian-to-globe ratio. Below 20px
detail strokes are dropped, the same optical-size rule as the compact mark: 1.35 units on a
24-unit grid is a third of a pixel at 16px and renders as a smudge, so every icon must read from
its silhouette alone. The set is transcribed from `docs/design/Icons.dc.html` (`P6-27`): the first
pass was drawn to the grid but not the artboard, and the difference showed beside every mock (a
gauge where the design has sliders, a plain square where it has a grid with one cell lit). Node
glyphs are glyphs, not icons: they appear at node scale and beside chips, never as controls, and
must stay in step with the spec's node types.

### The API client

`lib/api.ts` has **no base URL**: no `API_BASE` constant, and nothing reads an environment
variable. Every request is `/api/...`, which the Vite proxy forwards in development and nginx
or `cloudflared` fronts in production. A base URL would be a value that has to be right per
environment, and the way it fails is a build shipped pointing at someone's laptop.

**The wire types are kept honest by two enforced links.** TypeScript types vanish at runtime,
so nothing compares them with the pydantic DTOs directly. Instead `tsc` ties each `interface`
to its `*_FIELDS` runtime list through the `Expect<Equal<...>>` assertions (a field added to
the type and not the list fails the build), and `tests/api.test.ts` ties each list to the
pydantic model it mirrors, parsed out of `meridian_core/schemas/` (a field added in Python and
not here fails the test). Neither link can be dropped without something going red. The
`SOURCE_TIER` drift test in `tests/ui.test.tsx` is the same device; it exists because a tier
added to Postgres and not to the UI renders as a raw enum value and nobody notices.

**Calendar dates stay strings.** `publication_date` arrives as `"2025-12-03"` and is kept as
written: `new Date("2025-12-03")` parses as UTC midnight, which in any negative offset renders
as the 2nd, a citation dated to the wrong day on some readers' machines only. A published date
is a fact from a document, not an instant (ADR 0009).

**Refusals become sentences** (`ApiError`). FastAPI's `detail` is a string for a raised
`HTTPException` and an array of per-field objects for a validation failure; rendering the
second directly gives `[object Object]`, an error that names neither cause nor remedy, so both
are normalised once in the client.

Smaller rules carried by the wire types: `page_unit` is `null` when a source's media type was
never recorded, rather than a default that mislabels pages as offsets or the reverse (`P2-18`;
before it, a citation could say only "page/offset"). A hit's `age_days` and `decay` are
published rather than applied silently, since a result quietly demoted is one the reader
cannot audit (`P2-20`). A figure's `raw_url` is `null` when the deployment does not serve raw
files, because a caption with a dead link costs the reader a click to find out.

### Query strings

List filters repeat their key, `source_tier=government&source_tier=press`, which is what
FastAPI's `list[...] | None` expects. A comma-joined value arrives as one tier named
"government,press" and is refused as an unknown literal. Empty arrays are left out rather than
sent: "no tier filter" and "a filter matching no tiers" are different requests, and a cleared
filter control means the first.

### Explore and Admin prefixes

The client is split by prefix because the prefix is the role boundary (§12.6): everything under
`/api/admin` writes through `meridian_rw`, and the API refuses it unless callers are identified.
A 503 there is not an outage but an instance that has not been told who may change things, and
the message says so.

Saved views and notes are read under `/api/explore` and written under `/api/admin`. Saved views
are shared state with no per-viewer scoping, so a guest on a shared instance can open the
owner's views and cannot add to them. Notes have a sharper reason: a note is the one thing that
reads as the owner's own thinking, so a shared instance shows the owner's notes and offers no
way to add to them. The client never sends `produced_by`: `AnnotationCreate` has no such field
and forbids extra keys, so authorship is the server's to assign, and a helper offering the
argument would suggest otherwise. Rewriting a note's `about` replaces its nodes rather than
adding to them, because re-reading changes what a note is about, and a note that accumulated
every node it was pointed at would attach to a whole search history.

Some clients have their own module (`lib/gaps.ts`, `lib/proposals.ts`, `lib/chat.ts`,
`lib/areas.ts`) rather than more of `api.ts`. They use the same error shape (`ApiError`,
`describeDetail`), so a refusal reads the same everywhere, and each has its own field-list
drift test.

### Routing

`lib/route.ts` is a hand-rolled path router, deliberately not `react-router`. It began with two
routes, where a dependency would have been an upgrade path inherited for the life of the
project for very little. What matters is that **URLs are real**: a source page can be linked,
bookmarked and pasted into a citation, which is the point of a corpus that insists everything
be checkable. It now carries nine routes, and `P6-20` stands: when nested layouts or
route-level data loading arrive it should be replaced rather than grown.

Modified clicks (⌘, Ctrl, the middle button) on internal links are left to the browser: the
reader is asking for a new tab, and swallowing that is the most irritating thing a hand-rolled
router can do.

### Keyboard

⌘K (Ctrl+K off a Mac; either is accepted, since which one a reader presses is a fact about the
keyboard, not the operating system) works from anywhere: on Admin, a source page or the Map it
goes to Explore and focuses the search field. A shortcut that worked only where the field was
already visible would be a shortcut for pressing Tab. The field appears a render or more after
the navigation (React batches, and a page may load its data first), so focusing waits a few
frames rather than one tick.

### Theme

Three states, not two (`P6-17`). With no explicit choice the reader gets their system's
preference, not dark: a machine in light mode that never touched the toggle gets light. So
`system` is the default and is expressed by the **absence** of `data-theme`, which lets the
`prefers-color-scheme` block in `tokens.css` apply. Setting `data-theme="system"` instead would
match neither the light block nor the dark one and leave every reader on the dark palette
whatever their machine says. All the CSS lives in `tokens.css`; `lib/theme.ts` only decides the
state and stamps the root element.

### Browser storage

Every `localStorage` access is guarded. In a private window or with site data blocked,
`localStorage` does not merely return null: reading the property throws, and an uncaught throw
during the first render takes the whole page with it. A theme or a badge stamp is a preference
and must never stop the app loading; a preference that cannot be stored still holds for the
tab, and a badge that cannot be cleared is still a badge.

### Since last visit

The landing carries a delta (`P6-11`, §12.5), because "what arrived while I was away" is the
question someone opens it with, and a total answers a different one.

**When the stamp advances is the whole design.** Writing it on render makes the delta vanish
the moment it is seen: "12 new", then "0 new" on the next render, which is worse than not
offering it. So the previous stamp is read *once* per session and the new one written at once,
in a single call (`openSession`): the delta means "since you were last here", and stays stable
while the page is read. Reading and writing in two places is how the stamp ends up advanced
before it is read, and the symptom, a delta that is always zero, looks exactly like a corpus
where nothing happened.

The stamp is per viewer and per browser, and that is correct: two people looking at the same
instance have different answers to "what is new to me", so it belongs in `localStorage`, not
in a table.

### The status pill and bell

The top-right cluster (`P6-27`, design-system.md §5, §8) answers three questions, each with an
honest "don't know".

- **The status pill** shows queue depth, last hour's fetch success, and whether the last run
  failed. The dot is cyan, brass when a run failed, and *hollow* when run history could not be
  read: on an instance where Admin is closed, `/api/admin/runs` answers 503, and a cyan dot
  there would claim a health check that never happened. A run still in progress is skipped,
  not counted as healthy, since a failure followed by a run in progress is a failure nobody
  has yet seen succeeded past; `deferred` is a run that chose to wait, not a failure. The
  fetch figure says `idle` when nothing was attempted, because 0% would read as every fetch
  failing. The time shown is when the counts were taken, not the wall clock: a current time
  over hour-old counts would vouch for them.
- **A stalled crawl shows too** (`B-156`). The pill once reported synthesis only: it stayed
  cyan while Crawl health said "Stalled — no fetch in 32 min". `/api/explore/progress` now
  carries the same liveness verdict as `/crawl-health` (one function, `crawlhealth.liveness`),
  and the dot is the worse of the two: brass for a failed run or a stalled crawl, with
  "· crawl stalled" beside "· run failed" in words. The tooltip says how long it has been quiet
  and how many pages are ready; a crawl backing off or with nothing queued is not a stall.
- **The bell** counts notifications that arrived since this reader last opened the panel,
  rather than the server's `unread`, which nothing in the product clears and so only grows.
  Opening the panel is the acknowledgement; §8 has the panel filter by type, not read state,
  so the stamp only ever moves the badge.
- **Notification kinds** are §8's three (jobs, approvals, alerts) over the types the database
  records. The map mirrors `NOTIFICATION_TYPE` in `models/runs.py` and `tests/shell.test.tsx`
  compares them, because a type in one and not the other would fall into no filter: the one
  way a notification can be recorded and never seen. Unknown types show as jobs rather than
  being dropped.
- **Each row and the pill lead where the thing is decided** (`B-181`). Every approval, alert
  and run summary once linked to `/admin`, which opens on Topic weights, so the bell's commonest
  click landed on an unrelated screen. Now: seed and gazetteer proposals open their sections, a
  run summary the run log, an alert the section for its condition (`ALERT_SECTION`, read against
  the keys `alerts.py` raises; the disk has no page and gets no action), and a possible
  duplicate the node it created, since no screen decides a merge yet (`B-182`). Every type the
  database allows has an entry in `ACTION_FOR_TYPE`, and a test fails until a new one is given
  a place. The pill asks "is the crawl all right?", so it opens Crawl health, or the run log
  when the last run failed.

### Readable passages

`lib/readable.ts` is the display's own view of passage text and changes nothing but what is
drawn. Pages are converted to Markdown at ingestion, and about a quarter of stored passages
still carry its syntax: links, footnote markers like `[[30](https://…#ref-30)]`, images, bold
and `#` headings. The stored text stays as it is, since everything is re-derived from it
(§2.4), and embedding already reads a view without URLs (`B-49`). A short line ending in an
ellipsis at the very start is a widget's prompt ("Search…"), not what the page says (`B-98`);
only there, and only short, so a sentence left unfinished mid-passage is kept.

A passage is cut at a length, not at a link, so about one in a hundred begins inside a link's
words (`… 2019)](https://…)`) and nearly as many end inside its address. Both keep the words
(`B-164`), but only when the address plainly is one (a scheme, `www.`, a path or a fragment),
so an ordinary `]` before a parenthesis is left alone. The same test admits link words wrapped
onto one more line. Escaped brackets (`\[47\]`, the converter's footnote markers) read as
brackets. Link "cards" whose words run over several lines are still shown as stored.

Text extracted from a PDF carries its column width: a line ends wherever the page did. As-is it
is a ragged column; with every newline kept it cannot be skimmed. But web pages break lines on
purpose (a label, then its value), and joining those would run them together. So a passage
counts as wrapped only when most of its breaks fall mid-sentence, and then only breaks that
plainly continue are joined: the next line in lower case, or this one ending on a comma or on
a word such as "of" or "the" that no sentence ends with. A break after a full stop, before a
capitalised line, a list item or a blank line stays, and a word hyphenated across a break is
rejoined. The rule is conservative on purpose; see the handover before loosening it.

Some PDFs are set in fonts that hand extractors Adobe's private-use small capitals and figures
(U+F721–U+F77E, each an ASCII character plus 0xF700), so a title or passage reads as boxes.
`meridian_core.titles.plain_letters` maps them back at extraction, for titles and PDF pages;
`plainLetters` does the same in `readable()` and in result titles for what was stored before.
Other private-use characters (icon fonts) are left alone, having no letter to map to.

## The surfaces in detail

### Explore and Find

**The landing is §8's default state** (`P6-27`), not a focus+expand view and never the whole
graph: the lockup above a centred, wide search field; four counts; the three entry points as
parallel cards; a short "where you were". Behind it, the mark's circle-and-meridian, scaled up,
cropped and fixed so it also sits under the translucent top bar, transcribed from the artboard's
1440 × 900 frame. It is decoration: hidden from assistive technology, never interactive, and
nothing on it is corpus data.

**The degraded flag is rendered, not logged.** Without an embedder only the lexical arm runs,
and every response says so. A reader who searches a topic, sees nothing, and is not told that
meaning-based matching was off will conclude the corpus lacks the topic: false, costly, and the
confusion §12.5 exists to prevent. So the notice is loudest when there are no hits (bordered,
full ink), because a degraded search that returned nothing makes a claim about the corpus it is
not entitled to make; with hits on screen it is a mono caveat line above the list.

**Searching happens on submit**, not per keystroke: each query is a fused ranking over two arms
and a candidate pool, and one per character spends the machine on queries nobody finished
typing. A superseded request is aborted rather than left to land out of order.

The summary line counts per arm in a reader's words ("matched the words", "near in meaning",
`P6-44`): "lexical" and "vector" are how it was done, not what it means. The older "20 of N
candidates" took N from the lexical arm alone and read "20 of 0" whenever meaning-based
matching found everything, a sentence contradicting itself on a correct result. Each arm stops
at the candidate pool, so a count that reached it is written `100+`: "100 matched the words"
read as an exact count when it was a ceiling.

**`/?q=…` opens with that search run**, so a search can be linked and shared. Gaps' "Search it
in Find" (`P6-36`) and the Map's "Open in Find" (`P6-34`) link here. Every filter travels in the
link (`B-173`, `lib/find.ts`): `topic=` and `place=` and `tier=` (each repeated), `topic_match=all`,
`from=` and `to=` (years), and `view=answer|passages` once the reader has picked a tab, so a
shared link opens as the sender saw it. The Map's topic web (`B-72`) sends its intersections with
`topic_match=all`. Filters without a query are set up and wait for the words; the landing says
so in one line ("Searching within: …", with a way to clear). What a hand-edited link cannot mean
(an unknown tier, a two-digit year, a view called anything else) is dropped rather than searched
for, and a range typed backwards is read as the same span.

One grammar serves the link, the search request and a saved view. A saved view stores the
filters under `SearchFilters`' own names, because the server validates against that model;
before `B-173` it stored topics only, so reopening a view narrowed by place or anything else
silently widened it. `tests/save-view.test.tsx` checks every key against the dataclass and that
every filter survives a save and reopen.

### The search field

§8 puts two unusual things in the control. **The `words + meaning` marker**: retrieval is two
arms fused by reciprocal rank (`P2-06`), one matching the words and one finding passages near in
meaning, and a words-only result set and a fused one fail in different ways, so the mode is
named, the same instinct as putting the tier on every hit. **"Filters narrow what is searched,
not what is shown"**: it is the difference between "twenty government sources" and "whatever
survived filtering the top twenty", and a reader who assumes the second will mistrust a correct
result set. The field holds no state; the query belongs to the page.

Both were first written in the implementation's terms (`hybrid`, "filters apply before the
vector search") and are reworded in a reader's (`B-99`, [ADR 0013](../adr/0013-find-says-what-search-does-in-plain-words.md)),
matching the summary line's "matched the words · near in meaning". They state what search does,
not how; a search that ran on words alone still says so in the results, through the degraded
notice, since the marker describes the design and the notice describes this answer. Tests hold
both free of `hybrid`, `vector`, `lexical` and `embedding`.

### Search results

No artboard draws the list. Its form comes from the nearest thing that does, the supporting
chunks in the Explore artboard's detail panel: one bordered surface, rows divided by
hairlines, the passage first and one mono line of provenance beneath it (domain in cyan, tier
as a bordered chip, date, position).

**Every hit shows where it came from, and that is the feature.** §2 principle 3 is that nothing
is assertable without a citation that can be followed back to a file, and this is not a RAG
chatbot. A list that left the source to a hover or a detail panel would make the citation
optional in practice. **The date says "no date"** rather than nothing: an undated source and
one whose date was never extracted look identical as a blank, and §9 makes staleness a
first-class signal.

Passages stay in Archivo at reading size rather than the artboard's mono italic quotes: there, a
quote is two lines of evidence beside a node; here it is the thing being read. A long passage is
clamped to eight lines with CSS, so find-in-page, copy and screen readers still get the
verbatim chunk; a list where one extraction-damaged chunk fills three screens is a list nobody
reads past the first hit.

**More passages** (`B-174`). The list once stopped at twenty with no way on, though the route
pages by `offset`. A button under the list reads the next twenty of the same search (words and
every filter), appended and keyed by passage so a ranking that shifted between requests never
shows one twice. A page is never asked for past the candidate pool, which the route refuses
because fusion never ranked anything beyond it. Once a reader has paged to the end, one line
says it is the end of what this search ranked, not of the corpus.

### The filter rail

The results page has the Explore artboard's left rail (`B-173`, `FindRail.tsx`): topic, place,
source type, publication years and saved views, as ticked rows like the graph workspace's rail,
whose `Row` and `Section` it reuses. Until `B-173` the filters were rows of chips above the
results and on the landing (the landing artboard draws none), and source type and dates, which
the search route had always taken, had no control. An intermediate reader's first narrowing
("government and peer-reviewed only", "since 2018") was impossible without editing a URL.

- **The landing has no rail.** It is the default state (§8) and stays a search field and three
  doors; filters set by a link show as one line there.
- **Below `lg`** the rail folds behind a "Filters · n" button beside the Answer/Passages tabs,
  so on a phone the results are still the first thing under the field. The field comes first
  in the document, so it is first in tab order too.
- **Every change re-runs the search** and replaces the history entry rather than adding one, or
  each tick would be a step of Back.
- **Places fold after eight**, and a chosen place stays visible past the fold so it can be
  unticked. "All at once" appears only when two topics are chosen.
- **Years commit on Enter or on leaving the box**, so typing "20" does not search for the year
  20. Something that is not a plausible year is put back, not applied.
- **The Answer tab gets the same filters**: `/api/explore/answer` takes the dates too now, and
  an integration test compares the two routes' signatures so a filter added to one is not
  silently ignored by the other.

**The caveats are the interesting part, and each is said only while it applies.** A source never
examined for topics or places carries no labels, and a filter on either excludes it: correct,
since nothing established that it belongs, and invisible, since a reader who narrows and sees
three results cannot know three hundred documents were never examined. So the rail says so under
the section while it narrows. A year does the same to every undated document, which on a web
crawl is many of them, so the Published section says that too while a year is set. Selected
rows carry the graph accent and nothing else: §2's palette has no green or red, and colour must
not imply a verdict.

Not yet: the artboard's per-value counts. They need a facet query over the search's candidate
pool, which the graph workspace has and Find does not.

### Entry points

Search, Coverage and Contested are parallel cards on purpose. Search is the one everyone reaches
for, and giving it the whole screen would make the other two features one has to know about.
§12.3 notes that the coverage view is the one people skip and then miss, because absence is
what gap analysis acts on and node-link diagrams are bad at showing it; §9 makes contested pairs
the highest-value nodes in the graph. Neither survives being a menu item. The copy states what
each view does in §4's register: short declaratives, concrete nouns, no promises.

Coverage opens Gaps (`P6-36`, `P6-42`): the ranked list of what the corpus cannot answer is
where absence became visible, and a card saying "not built" beside it was a dead end on the
first screen (`P6-44`). A card that has no action is drawn as a card, not a button, and a card
that cannot be opened says why. Each card may carry one mono line of a measured fact; where none
is supplied the line is absent rather than invented. None is supplied yet: the artboard's "9
thin cells · 3 stale" could now come from Gaps, but is not wired.

### Corpus counts

§8's four counts (documents, nodes, edges, contested†) in four equal cells, mono numerals with
tabular figures (§3) so they scan as a column of numbers. **The absent value is a designed
state**: null counts render an em dash per figure, because zeros are a lie that reads as a true
statement about an empty corpus, and a reader cannot tell "nothing has been crawled" from "the
API did not answer". `contested` carries the dagger as text beside its brass figure, because
§6's rule is that the tint never appears without the mark, and a count is where a reader
colour-blind to brass would otherwise lose the distinction. Both only above zero.

"Concepts" counts nodes that are neither notes nor merged into another node
(`stats.live_entities`), the rule Growth uses, so the landing and Growth agree. **Sources too**
(`B-156`): the landing, About and the since-your-last-visit delta count `stats.kept_sources`,
documents that are neither junk nor a copy of another, as Growth does. They had counted every
source row, a fifth more than Growth, and a reader moving between the two saw two answers to
"how much is here". The raw row count (`sources`) stays in the payload for operator surfaces,
where fetched-but-junk matters.

### Where you were

§8's list of saved views and recent nodes as one list: name, kind, when. Short by design:
§12.5 optimises for legibility over volume because reading time is the bottleneck. **Empty is
a first-class state**: a reader with nothing here has not used the system yet, while one who
saved three views last week and sees an empty list has a bug. Rendering nothing makes the two
indistinguishable, so the empty state names the condition, and says which half is empty when
only one is.

The since-last-visit line sits at its head, because §5 wants "a single mono line of deltas
since their last visit, set with the other counts, not as a separate widget"; its figures are
brighter than the words around them, since they are what a returning eye looks for. It has
three states: `null` is a first visit, so there is no "since" and claiming one would invent
history; `0` is a finding worth stating, because a silent line reads as a failed load; any
other number is the delta. The artboard's line also carries edges and newly contested pairs;
the API's delta covers sources and passages only (`P6-11`), so those are absent rather than
shown as zeros.


**It leads back** (`B-177`). Until `v0.166.7` the landing passed this list an empty set of
nodes, though the node page had recorded every node opened in `localStorage` since `P6-01`, so
the half of the list the design draws as "Silver Zone · NODE · Yesterday" never appeared.
Up to three recent nodes now show, each with when it was opened. Saved views open through one
function, `hrefForView` (`explore/views.ts`): a node view on its node with its filters, a
search view on Find with its words and filters. Before it, a node view was a dead click on the
landing (it has no words to search for), and a search view opened from the node workspace's
rail went to an empty landing.
### Notes

§12.5 asks for "my own notes and edges, tagged as mine" and adds the rule that shapes the
component: **build the affordance early or it won't get used.** §12.6 puts annotation in
Explore, not Admin, because it is part of reading, and a control surface is somewhere nobody
goes mid-thought.

- **The composer is offered wherever reading happens, and never hidden.** A saved view needs a
  query; a note is the opposite. The thought that has not found its node yet is the one the
  corpus cannot re-derive, and the backend accepts a note with no target for that reason. A
  composer that appeared only once something was selected would refuse the note most worth
  keeping.
- **It says what it will carry before it is written**: in words, what the note will attach to
  and how many passages it will cite. A note whose thread back into the corpus was silently
  dropped still looks like a note, and the reader finds out months later on clicking the
  citation: the one failure this layer cannot afford (§2 principle 3).
- **Nothing claims authorship** (see [Explore and Admin prefixes](#explore-and-admin-prefixes)):
  the layer is only worth having while nothing else can write it.

The notes panel sits on the landing rather than behind a menu: over months this becomes the
highest-quality layer in the system, and a layer nobody passes is one nobody adds to. Its export
is a plain link, not a fetch-and-blob: §12.5 asks for Markdown so material is not trapped in a
bespoke store, and a link can be copied, opened in a tab or fetched with curl.

### Saved views

§12.5 asks for "a filter set plus focus node, named and re-openable". The affordance must be
offered at the moment a view is worth saving, while looking at results, not from a menu: a
saved-views feature nobody reaches is the same as none. It appears only when there is a search,
because a disabled control always on screen teaches the reader to stop seeing it. Saving is a
write, so it is refused while Admin is closed, and the API's message is shown as written
(`P6-13`): views are shared state with no per-viewer scoping.


**A view is checked against the page that reopens it** (`B-193`). A search view's filters are
validated against `SearchFilters` and a node view's against `GraphFilters`, the graph
workspace's own model. Until `v0.166.8` both were checked against `SearchFilters`, while the
node workspace stored its filters under its URL's names (`topic`, `tier`), so every node view
saved with a filter was refused with a 422 and only unfiltered ones could be kept. A node view
now stores `GraphFilters`' names (`viewRecordOf`); the workspace reads either spelling, and a
web test reads `GraphFilters` from the Python so the two cannot drift again.
### Source pages

The first screen where the corpus reads as documents rather than results (`P6-14`, `P6-15`):
chunks in document order, figures with captions, and §12.5's exports, all of which had
endpoints and nowhere to be shown. **Provenance is the page, not a footnote**: tier, date, DOI
and how the text was extracted are in the header, because "what is this and how do I know" is
the question a reader arrives with, and `extractor` (`P1-44`) separates a document that had no
text from one whose extractor fell over.

**The corpus's bookkeeping is folded away** (`B-156`). The breadcrumb names the site, not the row
("source 46231"); the source id, the extractor and the language (and whether it was read from
the text) sit under a "record details" disclosure. A passage shows its page when its document is
paginated (`page_unit`, now on the source as on a search hit) and nothing otherwise: a character
offset is bookkeeping, not a citation. The chunk id and offset stay on the position's tooltip,
and a near-duplicate says "a copy of an earlier passage" with the id on hover. Result cards
follow the same rule: "page 12", never "offset 0". **Annotation lives here** (`P6-05`), since this is where
reading happens; ticking passages puts the citations on a note without copying chunk ids by
hand, the version of the feature that would not get used.


**A passage link opens on the passage** (`B-178`). Every link from a passage (Find's results,
the answer page, the neighbourhood, node evidence, the Map's theme card, Ask's citations)
carries `?passage=<chunk id>`, and the page asks `/sources/{id}/chunks?around=` for a window
starting three passages before it, marks it (`aria-current`, the accent rule) and scrolls it to
the centre once. Before, every link opened page one and the page read twenty passages and
stopped: an audit followed six hits and two were not on the page at all (one on page 24 of a
source whose page ended at page 9, under a figures list running to page 54). The window now
grows both ways ("Earlier passages", "Later passages"), the count says which passages are shown
("41–60, more after"), and a link to a passage the source no longer holds as live text says so
instead of marking nothing.

**Citing from the page** (`B-179`). Each passage has "copy citation" (`lib/cite.ts`): the
words, cut at a word past 400 characters and stripped of the Markdown the page was stored in,
then title, publisher, date ("n.d." when there is none), the page when the source counts pages,
the original URL, and the link back to this passage here. A character offset is never written as
a page. The note checkbox, which was also labelled "cite", is "quote in note": the node page's
"Cite" copies, and two controls with one name doing different things taught the wrong one. The
original URL opens in a new tab, as it does from the results, and the breadcrumb leads back to
the results this tab came from ("results for “…”", from `sessionStorage`) rather than to an
empty landing; Back always worked, the breadcrumb did not.
### Figures

§12.5 asks for "thumbnails linked to the node, with page-accurate links to raw files". There are
no thumbnails: nothing downloads figure images (`P1-10`), so `thumbnail_path` is empty, and a
placeholder grid would be a promise the corpus cannot keep. What there is is the part §6.6 says
carries most of the value, the caption ("often the most information-dense sentence about the
figure"), so the caption is the content and the links make it checkable. There are two links:
`image_url` is the picture where the publisher has it, live and liable to move; `raw_url` is
this corpus's own copy at the caption's page, which is what §5.4 keeps raw files for, since link
rot is the binding reason and a local copy keeps a citation checkable years later.

<a id="figures-furniture"></a>**Logos and icons are not figures** (`B-156`). Extraction stores every
image a page carries, and on a sample of 40,000 stored figures about half were the page's chrome:
logos, social icons, close buttons, seals, banners. `figures.is_furniture` drops an inline
(`data:`) image, an SVG, an image whose path names such a thing, and one whose short label does
(a long label that mentions a logo is a description). A random sample of what it dropped held
nothing else, with one exception that shaped the rule: a messaging app's name in a path also
names photos sent through it, so it counts only in a label. The panel says how many it left out.

**A file name is not a caption.** `figures.reader_caption` withholds a label that is the image's
file name: one with a generated id ("0Wjcr3j8CU", digits in two places between letters), a long
digit run (a stock-library id; a YYYYMMDD date is allowed), a camera or stock word in a label of
three words or fewer, or the file's own name (a thumbnail's size suffix removed) when that also
looks like a name, a short label ending in a bare number or one run-together CamelCase word. The
same plain words as the file name are still a description ("happiness metrics"). Each refinement
came from a false hide in the sample; the rules withhold 1.3% of the kept figures' labels, and
the panel shows "Untitled image" with its links. `caption` and `alt_text` are returned as
extracted.

### The first hour

Production starts empty by design, so for the first hour there is nothing to search (`B-09`).
The options were shipping a small real crawl as a demo corpus, or making the first hour
legible; the second was taken, for reasons beyond effort. A snapshot of a real crawl is
third-party content, and whether it may be redistributed is the question §14.2 keeps apart
(`MERIDIAN_SERVE_RAW` defaults to off for that reason); shipping one in the repository would
answer it the other way without saying so. Synthetic fixtures do not resemble real extraction
output, so the first impression would be of a system that works better than it does. A queue
draining is a system working, and every number shown is true of the machine right now. Both
halves of the fetch rate are always shown: a crawl failing steadily and one succeeding steadily
have the same attempt count and want opposite reactions.

### The answer view

A question answered as evidence grouped by country. Nothing on it is written by a model: each
item is a source's own best passage, and each country's heading is a count of how many sources,
from how many publishers, of which kinds, and how recent. Coverage is a count against a stated
rule, shown in words beside the verdicts, not a judgement of any source (design-system §4).
**Thin is marked by form as well as colour**: a thin country's chip is dashed and says "thin";
the brass is the flag the Map uses for a weak area, and the word survives without it (§5). **A
country with no evidence is absent, and the page says so**, with a way to ask for any country by
name.

### Gaps

The operator's three screens are Find, Map and Gaps; Gaps says what to do next. Each row is a
finding in numbers (design-system §4, "state the absence") with an action through existing
machinery, so every click is logged in the steering audit and can be undone in Admin. The
visual language is the Coverage board's gap aside (brass mono label with the dagger, a plain
heading, the reason, "takes effect at the crawl's next claim · reversible in Admin") laid out as
a list on Admin's primitives. See [gaps.md](gaps.md).


**What a reader can ask comes first** (`B-184`). Ranked purely by severity, the first eleven
rows were the crawl's own search diagnostics (searches finding nothing new, or landing off
topic), and the question set, the most useful rows to a reader, began at twelve. "All" now lists
coverage, fields and the question set first, and folds "Search yield" below them with one line
saying what it is for: it changes what is fetched, not what is known. Rows are numbered within
the list shown. The tab is in the link (`?kind=`), so following "Search it in Find" and pressing
Back returns to it. An off-topic search gap says a description would steer it, so it links to
where descriptions are edited; a seed search keeps engine syntax (`!news`) on what is queued but
out of the words a reader edits, named beside the field instead. An action's result links to
where it can be undone, and its text names that section correctly (a boost said "Admin ›
Topics"; boosts are in Pins & boosts). A web test reads every "Admin › …" the route writes and
checks it is a section.
### Contested

§12.5's third entry point (`P6-10`). Every disagreement §9 marked, each once, newest first, both
sides side by side. Neither side is placed first on merit: the list has no node to arrive from,
so the order within a pair is the order the edges were written, and the two columns are drawn
alike. §9's "contradictions are signal, not error" means the page resolves nothing. The brass
tint always travels with the dagger (§6).

### The node workspace

`/nodes/{id}` is §12.2's focus + expand as the artboard draws it: filters left, canvas centre,
node panel right, filling the viewport under the top bar. The URL is the focus and the filters,
so any state a reader can see can be linked, the reason `P6-04` gave nodes URLs before there was
a canvas. **Refocusing is navigation**: clicking a neighbour moves to its URL, so the back
button is the breadcrumb's back and a refocused view can be pasted into a note. A note written
here is re-fetched rather than spliced into the panel, because the server decides what a note
ends up being.

### The node panel

§12.5 asks for "description, attribute tags with confidence, supporting chunks with source and
tier, contested edges, own annotations", and the artboard gives each a block, in that order, on
one opaque surface: citations, provenance and confidence do not go on glass over a live graph
(design-system.md §5). Three rules survive from the older panel because they are about honesty
rather than layout: **confidence is on the chip**, as a number, since a confidence a reader must
hover for is a claim rendered as a fact (§7); **a tag with no evidence says so**, because an
empty citation list is a data problem to show (§2 principle 3); and **nothing is invented to fill
a block**: with no description, note or contested pair, the block is absent or states the
absence. The artboard's sample prose is sample data, not a template.

### The neighbourhood panel

Beside Find's results (`P6-33`). **Two rings, drawn and listed apart**: the inner ring is what a
passage states a link to (solid cyan spokes); the outer is what merely reads alike (dashed,
fainter, smaller, and a separate list headed "no stated link"). Nothing puts the two in one list,
because resemblance shown beside a citation reads as a second citation; the client keeps them
as separate types for the same reason. **Sparse is stated, not hidden**: most terms have no
stated links yet, and the panel says so and still shows the outer ring. When no vector was
available the outer ring was not measured, and the panel says that rather than drawing an empty
ring that would read as "nothing is similar". The ring diagram uses the canvas's dark ground
and palette in both themes, like the graph workspace: it is a picture of the graph.

**Reader words** (`B-156`): concepts, not nodes; "very close", "close" or "related" rather than a
cosine to two decimals (0.85 and 0.75 are the lines), with the number on hover for an operator;
"no concept is close enough in meaning to list" rather than the floor.


**A whole question that names no concept keeps the panel quiet** (`B-176`). Asked "how do cities
make streets near schools safer for walking?", the panel opened with "No concept is called
'how do cities …?'" and offered concepts matched on single common words ("Pakistani cities",
"asymmetric price elasticities"). Both are about the question's wording, not its subject. For a
question (by `looksLikeQuestion`, the rule that picks the Answer tab) with no concept of that
name, the panel shows only when something is stated or near in meaning, and then without the
sentence and the word matches. A few search words keep the panel as it was: there, a concept
named by one of them is a fair suggestion.
### Notifications

The in-app counterpart to the Telegram digest, reading the same rows (`P6-08`): an alert is
recorded before it is delivered (`P5-07`), so a deployment with no bot token still has somewhere
to see what would have been sent. **Filterable by kind, not read state** (§8): a returning reader
asks "did anything need me?", not "what have I already seen?", and a read/unread split turns a
panel of findings into an inbox, which gets cleared without being read. That is why the
artboard's "Mark all read" is absent. The recorded types fold into the three kinds, and the
counts on the filter come from every type, not the filtered set: "Alerts 0" while three proposals
wait would hide the thing the reader came for.

### The Ask panel

`SynthesisPanel.dc.html` and `SynthesisToggle.dc.html` (`P6-06`, `P6-07`). **Translucent floats,
opaque reads** (design-system §4): the toggle is tinted over a blur; the panel, which carries
citations, is solid. **Selection is bound to the panel** (§12.4): a page puts what the reader is
looking at into `AskContext`, and it travels with the question as context the reader can remove.
**Answers cite nodes, then passages**: nodes as chips, passages as numbered links to their
source, and only those the server checked, since it drops every reference to something its model
was not given. There is no unread badge: an answer only arrives because the reader asked. See
[ask-the-graph.md](ask-the-graph.md).

## Admin in detail

### Admin

§12.6 splits the interface in two: Explore is where reading happens, Admin where configuration
changes (`P6-13`, `P6-28`). Most of Admin is CRUD over existing tables ("generated forms are
fine; effort belongs in Explore"), so it uses one plain, dense language everywhere: a left
section nav, a page header, bordered tables with mono heads, and on the steering pages a right
rail with the audit and the last runs. §5 says "Admin is generated CRUD and should stay plain";
plain is not unstyled, and the failure the operator named was every section inventing its own
card. So `admin/ui.tsx` has one page header, one card, one table, two buttons and a badge, and a
section needing something else is a question for the design, not a new class string. Every
colour is a token role, so the same markup reads on paper and on the dark palette.

- **Paper by default.** Design-system §2: light "exists for docs, Admin and print". With no
  explicit choice Admin is paper whatever the machine prefers, because the dark palette belongs
  to the canvas and Admin is a document; an explicit dark choice still wins, since overriding a
  preference somebody set is the one thing a theme control must never do.
- **A closed Admin is explained, not hidden.** These are the only routes that write, and the
  API refuses them (503) unless callers are identified or the instance is declared unexposed.
  The API's message is shown as written, because it names the environment variables that fix it.
- **Lists are refetched after each change, not patched in place.** One row's state is computed
  from all the others (a gazetteer verdict from every other approved term, a topic's share from
  every other active topic), so updating only the clicked row would show something that stopped
  being true the moment it was clicked.
- **Every section is a URL** (`/admin/<path>`), so it can be linked and the back button works.
  `route.ts` treats everything under `/admin/` as Admin, and the section is read from the path in
  `sections.ts`, so the router need not know Admin has sections.
- **The nav follows the mock's STEERING and SYSTEM groups**, mapped onto what exists. Crawl
  health sits beside Fetch policy because they are read together: an outcome mix full of
  refusals is answered by that domain's policy row. The mock's "Enrichment queue" is listed
  and marked unbuilt (`P7-07`; the table exists from `P0-09`) rather than dropped, because a nav
  that silently omitted it would read as the design having been forgotten.
- **Bare `/admin` opens on Topics** (`B-122`), as the artboard draws it. It used to open on the
  gazetteer queue, the weekly task (§5.6), until the queue grew to tens of thousands of
  harvested terms: a landing that opens on a backlog nobody will clear reads as the system being
  behind, while Topics is what steers the crawl. A fresh install, with nothing crawled, opens on
  Seeds instead. `sectionFromPath` returns null for bare `/admin`, so the page can tell "nobody
  chose" from a linked section.
- **The steering rail** keeps two answers beside the controls that produce them. The steering
  audit is §10.1's log: with two writers, the alternative is opening the screen in a month with
  no idea what moved anything, and most of what moved a weight is a change to a *different*
  topic, so those lines are shown too. Last runs show what the weights were steering, in
  counters rather than a verdict.

### Topic weights

§10's model in one line: attention is a weight vector over topics, and seeds are drawn in
proportion. The design problem is that **the number set is not always the number that
applies**: a floor lifts a starved topic, a ceiling caps a dominant one, a boost multiplies until
it expires, and pausing removes a topic from the pool. So the stored weight is on the row and
the slider, the share drawn is on the line beneath, with a note saying which mechanism made them
differ, only when one did (a note on every row would be noise).

**A slider stages; Apply commits.** Moving one topic re-normalises every other, and a slider
that wrote on release made that a side effect of dragging. A drag is a draft: the server
previews it (the write, rolled back), every row shows where it would land, and Revert or Apply
decides. One topic is staged at a time, because the server holds exactly one topic at the value
asked for and redistributes the rest; two staged topics would be two writes, and the second
would move the first. The slider is drawn rather than a styled native range, because the design
needs floor and ceiling marks a native control cannot carry; a real `<input type="range">` sits
transparently over the floor-to-ceiling span, so dragging, clicking and the arrow keys work and
it cannot reach a value the server would refuse.

**The re-normalisation is shown before it is committed** (design-system §8, "the dialog shows
the arithmetic before you commit"), for staged weights and for Add and Archive, whose cost lands
on *other* rows: a new topic takes its share from the rest, an archived one hands its share
back. The arithmetic is the server's: `after` comes from a preview route that runs the real
write and rolls it back, and only the Change column's subtraction is computed in the browser. A
client-side copy of clamp-and-redistribute would be right until the day the server's changed,
and then the dialog would confidently show numbers the button does not produce. Only drawing
topics appear: a paused topic is outside the pool on both sides, and "— → —" says nothing. A
pinned topic is reported as what happened to it rather than what the word promises, because the
server's re-normalisation redistributes over every active topic, pinned ones included; "pinned,
held" beside a number that moved would be the dialog lying on the one row somebody pinned to
protect. Pause is not a dialog: it is reversible in one click and the audit shows what it moved.

**Archive is not delete**, and the copy says so: §10.2 leaves nodes, edges and tags untouched,
and coming back is a status change. Archived topics move to a collapsed list with Restore.

A topic's description matters more than it looks: pages are labelled by how close they sit to
the topic's name, description and vocabulary, so a topic described in a sentence is found more
reliably than one known only by its slug. Changing it re-labels the corpus, and the line says so
before the change is made.

### Pins and boosts

The two steering controls that are not a weight. A pin is about *who* may move a weight (§10.1's
autonomous adjustment may not touch a pinned topic); a boost is about *how long*, a multiplier
with an expiry that removes itself. Both are on Topic weights too; here they are the whole page,
with expired boosts kept in view, since an expired boost on the row is the record of what was
boosted and until when.

**A boost belongs to a topic.** The mock's table has a Term column ("covered walkway 1.8×"),
and this system has no term boosts: §10's boost is a multiplier on one topic's weight with an
expiry, stored on the topic row. The column is left out rather than filled with the topic name
twice, which would suggest a finer control than exists. **A factor and an expiry, or neither**:
§10 makes decay what removes a boost ("steer back later without needing to remember"), so the
form cannot submit a factor without a date, and the server refuses one anyway. Ending a boost
early clears both.

### Proposals

The operator's rule (`P6-38`): the system proposes what to steer, and if nobody objects it is
steered that way. So the page leads with **when each proposal applies by itself**, because that
is what a reader is deciding against: a proposal left alone is a decision, and the screen says
so rather than reading like a queue awaiting approval. Each carries its reason in words, then
the numbers under it: the sentence is what a person reads, the numbers what they check it
against. Accept applies it now; Reject means it never applies, with an optional reason in the
steering audit. Proposals sit beside the controls they propose changes to.

### Agent registry

§11.3's case for a registry is that swapping models should be a config row, not a code change
(`P6-23`). This screen is where somebody reads that row and turns one on: the last step before
the graph has an edge, and the first that spends money. It sits beside Run history in spirit: a
run that deferred and the row that made it defer are one question asked twice.

- **The failure it exists to end is between the rows.** Routing picks an agent by task type, so
  a type no enabled, usable row declares is a stage that defers every run, while every row
  looks fine. `unserved` is therefore first on the screen, not a detail under the table.
- **A row says why it cannot be used, in routing's words.** An enabled agent whose key variable
  is unset looks like a working one until a run defers hours later. The server computes the
  reasons, because a client deriving its own would eventually disagree with the router that
  actually refuses, and in the bad direction: a green light for an agent nothing can reach.
- **No key, anywhere.** §11.11 keeps credentials out of the database because it is snapshotted
  off-device. The row names the variable; the screen reports only whether it is set where the
  API runs.
- **The model is the one string worth changing here.** A local server names its models however
  it was started, so the row pointing at it has to follow, and that is an edit, not a release.
  A `${VAR}` value is read from the environment where the call is made, as the endpoint is.

### Run history

§11.10 keeps the orchestrator's state in a plain table so a crash resumes rather than restarts;
this screen reads it. It was held back until runs did something (`P4-16`), because what a run
row needs to show is decided by what runs do, and that turned out to be mostly **stop for
reasons**, so the reason leads, in full, since it names the condition. §13.4 makes deferral
ordinary (a provider down, a budget unset, nothing enabled): `deferred` reads like an error and
is a scheduled retry, and someone assuming the first goes looking for an outage that is not
there. **Counters, not a verdict**: §11.9 compares cost and volume run on run, and a
"successful" column would hide the run that finished having written nothing, the common case
while stages are unbuilt and the interesting one once they are not. Zero written is shown as a
real answer.

### Gazetteer approvals

§5.6 ends "approve in the UI — a two-minute weekly task". The harvest files terms by the
thousand, so the two minutes are now the hard constraint: a dense table, a page at a time, a
selection decided in one request, and keys for single rows. **Every row says whether the matcher
will load it.** An approved term whose surface form another row already claims is withheld, so
it can read approved and never match anything, and nothing else in the system reports that. The
server computes the verdict against the whole approved set, and it is shown beside the decision
that caused it, which matters most in bulk, since approving forty at once is how two come to
claim the same wording. The reason is named rather than reduced to a boolean because each has a
different fix: a collision needs one of the two rows changed, ambiguity leaves the mention to the
resolver, and the other two are just where the row is in the queue. Approve and turn down are
not coloured: the palette has no green or red, and colour must not imply a verdict.

### Fetch policy

The one Admin screen whose changes reach somebody else's server (`P6-22`), and the copy is
written on that basis: these are decisions about how a machine behaves towards a stranger's
infrastructure, not preferences. **Three layers are kept visibly separate**: what is set here,
what the domain resolves to once the global row and file defaults merge under it, and what the
crawl learned by watching. Only the first can be changed on this screen, and the learned layer
has its own button, because clearing an observation and setting a policy are different acts.
**The safety guards are not here at all**, and their absence is stated: `block_private_addresses`,
`respect_robots` and the rest are deployment settings, and a form able to switch one off would
put it one click from the routine politeness controls.

### Crawl health

`FirstHour` covers the first hour; a run left alone for days needs something else (`P6-25`):
not "is it doing anything" but **when did it stop, and why**. So the screen leads with a verdict
in words, and everything under it is evidence. **The verdict is the server's**:
`crawlhealth.judge` decides it from the last attempt and the queue, and the page only phrases
it. A client that re-derived "stalled" would one day disagree with the alert that fired about
the same crawl, and then neither would be trusted. **A day, not an hour, with empty hours
drawn**: what is looked for is a gap, the hour fetching stopped, and a chart skipping empty
hours would close exactly that gap. No chart library: twenty-four stacked bars are a few
rectangles, and the package keeps its dependencies to React.


**The evidence in words, and a way on** (`B-197`). Outcomes and queue states were shown as the
database stores them (`robots_unreachable`, `rejected_duplicate`); they are now words, with the
stored value as the row's title, and `OUTCOME_WORDS` and `STATUS_WORDS` are read against the
model's enums by a test, so a new value must be given words. Queue states with nothing in them
are left out: `extracted` and `embedded` read 0 every day. Each of the busiest domains links to
its Fetch policy row (`/admin/fetch-policy?q=<domain>`), since "0 of 7 succeeded" is answered
there and the page had no links at all.
### The first run

§16 lists cold-start seed quality as a real risk, "worth spending an evening on", and that
evening had to be spent editing `config/seed_sources.yaml` before first boot, because the file is
read once (§13.1), by someone who does not yet know what belongs in it (`B-07`). **This is not a
wizard and gates nothing**: by the time anyone opens it the crawl has started (`make quickstart`
brings the worker up with everything else), and a screen implying otherwise would invite
removing a seed already fetched. What it offers is the window between a seed being queued and
reached, which per-domain rate limiting makes generous. So both halves are shown, what is still
changeable and what is underway, and the second is not styled as an error. Seeds is last in its
nav group, as the one section that stops mattering, and is where a fresh install opens.

## Failure modes and traps

- **Run the web suite before committing a schema change.** The API drift test lives in
  `web/tests/api.test.ts`, not in the Python suite.
- **A Playwright handle to Find's input goes stale after a search**, because the field
  re-renders. Query it again after each submit.
- **TSDoc is not Markdown or reST.** A double-backtick span reads as empty, and a
  `@vitest-environment` pragma inside a `/** */` block reads as a malformed tag; the pragma
  goes on its own `//` line.
- **The package builds with TypeScript 7**, which has no JavaScript compiler API, so tools
  built on it (typescript-eslint, older editor plugins) cannot load it.

## Tests

`web/tests/*.test.ts(x)`: one file per surface (`explore`, `map-*`, `graph-*`, `gaps`,
`admin-*`, `ask-panel`, `contested-page` …) plus the API drift test, and `lint-config.test.ts`, which lints a file breaking each rule
so a rule that stops applying fails the suite.
