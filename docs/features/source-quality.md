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

### Tiers

Tiering is pure functions over the tier map, so it is trivially testable and gives the same
answer every time for the same domain (§5.2). Tier does three jobs: it is the first
tie-breaker when sources conflict; it sets queue priority, so a search result from an official
domain is fetched before a blog without anyone curating a seed list; and it drives
tier-imbalance counter-seeding (§7.4), where a node evidenced entirely by one tier gets seeds
aimed at the missing ones.

A domain resolves by exact match, then the **longest** matching suffix pattern, then the
default. Longest-wins matters: `*.gov.sg` and `*.sg` can both match, and the specific one has
to win or every domain under the broader suffix collapses into one tier.

<a id="scholarly-evidence"></a>**Scholarly evidence** (`B-50`). An academic institution's
domain serves its journals and theses, and also its admissions pages, its hospital's condition
pages, its law school's statute library and its HR policies. The suffix says who runs the site,
not whether a page was peer reviewed. `needs_scholarly_evidence` lists those suffixes; a
publisher's domain is not in it, and neither is a domain named exactly, since naming it is
somebody's judgement about it. `worker.retier` applies the rule to sources already stored: tier
only, so a kept raw file, edges and claims are untouched, because re-weighing deletes nothing.

<a id="urgency"></a>**Urgency** (`P2-20`). The queue reads the same half-life table as ranking,
rather than a second set (two sets diverge because nobody notices they exist; `P7-06` should do
likewise). A fast-rotting source is worth fetching sooner for two reasons that point the same
way: its claim stops being current, and the page itself is likelier to be gone (news sites
reorganise, press releases move, a paper is still there in five years). Neither applies to
`peer_reviewed`, which gets nothing. Urgency is added to the tier's priority, never a
replacement: it separates documents *within* a tier, small enough that it cannot promote an
informal page above an official one, large enough that a news page outranks a paper of the same
tier. `priority_for_domain` stays beside `priority_with_urgency` for a test or Admin screen that
wants the tier map's answer alone.

### Trust

`P1-23` built the injection screen and deliberately stopped at flagging: a screen that
quarantines before anyone has seen its false-positive rate quarantines the corpus. It ran clean
across the crawl, and `P4-14` is the half that acts on it.

- **Paid once per domain.** The verdict is cached on `fetch_policy`. A site with thousands of
  pages must not be judged thousands of times, and a domain cleared on Monday must not have a
  page quarantined on Friday because it quoted an instruction. So a flagged page on a *cleared*
  domain stays cleared: re-quarantining pages one by one would make the clearing meaningless and
  produce the drip of false positives `P1-23` avoided. A flagged page on an *unscreened* domain
  is quarantined on its own account, because a domain without a verdict is no reason to admit a
  page that tripped the screen.
- **Stored, never deleted** (§2.5). The page keeps its raw file, extraction and chunks; it loses
  only eligibility for what the slow loop reads. That is reversible, and the thing screened for
  is a false positive away from ordinary writing about security.
- **`unscreened` is not `cleared`.** `only_readable` admits `IN (cleared)` rather than excluding
  `quarantined`, so an unexamined page does not reach a model by default. It is applied to the
  *chunk* query, where every reader starts, because a filter a caller can forget to join will be
  forgotten. It is deliberately not applied to the operator's own search: reading their own
  corpus, they should see what was quarantined, which is how a false positive gets noticed.
- **Two cheap ways to clear.** A domain the curated tier map names is cleared on sight: someone
  curated that list, the human judgement a model would otherwise be asked for. `is_tier_mapped`
  answers that separately from `resolve_tier`, which always answers; the two share matching
  rules but not a function, since a predicate that also returned a tier would be used for the
  wrong one by somebody in a hurry. Any other domain clears after `CLEAN_FETCHES_TO_CLEAR` (5)
  consecutive unflagged fetches. Five rather than one, because the pages carrying an injection
  are rarely the first linked; five rather than fifty, because until a domain clears everything
  it serves is held back, and a threshold nobody reaches is a corpus nobody can read. The counter
  resets on any flag, whatever the state, like `consecutive_failures`: a cleared domain stays
  cleared but starts earning its clearing again.
- **One row, no history.** `record_screening` runs once per fetch; `trust_decided_at` and
  `trust_decided_by` explain the current verdict and the crawl's logs carry the rest. A
  `rejected` domain is left alone: a crawl that un-rejected a domain by fetching five clean pages
  would be overruling a person.

What is not here is the part that needs a model: an unknown domain that trips the screen stays
quarantined for a frontier model to judge (`P4-07`). Until then a person clears it, which is the
correct failure; the alternative is admitting unscreened content because nothing could screen
it.

<a id="seeding-a-domain"></a>**Seeding a domain** (`P4-12`, §11.4). Only a queued URL or
sitemap records its domain; a search query or DOI names no host and records nothing (`B-149`). A third question beside
"may we fetch this" (`status`) and "may a model read what came back" (`trust_state`): within
which domains §11.4's cap on model seeding applies. The first `seed_source` for a domain sticks:
a domain found by a link and later proposed by a model was still found by a link, and letting
the later event win would erase the provenance that decides auto-approval. An operator's own
seed is allowed immediately, since typing a URL is consent. A domain first sighted in a model's
proposal accrues the same evidence of novel documents but still waits for a person: the failure
avoided is a model talking the crawl into a domain by describing it confidently, and evidence
gathered after the proposal is evidence the proposal caused.

### Injection screening

The slow loop hands a frontier model passages from pages found by following links, and that
model holds write tools (`add_edge`, `tag_entity`, `enqueue_seed`). A page that can talk to it
can write to the graph, and since `P1-06` the frontier follows links at volume.

- **Mechanical only.** §2.1 keeps ingestion working with every model offline; screening with a
  model would put the guard behind the thing it guards. Regex and DOM work are also cheap enough
  to run on every page.
- **Hidden is the signal; imperative is not.** A corpus touching AI will legitimately contain
  "ignore all previous instructions", in articles *about* injection. Flagging that trains people
  to ignore the flag. Text hidden from a reader that still reaches extraction has no honest
  purpose. So `visible_instructions` is recorded as context for a page that tripped something
  else, never as evidence alone, and `suspicious` means hidden instructions or tool-directed
  imperatives, not a page mentioning an AI. The phrase lists come in two families, phrasing
  aimed at an assistant and phrasing naming its machinery, kept apart because neither is proof.
- **A tool directive is suspicious even when visible**: a page telling an agent to add an edge or
  send contents somewhere is addressed at something with tools, and visible injection works. An
  article quoting a full payload also trips it, which is the right side to err on, since the flag
  blocks nothing and the other error is a model reading an instruction as prose while holding
  `add_edge`.
- **Where it looks.** The raw HTML is needed because hiddenness is a DOM property extraction
  throws away; the extracted text is screened separately because an instruction that survived
  extraction is the one that would be read. Only inline styles and attributes are checked:
  resolving stylesheets is a browser's job, and the attack overwhelmingly uses inline styles
  because they need no second request. HTML comments are checked in the raw source, since a
  comment has no honest audience and survives naive extractors. A hidden element must hold a
  minimum of text, because `display:none` drives every dropdown, modal and tab strip, and a low
  threshold would flag every page. A finding says how text was hidden (an element with
  `display:none` and white-on-white text want different responses).

### Site furniture

Pages crawled before the furniture rule was wide enough cost twice: they answer searches and
take whole synthesis batches with nothing to extract. `worker.furniture` moves them to `junk`,
which a misjudged page is one `UPDATE` away from leaving, with its raw file and chunks untouched.
It uses the frontier's own `prefilter.is_site_furniture`, on the URL a source was stored under
and the one it was served from, so a page cannot be furniture at the door and a document once
inside. Evidence outranks the address: once a passage is evidence for something, how its URL
looks no longer decides anything.

### Ageing

§9 says ageing is topic-dependent (a finding in one field is stale within a few years while one
in another stays sound), and the same holds across source tiers: press and informal material rots
in months, peer-reviewed work often not at all, and a policy page supersedes rather than ages.

- **A single "newer is better" multiplier is the wrong shape.** Applied globally it buries the
  foundational papers, the failure that matters most for a corpus with an academic spine: the
  paper everything cites would rank below a blog post about it. So `peer_reviewed` does not
  decay, and the other half-lives are ordered by how quickly the *claim* stops being current,
  not how quickly the page changes; an official policy page sits well above press without being
  exempt.
- **A decay, not a filter.** A filter removes; a decay reorders, which is what "probably less
  current" means. The floor (0.25) keeps it so: without one a ten-year-old press article scores
  within rounding of zero and drops out, a filter wearing a decay's clothes.
- **Undated is neither old nor new.** A large share of crawled pages have no extractable date,
  and either default is wrong for the other kind: treated as new, every undated blog floats up;
  treated as old, every undated standards document sinks. The factor is 1.0 without a date, and
  the result says "no date".
- **Topic overrides win over the tier**, since §9's example is a topic one. Across several topics
  the **longest** half-life wins: a document partly about something slow-moving should not be
  aged as if only about the fast half.
- **A future date is age 0.** Dates slightly ahead are common (an embargo, a time zone, an issue
  dated next month), and a negative age would let a scraped field boost the document.
- **Shown, not silent.** A quietly demoted result cannot be audited; the hit carries its age and
  factor, as it carries its tier and per-arm ranks.

### The raw store

Link rot is why it exists (§5.4): official URLs reorganise constantly, and a citation that
resolves to a 404 cannot be checked, so a local copy plus a checksum keeps the corpus honest
about what it read. Not everything is kept, and the split is the point: background sources keep
extracted text and metadata, and the bytes are dropped after extraction. Every tier still gets a
checksum, one hash of bytes already in memory, so a later fetch can say "unchanged" without the
previous copy.

- **The path comes from the URL, not the content.** A re-fetch must land on the same path, or the
  store grows a copy per visit. So the name is `sha256(url)`, and the content hash goes in the
  database, where it answers whether the page changed. The layout is
  `<domain>/<two hex digits>/<sha256(url)><ext>`: the domain leads so everything from a site is
  one directory (what a takedown and a sweep need), and the shard stops a busy site becoming one
  directory with a hundred thousand entries.
- **Every write is atomic**: a temporary name in the same directory, then `os.replace`. A crash
  halfway through a large PDF must not leave a truncated file that its checksum swears is
  complete.
- **The URL is attacker-influenced**, so only hex digits and a sanitised domain reach the
  filesystem, and the extension comes from an allowlist of media types, never from
  `content-disposition` or the URL's suffix; anything unrecognised is `.bin`. A host that is not
  a plain hostname (an IDN never punycoded, an empty host, `..`) is refused rather than sanitised
  into something plausible.
- **Retention only moves up automatically**, as quality tier does (§11.12): an operator who
  promoted a domain to `primary` did so on purpose, and a mapping that disagreed next week would
  quietly start throwing files away.
- **The stored path is relative.** An absolute one bakes in the container's mount point, and a
  store moved to a bigger disk would invalidate every row. `raw_root` (`P1-45`) is recorded
  beside it as provenance and never used to resolve anything. It answers "which store was this
  written into": a corpus written partly natively and partly by a container had rows that
  dangled from either root's view, indistinguishable from a real loss, which cost a session to
  work out.

### The retention sweep

§5.4 splits raw retention three ways, and the novelty gate's verdict is what the sweep spends.
Measuring the corpus before writing it changed what it is: there were no junk files and no
orphans to reclaim, but some sources whose `raw_file_path` pointed at nothing. No junk files is
structural: `P1-11` never writes the files §5.4 says to drop, and retention only moves up, so a
file that exists was written under a tier that keeps files. The reclaim arm is correct and
currently a no-op by construction. The dangling arm is the one that matters: a source claiming a
file it does not have is a citation that will not open, and nothing else notices, because the
row is complete and the chunks are real.

- `droppable`: a file its source says not to keep.
- `orphaned`: a file no row points at. Real, because `path_for` includes the media type's
  extension: a URL once served as HTML and later as PDF gets a different path, and the old file
  is left behind.
- `dangling`: a row whose file is missing. **Never deleted**: the row is the only record the
  fetch happened, and its text is still in the corpus. A row recording a *different* `raw_root`
  is reported separately, not as dangling, since it is a file this sweep is not looking at;
  folding the two together made a multi-root corpus report a dangling list long enough to hide a
  real loss. A row with no root predates the column and cannot be told apart from a loss.

A primary file is never swept, checked when the plan is built and again when it is applied,
because a plan is data a caller can construct, filter or replay. `dry_run` defaults to true: this
is the only operation that destroys what a re-crawl cannot reproduce. The sweep cannot be resumed
the way the queue-driven passes can: a half-finished sweep has deleted some files and not
others, and its plan is stale. Superseded chunks (`P1-32`) are reported by the sweep too, rather
than reclaimed by the crawl, which sees that a page changed but has no idea what the graph was
built on.

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
