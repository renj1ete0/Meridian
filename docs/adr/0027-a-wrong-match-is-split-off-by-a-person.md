# 0027. A match graded wrong can be split off by a person, without moving its relations

- **Status:** Accepted
- **Date:** 2026-11-07
- **Tasks:** `B-273`, `B-274`

## Context

Since `B-273` the graph audit samples matches at resolution: a name a model read that the
resolver joined to a node the graph already held. A merge graded wrong has an exact undo in
Possible duplicates; a match had none, so a grade of wrong measured a conflation without any way
to correct it. Three responses were weighed: grade only, and let a high error rate tighten the
resolver's threshold; a split that gives the name a node of its own; or an exact split, which
would need resolution to record every relation and tag each match fed.

## Decision

The operator chose to build the split now. On a match graded wrong, Admin › Graph audit offers
"Split this name off": the name becomes a node with its type, jurisdiction, passages and the
model's provenance, and the node it had joined loses the alias and the passages the match added
(unless another match still standing brought the same). It needs the permission that decides
merges, since it undoes the same decision, and a merge in Possible duplicates rejoins the two.

Relations and tags written from the mention stay on the node it joined. They name no mention, so
nothing tells them apart from the node's own; making the split exact is the third option, and
is not built.

## Consequences

- A conflation found by the audit can be corrected where it was found.
- The correction is partial: a relation the run wrote from the wrongly matched name still points
  at the old node and has to be read and moved by a person. The card says so beside the button.
- If graded draws show matches going wrong often, the threshold and the exact split are the next
  steps; the error rate per model is on the same page.
