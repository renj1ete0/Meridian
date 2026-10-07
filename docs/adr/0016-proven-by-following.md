# 0016. A host is proven for following by what following found

- **Status:** Accepted
- **Date:** 2026-10-07
- **Tasks:** `B-155` (amends `B-115`)

## Context

`B-115` gave a proven host's links a boost larger than any tier: a host whose examined pages
were at least a quarter on a topic had its links fetched before any unjudged host's. Loop run
17 found the boost spent on the wrong hosts. About 900 of its 2,300 fetches went to five hosts
that yielded about 4%, each proven on twenty-odd pages that a search, or a link from an
on-topic page, had picked. Pages picked for being on a topic say little about where the rest of
a site's links lead.

A backtest over runs 15–17 supported the reading: hosts proven only on picked pages mostly
yielded 1–5% on followed links, and where a host had a record on pages reached by following,
that record predicted the next followed page better than the record over every page.

## Decision

Judge a host's links by the pages its links have led to. Keep a second count per host, over
pages reached by following a link or a sitemap. With enough of them, that share alone decides
proven or thin. Without enough, a host that looks on-topic overall is *promising*: explored like
a host on-topic pages vouch for, a few dozen links at a moderate boost, until its followed pages
are read. The off-topic gate is unchanged. The pass that applies verdicts to waiting links runs
hourly, so a change of standing takes effect within the hour.

## Consequences

- Fewer fetches spent on general sites whose search hits happen to be on a topic.
- A specialist site proven by search waits an hour or two longer for most of its links (one
  in the backtest yielded 97%); its links are deferred, not dropped.
- Drift within a host (a repository whose early pages were its best) is not addressed; a
  recency window on the followed record is the candidate if loop runs keep showing it.
