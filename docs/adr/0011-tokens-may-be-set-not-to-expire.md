# 0011. Tokens may be set not to expire, and say so

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-138`, `B-146`
- **Refines:** [0003](0003-external-assistants-over-mcp.md)

## Context

ADR 0003 made tokens expire after 90 days by default. The Admin mock offered "never" as well,
and the question was whether to keep it.

## Decision

A token may be issued with no expiry, for a device the operator owns and controls. Such a
token is flagged "no expiry" wherever tokens are listed, in Admin and by `api.tokens list`, so
it stays a visible choice rather than a forgotten one. The default stays 90 days.

## Consequences

- Revocation is the only way such a token stops working; the list makes it easy to find.
- A lost device holding one is a standing risk until revoked, which is why it is flagged.

## Alternatives considered

- **Every token expires (at most a year).** Safer by default, at the cost of re-issuing tokens
  for devices that never leave the operator's hands.
