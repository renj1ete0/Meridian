# 0021. The stack calls the GPU machine; the GPU machine keeps no state

- **Status:** Accepted
- **Date:** 2026-10-24
- **Tasks:** `B-222`, `B-223`, `B-224`, `B-225`, `B-226`, `B-227`, `B-228`

## Context

The stack is to run on a small always-on machine under podman, with the embedding model and a
local language model on a separate GPU machine that is switched on only some of the time. Either
side could start the conversation: the small machine asking the GPU machine for vectors and
answers when it is up, or the GPU machine announcing itself, or pulling work from the database.

## Decision

The small machine calls the GPU machine over HTTP at a fixed address, and learns whether it is up
by polling its health checks with a backoff. Nothing on the GPU machine calls back, discovers
anything, or reaches the database. The GPU machine runs only the services themselves (the
embedder and a llama.cpp server), with no extra container to mediate the connection. A status
route for the operator is admin-only, apart from the public liveness check.

## Consequences

- Postgres stays on the small machine's internal network; the GPU machine is stateless and can be
  rebuilt or switched off at any time.
- Every client that may call the GPU machine needs a short connect timeout and a remembered
  outage (`B-223`, `B-224`), since a machine that is off behind a router drops packets rather than
  refusing.
- Noticing that the machine came back costs up to one polling interval: a minute or so for the
  embedder's backfill, five minutes for synthesis (`B-227`).
- The GPU machine needs a fixed address (a DHCP reservation or a VPN name).

## Alternatives considered

- **The GPU machine pulls work from the database.** Rejected: Postgres would be exposed to the
  LAN, and searches would still need an embedder on demand.
- **The GPU machine announces itself.** Rejected: it needs an endpoint on the small machine and
  saves at most one polling interval.
