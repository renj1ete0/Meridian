# 0013. Find says what search does in plain words

- **Status:** Accepted
- **Date:** 2026-10-07
- **Tasks:** `B-99`

## Context

Find's search field carried two pieces of implementation language: a `hybrid` badge naming the
retrieval mode, and the note "Filters apply before the vector search." Both were in the
approved mocks and defended by design-system §8, because the facts behind them matter to a
reader: there are two ways a passage is found, and filters restrict what is searched rather
than trimming results afterwards. But the reader surfaces had otherwise moved to plain words
(`P6-44`), with the summary line already saying "matched the words · near in meaning". The
operator left the choice between keeping and rewording to the build.

## Decision

Keep both facts and say them in a reader's words. The badge reads `words + meaning`, the two
things search matches on, in the summary line's vocabulary. The note reads "Filters narrow
what is searched, not what is shown", which carries the consequence a reader needs: a short
list under filters is what the corpus holds, not results hidden after the fact. The mocks and
design-system §8 change with the code, so the build still matches its designs.

## Consequences

- The badge describes how Find searches, not how this search went. A search that ran on words
  alone, because no passage vectors were reachable, is still reported in the results by the
  degraded notice.
- Tests fail if either string goes back to `hybrid`, `vector`, `lexical` or `embedding`.
- Operator and Admin surfaces keep the technical terms; this applies to reader screens.

## Alternatives considered

- **Keep the wording.** Accurate, and defended in §8, but it asks a reader to know what a
  vector search is to understand a sentence about filters.
- **Drop the badge and the note.** Shorter, but loses the two facts that make a thin result
  set readable.
