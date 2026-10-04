# 0005. Readers can see how the corpus grew

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-140`

## Context

The corpus grows every day, unevenly by topic and by source, and none of that is visible.
The operator sees only the last 24 hours, in Admin → Crawl health. Readers see the totals
as they stand now.

## Decision

A reader-facing page shows growth over time: pages read and kept on a topic per day,
growth per topic, new sites, the map's areas, and the size of the knowledge graph. It can
be viewed over 7 days, 30 days, or all time. A mock is drawn in `docs/design/` and approved
before anything is built, as for every other reader surface.

## Consequences

- Built in `B-140`, with ADR 0010's placement and window. The map's history is the one figure
  that needed recording: `area_build_history` keeps each build's size after the build is pruned.

- Growth is computed from timestamps the database already holds, so no new tracking is
  needed. Anything that cannot be derived from the past (for example, the map's shape on an
  earlier day) is recorded from the day the page ships, and the page says where its history
  starts.
- The page shows topics and counts, never source names, so it discloses no more than Find
  already does.

## Alternatives considered

- **An operator report in Admin only.** It is useful too, and may follow. But the operator
  asked for the reader view first.
