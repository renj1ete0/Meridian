# 0006. A stricter sample bar for very long documents

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-133`

## Context

A long document is embedded from a sample first, and the rest is embedded early only when
the sample's best topic score clears a triage floor (`B-89`). Very long listing pages,
such as indexes of titles, beat that floor with a single matching line and are then
embedded in full, mostly off-topic. Measured on fully embedded sources: for documents of
1,000 passages or more, raising the bar slightly held back no on-topic document in the
sample, and held back clearly more off-topic text.

## Decision

Documents of 1,000 passages or more need a slightly higher sample score before the rest is
embedded early. Shorter documents keep the current floor.

## Consequences

- Holding back delays a document; it deletes nothing. A wrongly held document is still
  embedded, from the last tier.
- The sample of very long on-topic documents was small, so the threshold is re-measured once
  the corpus has more of them.
- The gain shrinks once embedding runs on a GPU (0001). The rule stays because it orders
  the work sensibly at any speed.

## Alternatives considered

- **Raise the floor for every document.** Rejected: at shorter lengths it held back
  on-topic documents.
- **Detect listing pages by their shape.** Possible later. The threshold is simpler and
  was measured.
