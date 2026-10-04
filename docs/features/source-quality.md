# Source quality

Several mechanical judgements decide how a source is weighed, whether a model may read it,
and whether its raw file is kept. None of them is a credibility score, and none is made by a
model. Each one records the rule that decided it, so it can be checked and reversed.

- **Code:** `packages/meridian_core/meridian_core/tiering.py`, `trust.py`, `retention.py`,
  `ageing.py`; `services/worker/worker/extract/injection.py`, `furniture.py`, `retier.py`,
  `sweep.py`, `rawstore.py`
- **Tasks:** `P1-11`, `P1-23`, `P1-31`, `P2-20`, `P4-14`, `B-23`, `B-42`, `B-50`

## How it works

**Source tier** (§5.2). Each source gets one of `peer_reviewed`, `academic`, `government`,
`institutional`, `industry`, `press` or `informal`. The tier comes from domain patterns in
`config/source_tiers.yaml` plus document evidence. An academic domain makes a page
`peer_reviewed` only when the page names its own DOI (`B-50`); otherwise it is
`institutional`. The tier is the first tie-breaker when sources conflict, sets queue priority,
and drives counter-seeding toward tiers a node lacks. It says who published something, never
whether it is right.

**Retention tier** (§5.4). `primary` keeps the raw file; `background` keeps text and metadata
only; `junk` is excluded from search, the map and synthesis, and its raw file may be swept.
Retention only ever moves up automatically.

**Trust** (`P4-14`). Each fetched page is screened for prompt injection. Hidden imperative
text is strong evidence; visible imperative phrasing is weak, because articles about
injection quote it. The verdict is cached per domain:

- a domain in the curated tier map is `cleared` on sight;
- any other domain is cleared after `CLEAN_FETCHES_TO_CLEAR` (5) consecutive unflagged
  fetches, and any flag resets the count;
- a flagged unknown domain is `quarantined`.

Synthesis reads only `cleared` sources. `unscreened` is not treated as cleared. Quarantined
content stays stored, extracted and searchable; it is only withheld from models.

**Site furniture** (`B-23`, `B-42`). Pages about a website rather than a subject (privacy,
terms, contact, accessibility, a search engine's result pages) are not queued, and the ones
already stored are demoted to `junk`. The rule reads the URL, never the text. A source that
any edge or entity cites is never demoted.

**Ageing** (`P2-20`, §9). In search, older material is decayed by a half-life per tier,
applied to the fused score and never used as a filter. Peer-reviewed work does not decay;
informal material halves in about six months. The factor never falls below 0.25. Undated
documents are neither old nor new, and every hit shows its age and the factor applied.

**Retention sweep** (`P1-31`). Three verdicts: `droppable` (a file its source says not to
keep), `orphaned` (a file no source points at), and `dangling` (a source whose file is
missing). Only the first two are deleted, only with `--apply`, and a primary file never.

## Design choices

- **Tiers are mechanical by rule.** A model's opinion of a source would be untestable and
  would drift; a pattern list gives the same answer every time.
- **Screening flags rather than deletes**, and costs once per domain. Judging every page
  separately would quarantine a cleared site's page 3,001 for quoting an instruction.
- **The sweep is a separate, manual step.** It is the only pass that destroys anything. Run on
  a timer, it would sooner or later run at the same moment as the mistake that made something
  droppable.

## Configuration

- `config/source_tiers.yaml` (seeded) maps domain patterns to tiers, and lists domains that
  need scholarly evidence.
- Half-lives are constants in `ageing.py`, overridable per topic.

## Operating it

- The `sweep` job runs daily in report mode. Read its report, then run
  `python -m worker.sweep --apply` by hand.
- Quarantined domains appear in Admin. Until a model can judge them, a person clears a
  quarantine.

## Tests

`tests/unit/test_tiering.py`, `test_tier_evidence.py`, `test_ageing.py`,
`test_injection_screen.py`; `tests/integration/test_trust.py`, `test_retention.py`,
`test_retier.py`, `test_furniture_sweep.py`.
