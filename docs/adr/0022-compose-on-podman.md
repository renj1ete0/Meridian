# 0022. Podman runs the compose files; the GPU machine is woken by hand

- **Status:** Accepted
- **Date:** 2026-10-24
- **Tasks:** `B-226`, `B-229`

## Context

The stack is to run on Fedora CoreOS under podman, and the GPU node on Windows with Podman
Desktop. CoreOS's native way to run containers is Quadlet, systemd units generated from
`.container` files. The registry also had an unused Wake-on-LAN field, and §11.7 designed a
daily wake as a safety net for a GPU machine that sleeps.

## Decision

Podman runs the same compose files as Docker, through the `docker-compose` binary on podman's
Docker-compatible socket. What podman needs differently sits in overlays and a few systemd units,
not in a second definition of the services. Wake-on-LAN is not built: the operator switches the
GPU machine on when it is wanted, and the stack uses it whenever it is up.

## Consequences

- One definition of the services for both runtimes; a change to `docker-compose.yml` reaches
  podman hosts without a second edit.
- Podman has no daemon, so a systemd unit runs `compose up -d` at boot, and Watchtower is
  replaced by a timer that pulls and recreates through compose.
- Quadlet's per-container units, journald integration and `podman auto-update` are given up.
- `wake_mac` stays in the registry, stored and unread.

## Alternatives considered

- **Quadlet units on CoreOS.** Rejected: a second, hand-kept definition of every service, which
  drifts from the compose file the Docker hosts and development use.
- **Wake-on-LAN from the orchestrator.** Rejected by the operator; it would also have needed a
  broadcast path out of a bridged container past the egress rules.
