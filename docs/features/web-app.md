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
