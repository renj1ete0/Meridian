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
- **The bell** counts notifications that arrived since this reader last opened the panel,
  rather than the server's `unread`, which nothing in the product clears and so only grows.
  Opening the panel is the acknowledgement; §8 has the panel filter by type, not read state,
  so the stamp only ever moves the badge.
- **Notification kinds** are §8's three (jobs, approvals, alerts) over the types the database
  records. The map mirrors `NOTIFICATION_TYPE` in `models/runs.py` and `tests/shell.test.tsx`
  compares them, because a type in one and not the other would fall into no filter: the one
  way a notification can be recorded and never seen. Unknown types show as jobs rather than
  being dropped.

### Readable passages

`lib/readable.ts` is the display's own view of passage text and changes nothing but what is
drawn. Pages are converted to Markdown at ingestion, and about a quarter of stored passages
still carry its syntax: links, footnote markers like `[[30](https://…#ref-30)]`, images, bold
and `#` headings. The stored text stays as it is, since everything is re-derived from it
(§2.4), and embedding already reads a view without URLs (`B-49`). A short line ending in an
ellipsis at the very start is a widget's prompt ("Search…"), not what the page says (`B-98`);
only there, and only short, so a sentence left unfinished mid-passage is kept.

Text extracted from a PDF carries its column width: a line ends wherever the page did. As-is it
is a ragged column; with every newline kept it cannot be skimmed. But web pages break lines on
purpose (a label, then its value), and joining those would run them together. So a passage
counts as wrapped only when most of its breaks fall mid-sentence, and then only breaks that
plainly continue are joined: the next line in lower case, or this one ending on a comma or on
a word such as "of" or "the" that no sentence ends with. A break after a full stop, before a
capitalised line, a list item or a blank line stays, and a word hyphenated across a break is
rejoined. The rule is conservative on purpose; see the handover before loosening it.

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
