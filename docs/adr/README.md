# Architecture decision records

Decisions the operator has made, one per file, numbered in the order they were taken. Each
says what was decided, why, what it costs, and what was considered instead. A record is
not edited to reverse it; a later record supersedes it, and the earlier one says so at its
top.

`TASKS.md` entries that a decision settles link to the record. The specs remain the
reference for how the system works; these records are the reference for why it was
chosen.

| # | Decision | Date | Status |
|---|---|---|---|
| [0001](0001-production-runs-on-a-server.md) | Production runs on a server, with an optional GPU | 2026-10-04 | Accepted |
| [0002](0002-model-routing.md) | Which model answers what, and in which order | 2026-10-04 | Accepted |
| [0003](0003-external-assistants-over-mcp.md) | External assistants connect over MCP | 2026-10-04 | Accepted |
| [0004](0004-hosted-claude-generation.md) | Hosted Claude rows use the current generation at high effort | 2026-10-04 | Accepted |
| [0005](0005-growth-page-for-readers.md) | Readers can see how the corpus grew | 2026-10-04 | Accepted |
| [0006](0006-stricter-triage-for-very-long-documents.md) | A stricter sample bar for very long documents | 2026-10-04 | Accepted |
| [0007](0007-half-precision-vector-index.md) | A half-precision vector index, if it measures as well | 2026-10-04 | Accepted, built |
| [0008](0008-release-channels.md) | Code is published to GitHub; images wait | 2026-10-04 | Accepted |
| [0009](0009-times-stored-in-utc-shown-in-a-display-zone.md) | Times are stored in UTC and shown in one display zone (GMT+8 by default) | 2026-10-04 | Accepted |
| [0010](0010-growth-page-placement-and-range.md) | The growth page has its own section and opens on 30 days, filterable | 2026-10-04 | Accepted |
| [0011](0011-tokens-may-be-set-not-to-expire.md) | Tokens may be set not to expire, and say so | 2026-10-04 | Accepted |
| [0012](0012-web-linting-with-oxlint-and-prettier.md) | The web package is linted by oxlint and formatted by Prettier | 2026-10-04 | Accepted |
| [0013](0013-find-says-what-search-does-in-plain-words.md) | Find says what search does in plain words | 2026-10-07 | Accepted |
| [0014](0014-other-languages-scored-as-their-english-versions.md) | Pages in other languages are scored as their English versions would be | 2026-10-07 | Accepted |
| [0015](0015-followed-links-follow-the-evidence.md) | Followed links follow the evidence, upward as well as down | 2026-10-07 | Accepted |

## Template

```markdown
# NNNN. Title

- **Status:** Proposed | Accepted | Superseded by NNNN
- **Date:** YYYY-MM-DD
- **Tasks:** IDs

## Context
## Decision
## Consequences
## Alternatives considered
```
