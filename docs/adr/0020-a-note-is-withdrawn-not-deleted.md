# 0020. A note is withdrawn, not deleted

- **Status:** Accepted
- **Date:** 2026-10-11
- **Tasks:** `B-201`

## Context

A note is a node, written by hand, linked to the nodes it is about. It could be edited but not
removed. Deleting the row would cascade to its links and lose the one layer crawling cannot
recover; the spec's rule for steering is that changes are reversible because nothing is
destroyed. The operator was asked whether removal should delete or keep a record.

## Decision

A note is withdrawn: it is stamped, and it leaves every list, node panel, export, node search
and graph walk. The row and its links are kept, and a withdrawn note can be put back. It is put
back before it is edited.

## Consequences

- A mistaken withdrawal is undone in one step, from the line that names it.
- Withdrawn notes still take space and keep their links; readers of notes and of graph
  neighbours must leave them out, which the one column makes a single condition.
- A note cannot be erased from the interface. Erasing one is a database operation, on purpose.

## Alternatives considered

- **Deleting the row**: simplest, and irreversible; its links would go with it.
- **Withdrawing by removing the links only**: the note would still be listed, and the record of
  what it was about would be lost.
