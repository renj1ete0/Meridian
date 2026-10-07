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
matching found everything, a sentence contradicting itself on a correct result.

**`/?q=…` opens with that search run**, so a search can be linked and shared. Gaps' "Search it
in Find" (`P6-36`) and the Map's "Open in Find" (`P6-34`) link here. `topic=` (repeated) and
`topic_match=` preselect the topic filter; the Map's topic web (`B-72`) sends its
intersections with `topic_match=all`. Topics without a query set the filter and wait for the
words.

### The search field

§8 puts two unusual things in the control. **The `hybrid` marker**: retrieval is two arms fused
by reciprocal rank (`P2-06`), and a lexical-only result set and a fused one fail in different
ways and want different follow-ups, so the mode is named, the same instinct as putting the tier
on every hit. **"Filters apply before the vector search"**, an implementation note on the
surface on purpose: it is the difference between "twenty government sources" and "whatever
survived filtering the top twenty", and a reader who assumes the second will mistrust a correct
result set. The field holds no state; the query belongs to the page.

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

### The topic filter

`P2-14` put labels on every source and the filter on the API; `P6-24` is the control. Chips
rather than a dropdown, because the set is small (a handful of topics, which is what §10's
weight vector is) and a dropdown hides what is available behind a click. Selected chips carry
the graph accent and nothing else: §2's palette has no green or red, and colour must not imply
a verdict, so it says only "this is on". Places (`P2-23`) use the same component, because a
second one would drift from this one in exactly the caveat that matters.

**The caveat is the interesting part.** A source never examined for topics carries no labels,
and a topic filter excludes it: nothing has established that it belongs, and including it would
assert something no pass checked. That is correct and also invisible: a reader who narrows to a
topic and sees three results cannot know the corpus holds three hundred documents nobody has
examined. So the control says so, once, while a filter is active.

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

### Source pages

The first screen where the corpus reads as documents rather than results (`P6-14`, `P6-15`):
chunks in document order, figures with captions, and §12.5's exports, all of which had
endpoints and nowhere to be shown. **Provenance is the page, not a footnote**: tier, date, DOI
and how the text was extracted are in the header, because "what is this and how do I know" is
the question a reader arrives with, and `extractor` (`P1-44`) separates a document that had no
text from one whose extractor fell over. **Annotation lives here** (`P6-05`), since this is where
reading happens; ticking passages puts the citations on a note without copying chunk ids by
hand, the version of the feature that would not get used.

### Figures

§12.5 asks for "thumbnails linked to the node, with page-accurate links to raw files". There are
no thumbnails: nothing downloads figure images (`P1-10`), so `thumbnail_path` is empty, and a
placeholder grid would be a promise the corpus cannot keep. What there is is the part §6.6 says
carries most of the value, the caption ("often the most information-dense sentence about the
figure"), so the caption is the content and the links make it checkable. There are two links:
`image_url` is the picture where the publisher has it, live and liable to move; `raw_url` is
this corpus's own copy at the caption's page, which is what §5.4 keeps raw files for, since link
rot is the binding reason and a local copy keeps a citation checkable years later.

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
