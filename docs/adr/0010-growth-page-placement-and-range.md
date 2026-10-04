# 0010. The growth page has its own section and opens on 30 days

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-140`
- **Refines:** [0005](0005-growth-page-for-readers.md)

## Context

ADR 0005 accepted a reader page for corpus growth; the mock (`docs/design/CorpusGrowth.dc.html`)
left two choices open: where it lives and which window it opens on.

## Decision

- **Its own top-bar section**, "Growth", beside Map and Gaps, rather than a panel on the
  Explore landing.
- **Opens on the last 30 days**, with 7 days and all time one click away, and **filterable by
  topic**: choosing topics narrows the daily chart, the headline counts and the table to them.
  The window and topics are in the URL, so a filtered view can be linked.

## Consequences

- One more item in the top bar.
- 30 days shows the trend and the days the crawl was off without one week's noise. Days are
  calendar days in the display zone (ADR 0009).

## Alternatives considered

- **A panel on the landing.** Less prominent, and the landing already carries the counts.
- **Opening on 7 days or all time.** A week is too short to read a trend; all time makes recent
  days thin.
