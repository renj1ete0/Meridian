# Meridian — Design System

The written counterpart to `brand-canvas.dc.html` (the visual canvas). Where the two
disagree, this file is the reference: it holds the values the UI should be built from.

Artboard sources live beside this file as `*.dc.html`. `canvas.json` is their layout.

---

## 1. The mark

A globe outline crossed by one meridian. Three nodes sit on the meridian — zenith,
hub, base — and the arc between them *is* the edge. One straight edge leaves the hub
on a bearing and terminates on the rim: the graph continues past the reference line.

The meridian stroke is heavier than the globe outline. The reference line is the
subject; the sphere is context.

### Geometry (100 × 100 units)

**Full mark** — 32px and above:

| Element | Value |
|---|---|
| Globe | `circle cx=50 cy=50 r=38`, stroke 2.4 |
| Meridian | `ellipse cx=50 cy=50 rx=14 ry=38`, stroke 2.8 |
| Bearing edge | `M50 50 L81.1 71.8`, stroke 2.4, round cap (35° below horizontal) |
| Zenith node | `cx=50 cy=12 r=6` — filled with `accent.graph` |
| Hub node | `cx=50 cy=50 r=5` |
| Base node | `cx=50 cy=88 r=4.2` |
| Bearing node | `cx=81.1 cy=71.8 r=4` |

**Compact mark** — below 32px. Drops the bearing edge and its node; the meridian
widens and all strokes thicken so the drawing survives rasterisation:

| Element | Value |
|---|---|
| Globe | `r=38`, stroke 3.6 |
| Meridian | `rx=17 ry=38`, stroke 3.6 |
| Nodes | zenith `r=7`, hub `r=6`, base `r=5` |

Bounding box 76 × 86.2. Optical centre is the circle centre (50, 50).

### Rules

- **Clear space** — one unit `x` on every side, where `x` is the meridian's width
  (0.28 × mark height). At a 76px mark, `x` = 21px.
- **Minimum size** — full mark 32px; compact mark 16px; horizontal lockup 132px wide.
- **Single ink** — the mark must read with the accent dropped. It is a stroke drawing,
  not a colour composition. Print as 100% K.
- **Over busy ground** — scrim first: `rgba(13,20,36,0.62)` with a 14px backdrop blur
  and a `rgba(230,235,245,0.16)` hairline. Where the ground is unpredictable, use a
  solid white knockout with no accent.
- **Never** — stretch on one axis, tilt, gradient-fill, recolour outside the palette,
  place on `accent.graph` (the zenith node vanishes), or re-set the wordmark.

### Lockup

Horizontal (primary): mark · 22px gap · 1px hairline rule at cap height · 22px gap ·
wordmark. Wordmark is Archivo 500, tracking −0.006em.

Stacked (secondary): mark above wordmark, with the mono descriptor
`AUTONOMOUS RESEARCH SYSTEM` at 8.5px / 0.19em beneath.

### Tagline

> **A line to measure everything else against.**

Archivo 300, hung from the wordmark's left edge, in `text.muted`.

**It appears on the About screen and nowhere else.** Nav, headers, empty states and
every other surface carry the wordmark alone. Repeated, a tagline stops being a
statement and becomes decoration.

---

## 2. Colour

Dark is the flagship: the graph canvas reads best on a dark ground, and Explore is
where the work happens. Light exists for docs, Admin and print.

Both accents are `oklch L 79.0% C 0.115` in dark and `L 49.8% C 0.083` in light —
identical lightness and chroma, 130° of hue apart, so neither optically outranks the
other. The neutrals carry a navy hue bias (~265°) in both modes so the two read as one
family rather than an inversion.

There is deliberately no green and no red in the palette. Nothing in this system is
pass/fail, and colour must not imply a verdict.

### Dark tokens (flagship)

| Token | Hex | AA¹ | Role |
|---|---|---|---|
| `ground.deep` | `#070B14` | — | Behind the graph canvas |
| `ground` | `#0D1424` | — | Application ground |
| `surface` | `#151E33` | — | Panels, detail cards, top bar |
| `surface.raised` | `#1E2942` | — | Inputs, hover, chips, selected rows |
| `line` | `#2A3654` | — | Hairlines and panel edges |
| `line.strong` | `#3E4D71` | — | Focus rings, active edges, table heads |
| `text` | `#E6EBF5` | 13.9 | Titles, body, anything read at length |
| `text.muted` | `#9BA8C4` | 6.9 | Descriptions, secondary metadata |
| `text.faint` | `#7C8AAB` | 4.8 | Mono labels, units, timestamps — floor for text |
| `accent.graph` | `#49D0DA` | 8.9 | Edges, links, focus node, interactive affordance |
| `accent.graph.deep` | `#0C646A` | — | Edges at rest, unfocused neighbours, fills |
| `accent.attention` | `#E6B061` | 8.5 | Contested, stale, flagged — nothing else |
| `accent.attention.deep` | `#654617` | — | Contested fills and badge grounds |

¹ Measured against `surface` `#151E33`.

### Light tokens

| Token | Hex | AA² | Role |
|---|---|---|---|
| `paper` | `#F1F3F7` | — | Docs, Admin, print — a faintly blue paper, not cream |
| `surface` | `#FFFFFF` | — | Cards and tables lifted off the paper |
| `line` | `#DBE0EA` | — | Hairlines, table rules |
| `line.strong` | `#A9B4C6` | — | Dividers and marks that must be seen |
| `text` | `#101A2E` | 15.6 | Ink |
| `text.muted` | `#4E5A73` | 6.2 | Secondary copy and captions |
| `text.faint` | `#5C6A85` | 4.9 | Mono labels — floor for text |
| `accent.graph` | `#0D6F7C` | 5.3 | Links and controls |
| `accent.attention` | `#805A28` | 5.5 | Contested and flagged, on paper |

² Measured against `paper` `#F1F3F7`.

Documentation-only: `#B03A38` marks misuse examples in the usage guide. It is not a
product colour and must not appear in the UI.

### Graph canvas colours

| Element | Dark |
|---|---|
| Canvas ground | `#070B14` |
| Graticule | `#16233D` |
| Focus node | `#49D0DA`, r 13, with a `#49D0DA` ring at 45% opacity, r 26 |
| Neighbour node | `#8FA0C0`, r 6.5–8 |
| Contested node | `#E6B061`, r 8, with a ring at 60% |
| Cross-topic node | `#49D0DA` at 75% |
| Edge, from focus | `#2E93A0`, 1.6 |
| Edge, between neighbours | `#0C646A`, 1.2 |
| Edge, contested | `#B98737`, 1.8 |
| Edge, second hop (hint) | `#1B2C4A`, 1.0 |
| Node label | Archivo 12, `#C9D3E6`, with a 3.5 `#070B14` halo (`paint-order: stroke`) |

---

## 3. Typography

Two families, both on Google Fonts (verified loadable from `fonts.googleapis.com`).

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@300;400;500;600;700&family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;0,600;1,400&display=swap">
```

**Archivo** (Omnibus-Type) — display and text. Drawn for signage and small-size print:
open apertures, tall x-height, weight that holds at 12px. A wayfinding face, which is
the right register for a system about reference lines and bearings. It replaces the
obvious Space Grotesk pick — same technical family, without the startup accent.
Fallback: `'Helvetica Neue', Arial, sans-serif`.

**IBM Plex Mono** — data, labels, provenance. Drawn for engineering documentation;
humanist detail keeps a column of confidence values and source domains warm rather than
clinical, and its true italic gives hedged quotations somewhere to live.
Fallback: `ui-monospace, 'SFMono-Regular', Menlo, monospace`.

### The split is strict

Archivo carries anything written for a reader. Plex Mono carries anything the system
measured — source domain, tier, confidence, timestamp, chunk id — so provenance is
recognisable before it is read.

Uppercase is reserved for mono labels at 9–10px with 0.15em tracking. Archivo is never
set in caps.

### Scale

| Role | Family | Size | Weight | Line height | Tracking |
|---|---|---|---|---|---|
| wordmark | Archivo | — | 500 | 1.0 | −0.006em |
| display | Archivo | 29 | 600 | 1.15 | −0.008em |
| heading | Archivo | 19 | 600 | 1.3 | −0.004em |
| subhead | Archivo | 15 | 600 | 1.4 | 0 |
| body | Archivo | 14.5 | 400 | 1.62 | 0 |
| small | Archivo | 12.5 | 400 | 1.55 | 0 |
| data | Plex Mono | 12.5 | 400 | 1.7 | 0 |
| label | Plex Mono | 10 | 500 | 1.4 | 0.15em |

Use `font-variant-numeric: tabular-nums` wherever digits line up in a column.

---

## 4. Voice

Short declaratives. Concrete nouns. Limits named out loud. Warmth comes from precision,
not encouragement.

**01 · State the absence.** A gap is a finding. Say what is missing, how much, and since
when.
- Do: *No peer-reviewed source on shade provision in Seoul since 2019. Three press items, no primary data.*
- Not: *Great coverage across your topics — just a few gaps left to explore.*

**02 · Report structure, not verdicts.** The system extracts stance, funder, hedging and
tier. It does not score credibility, and the copy must not imply it did.
- Do: *Funder: advocacy organisation. Stance: pro-intervention. Hedging: low. Original data: none.*
- Not: *Low-credibility source — reliability 32/100.*

**03 · A contradiction is a result.** Contested nodes are the valuable ones. Never frame
disagreement as an error or something to clear.
- Do: *Two sources disagree on modal shift. Both edges kept. Open the pair.*
- Not: *Conflict detected. Resolve this contradiction to continue.*

**04 · An error says what broke and what happens next.** Name the cause, the scope and
the recovery. Never apologise.
- Do: *lta.gov.sg timed out three times in 40 minutes. Requeued for 06:00. No chunks lost.*
- Not: *Oops! Something went wrong. Please try again later.*

**Words this system does not use:** unlock, seamless, supercharge, empower, effortless,
insights, leverage, oops, smart, AI-powered, exclamation marks.

---

## 5. Surface conventions

- **Radii** — 0 nearly everywhere. Panels, cards, inputs and buttons are square. 3px is
  the only permitted radius, used for chips and the scrimmed logo panel. Rounded corners
  are not a decoration budget this system spends.
- **Borders over shadows.** Depth comes from `surface` steps and 1px `line`, never from
  drop shadows.
- **Density.** Explore is dense by design — reading time is the bottleneck, not screen
  space. Admin is generated CRUD and should stay plain.
- **Controls.** 32–36px tall on desktop. Primary action is `accent.graph` filled with
  `#04222A` text; secondary is `surface.raised` with a `line.strong` border.
- **State in form, not only colour.** Contested carries a bordered badge as well as the
  brass hue; a tier is a bordered mono chip, not a coloured dot.

### Translucency

**Translucency means "floating above your work". Opacity means "this is the thing you
are reading."** It is functional, never decorative — the legibility-over-volume principle
applied to surfaces.

| | Treatment |
|---|---|
| **Translucent** — floats over the graph canvas and is transient: the collapsed synthesis toggle, hover cards, the top bar where it overlays the canvas | `rgba(21,30,51,0.90)` + `backdrop-filter: blur(10px)`, hairline `rgba(230,235,245,0.16)`. The toggle uses the accent at the same tint: `rgba(73,208,218,0.92)` + `blur(8px)` |
| **Opaque** — anything the user reads: the synthesis thread, node detail panel, report output, every Admin surface | Solid `surface` `#151E33` (or `#FFFFFF` on paper). Text over a live graph is measurably harder to read, and these surfaces carry citations, provenance and confidence values |

The one exception is the logo scrim in artboard 03 — `rgba(13,20,36,0.62)` + `blur(14px)`.
That is heavier because it sits over arbitrary imagery rather than the app's own canvas.

The top bar is translucent on Explore surfaces (it overlays the canvas) and opaque on
Admin (it overlays a document).

### The top-right cluster

Designed once, used on every screen, in this order: **status pill** (queue depth and
fetch health; the dot goes brass when a run has failed) · divider · **notifications**
(cyan count for completions, brass count when the list holds an alert) · **settings**.

No greeting copy anywhere. One person owns this system; a welcome line would be
addressing them on behalf of nobody. Settings is where an account menu goes once auth
stops being Cloudflare Access (spec §12.6).

The Explore landing carries one further line of continuity for a returning user — a
single mono line of deltas since their last visit, set with the other counts, not as a
separate widget.

---

## 6. The contested mark

**† (U+2020, dagger)** — IBM Plex Mono, `0.72em` superscript, `accent.attention`.

The dagger is the typographic mark for "a qualifying note is attached to this", which is
what contested means. It exists in both families, so it needs no icon system and inherits
weight and colour from the type around it.

It is always paired with the brass tint, and the brass tint never appears without it.
Strip the colour and the reading must survive — that is the test.

| Context | Form |
|---|---|
| Inline claim in an answer | brass text, 1px `#B98737` underline, trailing † |
| Node chip | brass mono chip on `rgba(101,70,23,0.35)` with a `#654617` border, trailing † |
| Detail-panel badge | `†  Contested`, leading dagger with a nbsp |
| Graph canvas | brass node plus a † superscript on the node's label (`tspan`, 10px, `dy="-4"`) |

---

## 7. Icons

Monoline, drawn in the mark's own language: circles and arcs before rectangles.

| | |
|---|---|
| Grid | 24 × 24 |
| Live area | 20 (interface icons) · 16 (node-type glyphs) |
| Stroke, silhouette | 1.6 |
| Stroke, interior detail | 1.35 |
| Terminals | `stroke-linecap: round`, `stroke-linejoin: round` |
| Corner radius | 2 |
| Angles | multiples of 15° |

The 1.6 / 1.35 pair carries the mark's own ratio — its meridian is 2.8 against a 2.4
globe. Silhouette is always heavier than the detail inside it. Circular forms may
overshoot the live area by one unit so they do not read small beside a square.

**Below 20px the detail strokes are dropped and the silhouette carries alone** — the same
optical-size rule the compact mark follows.

Set: search, coverage, contested (the dagger, drawn to grid), saved view, annotate,
report, notifications, history, steering, export, path mode; plus node-type glyphs at the
secondary weight for `concept`, `place`, `organisation`, `intervention`, `finding`,
`source`.

---

## 8. Interaction model

### Explore, default state

Opening Explore with no active query does **not** land on a focus+expand view, and never
renders the whole graph (spec §12.2). The default state carries:

- the search field, prominent and centred, with the `hybrid` marker and the
  "filters apply before the vector search" note;
- four counts in the mono numeral style — documents, nodes, edges, contested†;
- the three entry points from spec §12.5 as parallel cards — Search, Coverage, Contested;
- a short "where you were" list of saved views and recent nodes.

Background geometry on this screen is the mark's own circle-and-meridian, scaled up and
cropped. It is decoration. Nothing on it is corpus data.

### Synthesis panel

Collapsed by default: a 44 × 44 floating button, 3px radius, `accent.graph` fill with a
`#04222A` glyph, 24px from the canvas's bottom and right edges. It floats over the canvas
and takes no layout space. Three states — rest, hover (`#7BDFE7` plus a 3px cyan halo),
and panel-open (a `surface` fill with a 2px cyan border and a left chevron).

There is no unread badge. An answer only arrives because you asked for one.

Expanded: a 420px panel sliding in from the right, over the node detail panel, with a
`line.strong` left border and a `-18px 0 40px rgba(4,8,18,0.55)` shadow. Contents, top to
bottom:

1. **Header** — title, "New question", close.
2. **Earlier questions** — a collapsible block on `#121A2C` listing past questions with
   dates and a "Show all →". History lives inside the panel; there is no separate screen.
3. **Thread** — the question in a `surface.raised` block; the answer as plain prose with
   **inline citations** (a 1px `accent.graph` underline on the cited span) and contested
   spans in brass with the dagger.
4. **Node chips** — the nodes the answer cites, contested ones marked.
5. **Input** — with the current canvas selection shown as a removable context chip.

### Topic management (spec §10.2)

Three permanent interventions, none of which delete anything:

- **Add** — takes its share from the unpinned pool. Pinned topics hold their weight; the
  remainder is redistributed in proportion. The dialog shows the arithmetic before you
  commit. Existing nodes keep the topic labels they already carry.
- **Pause** — temporary and reversible. The topic **keeps its weight in the pool**, so
  nothing else shifts while you fix a fetch problem. Acquisition is suspended; the row
  greys and carries a Paused badge.
- **Archive** — permanent but reversible. Stops seeding, **releases the weight** and
  re-normalises the rest. Archived topics move to a collapsed section rather than
  vanishing; their nodes stay in the graph and stay searchable. Restore is one click.

Archive is styled in `accent.attention`, not in a destructive red. It needs attention;
it destroys nothing.

### Drafting (spec §11.13)

One sheet, three numbered sections, one submit.

1. **Scope** comes from something that already exists — a saved view, a coverage-dashboard
   cell, the contested list, or a path-mode result — plus a free-text question. Never a
   blank field.
2. **Coverage pre-flight** is the point of the flow. Per dimension in scope: sources,
   tagged fraction, tier spread, newest date. Thin or stale dimensions are flagged in
   brass with the dagger, and a summary band says plainly what will not carry a
   recommendation. **It warns; it does not block.** Proceeding is allowed, and the draft
   then marks its own gaps rather than writing around them.
3. **Estimate** — chunks, tokens, cost, wall time, and what is left of the daily ceiling.

The job runs asynchronously and never blocks the interface; a partial job keeps and cites
what it did finish. Output carries inline citations (a 1px `accent.graph` underline), node
chips, the coverage note carried forward from the pre-flight, and exports as Markdown +
BibTeX.

### Notifications

In-app counterpart to the daily Telegram digest, opened from the top-bar cluster. Three
types — **jobs**, **approvals**, **alerts** — grouped by day and **filtered by type, not
by read state**: the question a returning user asks is "did anything need me?", not "what
have I already seen?" Alerts carry the brass tint and the dagger, and drive the brass
count on the bell.

---

## 9. Open, and adjustable

- The voice register assumes the reader built the system. Principle 04 is the first to
  soften if that stops being true.
- `accent.attention` on paper (`#805A28`) is gamut-matched to the light cyan, which makes
  it browner than a bright brass. `#8A5A0E` is the warmer alternative if it reads muddy
  in print — it costs the matched-chroma property.
- The mark's bounding box is 76 × 86.2, so the nodes overhang the circle top and bottom.
  Centre on the circle centre, not the bounding box.
- Graph content in the Explore and Admin artboards is sample data.
