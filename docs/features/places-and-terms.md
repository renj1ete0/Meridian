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
  queue with vocabulary from unrelated fields.

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
