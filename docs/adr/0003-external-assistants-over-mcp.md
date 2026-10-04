# 0003. External assistants connect over MCP

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-138`, `B-139`

## Context

The API already serves a read-only MCP surface at `/mcp`: search, source metadata, what is
new, a corpus overview, and read-only SQL. It refuses every call without a token, and
nothing could issue one. Readers would like to put questions to their own assistant
(Claude, Gemini or another MCP client) and have it answer from the corpus with citations,
on their own subscription.

## Decision

- Tokens can always be issued, from a command on the server and from Admin. Issuing needs
  no public address: what decides who can use a token is where `/mcp` can be reached.
- The issuing screen says where `/mcp` is reachable from: the server itself, the LAN, or
  the public tunnel. When it is public, the screen warns that whoever holds the token can
  read the whole corpus.
- Tokens are scoped to named tools, expire (90 days by default), are shown once and stored
  only as a hash, and can be revoked in one step.
- The surface gains read tools for what the site shows: the knowledge graph (a node, its
  neighbours, routes between nodes), the map's areas, gaps, and corpus growth.
- No write tools. Building the graph stays with the orchestrator and its validation.

## Consequences

- An assistant on the server, the LAN or a VPN needs no port forwarding. A web or mobile
  client needs the public tunnel that compose already provides.
- Every answer an assistant gives can carry Meridian's citations, because every tool
  returns them.
- The read-only database role remains the guarantee: the MCP layer cannot write even if a
  tool were wrong.

## Alternatives considered

- **Only issue tokens when the surface is public.** Rejected: most use is local, and
  whether a token can be issued is a different question from whether it can be used.
- **Let a connected assistant build the graph through write tools.** Deferred. It would
  replace the file relay with a live one, but it needs the validated write group of spec
  §11.6 and full provenance first.
