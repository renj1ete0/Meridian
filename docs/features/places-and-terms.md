# Places and terms

Two pieces of reference knowledge sit beside the topics. **Places** record which countries
and cities each source is about, so that coverage can be stated per place and results
compared across places. **The gazetteer** is the project's vocabulary of named things
(agencies, schemes, metrics, acronyms with their expansions). It grows mostly by harvesting
acronym definitions from documents, and it feeds entity extraction, place tagging and the
topic prototypes.

- **Code:** `packages/meridian_core/meridian_core/places.py`, `placenames.py`,
  `gazetteer.py`, `models/gazetteer.py`; `services/worker/worker/places.py`, `harvest.py`,
  `ner.py`
- **Tasks:** `P2-23`, `P5-02`, `B-123`

## How it works

**Places** (`worker.places`, hourly). Four mechanical signals, recorded per source in
`place_evidence`:

1. **Names in the text.** Country and city names, plus approved gazetteer terms that carry a
   jurisdiction, counted over the source's live passages. A mention in the title counts three
   times. A place is tagged when it is named at least `MIN_MENTIONS` (4) times *and* at least
   a quarter as often as the most-named country. So a passing mention does not tag a
   document, while one that compares several places evenly carries all of them.
2. **Place entities cited from its passages.** A graph edge about a place, drawn from this
   source, tags that place on its own.
3. **The publisher's domain.** A country-code or national-government domain lowers the bar for
   its own country. A government page that names no place is taken to be about its own
   jurisdiction. The domain never overrides the text.
4. **The document's language**, the weakest signal: it can confirm a country-code domain and
   never tags alone.

Codes: a country is its ISO 3166-1 alpha-2 code (`EU` for the union); a city is its
UN/LOCODE without the space, whose first two letters are its country. NULL means not examined
yet; `{}` means examined and about no place.

**The gazetteer.** Rows have a surface form, a canonical name, aliases, an entity type, an
optional jurisdiction, topic labels and an approval state. Only approved rows are used.

**Acronym harvest** (`worker.harvest`, daily). It finds the pattern `Full Name (ACRONYM)` in
documents labelled on a topic (`B-123`) and records each candidate unapproved. A term is
auto-approved once the same expansion is found in `APPROVAL_THRESHOLD` (3) separate documents.
Two different expansions for one acronym flag both rows as ambiguous, so neither is used.

**Entity ruler** (`ner.py`, optional `ner` extra). Approved terms compile to spaCy
`EntityRuler` patterns, which override statistical NER. Each pass reads the table as it is
when it starts.

## Design choices

- **Precision over recall for places.** Ambiguous names (common first names, words that are
  also places) and demonyms are never matched, and phrases that merely contain a place name
  (a treaty named for a city) are consumed first. A wrong tag puts a document in the wrong
  column of a comparison; a missing tag only leaves it out of one.
- **The place vocabulary is in code, not config.** ISO codes are settled facts, and changing
  the list changes the basis fingerprint, so every source is re-examined.
- **A bad pattern replaces extraction rather than degrading it.** The ruler overrides the
  model, which is why harvested terms start unapproved and corroboration is counted in
  documents, not occurrences. A glossary that repeats a term forty times has said it once.
- **Rejected terms stay as tombstones**, so the next harvest does not re-create them.
- **Harvest reads only on-topic documents** (`B-123`). Reading everything filled the approval
  queue with tens of thousands of terms from unrelated fields (radio specifications, clinical
  codes), burying the few worth approving. A document not yet labelled waits for its label; one
  labelled off-topic is never read for terms.

### Places

`sources.places` lets coverage be stated per place and results filtered by geography (`P2-23`,
§7.2); search, the explore API and Gaps read it. It is mechanical: the worker never calls a
model (§2.1), and the pass does not need the embedder either, so it runs as soon as a page is
chunked and a deployment without an embedder still gets places.

- **The text decides; the domain only helps.** A country-code or national-government suffix says
  where the publisher is, which is not what the page is about: a foreign publisher writing about
  another country is the case it gets wrong. The bar it lowers depends on how strongly the domain
  says it: a national government's page naming its country once is about it, while a university
  or company page naming its country once is usually giving its address, so two. A generic
  top-level domain, or a country code sold as a generic name, says nothing.
- **What decided is recorded** in `sources.place_evidence`, so a tag can be checked by reading it
  rather than re-running the pass. A source with no live text stays NULL.
- **Tags go stale by the rule topic labels use**: a basis fingerprint (method, thresholds,
  names, gazetteer terms) that changes when any of them does, a live chunk newer than the
  examination, and for the entity signal a place edge written since. The worker pass follows
  `worker.retopic`'s shape: a predicate queue, a cursor so a report-only pass still advances,
  a transaction per batch so a killed pass keeps what it committed.
- **Gazetteer terms vote only when unambiguous.** An ambiguous term (canonical form too) is left
  out: the table can only offer candidates for it, and counting one as a vote for a country would
  decide what the resolver deliberately leaves undecided.
- **The comparison set is configuration already held** (§7.2): the source-tier map's government
  patterns name one national suffix per place compared, and the gazetteer's jurisdictions name
  the places someone seeded agencies for. The union, sorted by name, with no corpus scan, so the
  answer does not depend on what the crawl reached.

<a id="place-calibration"></a>**Calibration.** The thresholds were set by reading random samples
of tagged sources beside their titles and URLs on a real crawled corpus; `python -m
worker.places` prints the distribution and a sample, which is where the next calibration starts.
Before the reference-list rules, most wrong tags came from citations: each cited publisher's or
conference's city adds a mention, and a long paper accumulates enough to tag a country it never
discusses. So text after the first reference-list heading (past a minimum position) is ignored,
and so is any line that reads as a citation entry (a year plus a citation word) wherever it sits,
since many pages carry references under no heading or mid-text; these lines were a few per cent
of the text and read as citations when sampled. What remains wrong is mostly the publisher's own
country on listing pages (a journal issue's contents, a department's publications) that name it
a few times. Raising the domain bar did not separate those from real pages about the country, so
it is a known error rather than tuned.

<a id="place-names"></a>**Place names** (`placenames`). Reference data, not configuration: ISO
3166-1 settles which country a name denotes, so it lives in code beside the matcher, and any
change re-examines every source through the basis fingerprint.

- A country is its alpha-2 code; `EU` is ISO's reserved code for the union, because a document
  about union-level regulation is about neither one member state nor none. A city is its
  UN/LOCODE without the space: five characters whose prefix *is* the country, so a consumer
  rolls a city up with `code[:2]`, length tells the kinds apart, and neither can be confused with
  an ISO 3166-2 code, which has a hyphen. City-states have no separate city code.
- Cities are large metropolitan areas, not a hand-picked list; a missing city is still tagged
  with its country when the country is named, and adding one is a line (which re-examines the
  corpus). States, provinces and regions are matched as names of their country (not stored as
  codes, since nothing asks questions at that level yet): a document about a state's regulation
  names the country too rarely to tag otherwise.
- Names match case-sensitively as proper nouns, plus all-caps for headings and some PDFs.
  `AMBIGUOUS` holds forms routinely meaning something else (common names, a US state that is
  also a country, a word that is also a bird). Demonyms and adjectives are not matched: most are
  also a language's name, and quoting a foreign-language title does not make a document about
  that country. `NOT_PLACES` (a treaty or newspaper named for a city) are consumed first. The
  matcher tries longest names first, so a containing name or phrase wins, and uses lookarounds
  rather than `\b` because several names end in a full stop.

### The gazetteer

Generic NER does not know domain entities: an agency's full name may resolve as an ORG, but a
scheme's name, a metric or a short acronym will not. The table is loaded into spaCy's
`EntityRuler` so its matches take precedence over statistical NER (§5.6), and its alias lists are
exactly what entity resolution needs, so the two share it. It is bootstrapped, not hand-written:
about fifty terms seeded, then grown by harvest (§5.6: "do not hand-write it — bootstrap it").

<a id="ruler"></a>**Loading the ruler** (`compile_patterns`, `ner.py`). Every pattern is an
*override*: whatever it matches stops being the model's question, so a bad pattern does not
degrade extraction, it silently replaces it.

- *Unapproved rows do not load*: they are where harvested and model-proposed terms wait, and
  loading them would make the approval queue decorative. The SQL filters them and
  `compile_patterns` re-checks, deliberately, for callers that build patterns from their own list.
- *An ambiguous row keeps its canonical form and loses its aliases.* Ambiguity is about the short
  ways of naming a thing; the full form a curator wrote is not a mention whose context is
  insufficient, and withholding it would cost the most reliable surface form for nothing.
- *Colliding surface forms are withheld even when nothing is flagged.* The flag is hand-maintained
  and drifts; two rows sharing a surface is the same fact observed. Otherwise the ruler keeps the
  first pattern it saw and row order chooses between jurisdictions. Each withheld form is
  *returned* with a reason (`unapproved`, `rejected`, `ambiguous`, `collision`, `no_patterns`),
  including the partial case of a canonical that loads while an alias does not, because an
  approved term that never matches is the failure curation is least able to notice. Admin's
  approval screen shows these verdicts (`loading_report`).
- *Case.* A short all-caps form matched case-insensitively fires on the ordinary English word,
  each hit becoming a curated, high-precedence entity; a long form matched case-sensitively only
  misses lower-cased prose. That rule is the most consequential line in the module.
- *Labels are upper-cased §5.6 types, not spaCy's scheme*, so a gazetteer match is never
  indistinguishable from a model's guess about a capitalised word. Each pattern carries its row
  id (`ent.ent_id_`), so an alias arrives already resolved, which §5.5 cannot recover from the
  span alone.
- *Before `ner`.* spaCy's entity spans do not overlap and the first component to claim one keeps
  it, so a ruler after `ner` cannot override it. The two placements look identical in a smoke
  test and differ on exactly the terms the gazetteer exists for. A `blank` pipeline (tokenizer
  and ruler, no model) is an honest configuration where curated terms are what matter.
- *Per process.* "At worker startup" means "not per document"; each `python -m` pass reads the
  table as it stands and caches the pipeline for its own life, so an approval takes effect on the
  next pass without a restart. spaCy is an optional extra (`meridian-worker[ner]`) because nothing
  in the fetch loop runs NER yet (`P5-01`), and the patterns are built without it, so what loads
  is testable without spaCy.

<a id="harvest"></a>**The acronym harvest** (`P5-02`, §5.6 step 2). Documents define acronyms on
first use, and `Full Name (ACRONYM)` is one regex over extracted text, by far the highest-yield
source. The regex is the easy part; the rest is the filter, because `(PDF)`, `(see Figure 3)` and
currency codes match the same shape, and each one admitted is permanent noise in what entity
resolution trusts.

- *An expansion may not cross a sentence boundary*: stitching across a full stop invents a term
  that appeared nowhere. A single newline is not a boundary, because extracted PDF text breaks
  lines mid-sentence and a two-column report's definitions routinely span one; a blank line is.
- *Anchored at both ends*: read right to left, one word per letter, connectives skippable; the
  first word must carry the first letter and the last the last, since an expansion starting
  mid-phrase is how a plausible wrong one gets in.
- *Only `Full Name (ACR)`*: the reverse form is rarer here and the same bracket shape glosses
  anything at all, so accepting it would accept every parenthetical.
- *Whole documents*: chunks are rejoined, because a definition split across a chunk boundary is
  invisible to both halves; chunks do not overlap (`P2-02`), so the join reconstructs the text.
- *Linear time* (`B-85`): clause boundaries are found once per document, since rescanning the
  prefix for every bracket made long reports quadratic, and only the last stretch of a clause is
  tokenised. Both give the same decisions as a full scan.
- *Display spelling*: words are rejoined as the document wrote them ("multi-agent", not
  "multi - agent"); patterns tokenise either the same way, so this changes what a person reads,
  not what matches. A name containing a clause character (a semicolon; not a comma, which
  belongs in many names) is rejected; an author list that slips through is never corroborated.
- *Type from the head word* (`B-70`). English puts the head last or before the first preposition.
  Only heads that say one thing set a type ("…Authority", "…Scheme"); "Service", "Group",
  "System", "Framework", "Network" and "Law" are too mixed and stay `concept`, the type that
  claims least: a concept corrected later costs a curator one dropdown, while a wrong `agency`
  reads as established fact. With every term a concept, the type told the ruler, the approval
  queue and search seeding nothing. `retype_harvested` brings older rows up to date: never a
  curated row, and a repair that would collide with an existing row is skipped and counted.

**Approval and its safeguards.**

- Nothing harvested is approved: an approved row overrides the model on every document that
  mentions it. Auto-approval needs the same expansion in three separate documents (§5.6 allows
  a frequency threshold): a report defining a term in its glossary and forty sections has said
  it once, and counting hits would approve one author's typo. Case-insensitive matching on the
  expansion keeps a heading's capitalisation from fragmenting one term into two rows (§5.5).
- A regex may not edit curated data: an approved row is loaded, so an alias appended to it would
  take effect with nobody agreeing. Its count still rises.
- Two expansions for one acronym is the finding: both rows are flagged ambiguous, keeping both
  out of the ruler and handing the mention to the resolver, which sees the document. Keeping the
  first would decide by row order and leave no trace. Rejected rows do not count as a competing
  reading.
- **Rejection is a third state** (`P6-13`), a timestamp rather than a boolean, because "when was
  this decided" is what is asked of a rejection nobody remembers, and because deleting would let
  the next harvest file the same term again. A rejected row is a tombstone: its count is not
  bumped, or a later document could push it over the threshold and bring back approved the term
  somebody turned down. Definitions matching a rejected term are counted per run; a climbing
  number means documents keep asserting what a curator keeps rejecting. Clearing the rejection
  returns the term to the queue: a judgement made on two occurrences is worth revisiting at
  twenty.
- The ambiguity flag means "the short ways of saying this are not decidable" (§5.5's middle
  band: a wrong resolution corrupts the graph invisibly; an unresolved mention stays visible).
  Jurisdiction alone does not settle it: within one country an acronym can be a domain term or
  unrelated business vocabulary.
- The harvest's queue is `acronyms_harvested_at IS NULL` (on-topic documents only), the novelty
  gate's shape: a killed pass keeps what it committed, with no cursor and nothing to reconcile
  if two overlap. Its count fields are logged as `terms_created`, not `created`, because `logging`
  raises when an `extra` key shadows a `LogRecord` attribute; the nightly harvest died on that
  every night (`B-21`).

## Operating it

- **Admin → Gazetteer approvals** lists pending terms and approves or rejects them. Approving a term
  shows whether it will actually load: a surface form that two rows share is withheld from
  the matcher.
- `python -m worker.places` reports tags it would change; `--apply` writes.

## Failure modes and traps

- Terms queued before `B-123` came from every document, so many are from unrelated fields.
  That backlog is waiting for the operator to judge.

## Tests

`tests/integration/test_places.py`, `test_harvest.py`, `test_admin_gazetteer.py`,
`test_translations.py`; `tests/unit/test_places.py`, `test_gazetteer.py`, `test_ner.py`,
`test_acronym_lookback.py`.
