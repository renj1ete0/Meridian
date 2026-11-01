# LAN names

Lets the URL of a machine on the LAN use its `.local` name (`http://gpu-machine.local:8101`)
inside the containers. A home network rarely gives its machines DNS names, and the operator
may not control the router; Windows and Linux machines announce a `.local` name over mDNS
instead. A container cannot hear those announcements, so a small service on the host's
network answers for it.

- **Code:** `services/worker/worker/lanresolve.py` (the resolver),
  `packages/meridian_core/meridian_core/lanhosts.py` (what the clients read)
- **Tasks:** `B-250`
- **Decisions:** [ADR 0021](../adr/0021-the-stack-calls-the-gpu-machine.md) (the stack calls
  the GPU machine at a fixed address; this keeps the address current)

## How it works

1. The `lan-resolver` service starts with the URL settings that may name the GPU machine
   (`MERIDIAN_EMBEDDER_URL`, `MERIDIAN_RERANKER_URL`, `LOCAL_LLM_URL`,
   `LOCAL_LLM_HEALTH_URL`, `LOCAL_CHAT_LLM_URL`) and `MERIDIAN_LAN_NAMES` for any other name.
2. Every minute it sends one mDNS question per `.local` name to the multicast group and
   writes the answers to `${DATA_ROOT}/lan/hosts.json`, atomically.
3. The services that call the GPU machine (`api`, `embed`, `scheduler`, `orchestrator`) mount
   that folder read-only at `/run/meridian-lan`. The embedder client's transport and the
   local model calls swap a `.local` host for its address just before sending; the `Host`
   header keeps the name.
4. A name the file does not know is left alone, so outside a container (development, tests)
   the system resolves it as it always has.

## Design choices

- **A service on the host's network, not host configuration.** mDNS is multicast on the
  LAN segment and a bridge network does not forward it, so the lookup has to happen in the
  host's network namespace. Turning on mDNS in `systemd-resolved` and pointing the
  containers' DNS at it also works, but needs root on the host and differs between Fedora
  CoreOS, other Linux hosts and Docker Desktop; a container behaves the same under Docker
  and rootless podman (both measured on 2026-11-01).
- **It holds nothing worth taking.** On the host's network it can reach whatever the host
  can, so it is given the URL settings only, never `.env`, with a read-only root, no
  capabilities and no new privileges. `tests/unit/test_compose_topology.py` keeps it so.
- **The last address is kept** when a machine stops answering: it is probably off, and its
  clients' connect timeouts already say so (`B-223`). Dropping the address would turn that
  into "name not known" and lose it for when the machine returns.
- **The standard library only**: one question with the unicast-response bit, answers parsed
  with name compression, malformed packets refused. A Windows machine answers within
  milliseconds.

## Configuration

| Variable | Default | |
|---|---|---|
| `MERIDIAN_LAN_NAMES` | unset | Further `.local` names to resolve, comma-separated |
| `MERIDIAN_LAN_RESOLVE_EVERY_S` | 60 | How often the resolver asks |
| `MERIDIAN_LAN_HOSTS_FILE` | `/run/meridian-lan/hosts.json` | Where it writes and the clients read |

The egress firewall (`deploy/egress-restrict.nft`) admits LAN machines by address and port
(`lan_allow`); a name changes nothing there. Give the GPU machine an address that does not
move (a DHCP reservation, or a static address outside the router's pool) and put that
address in `lan_allow`.

## Operating it

`docker compose run --rm lan-resolver python -m worker.lanresolve --once` prints what it finds.
`${DATA_ROOT}/lan/hosts.json` shows each name, its address and when it last answered.

## Failure modes and traps

- **Name not known inside a container** means the service has no `/run/meridian-lan` mount,
  or the resolver has never had an answer (the machine was off since the stack started).
- **Ping is not a test**: Windows' firewall drops ICMP by default while still answering mDNS
  and serving its ports. Test with the port: `curl http://<address>:8101/v1/models`.
- **A machine on another subnet or over a VPN** is not reachable by mDNS at all; use its
  address or a DNS name there.

## Tests

`tests/unit/test_lan_names.py`: real reply packets (Windows, and a compressed one), malformed
packets refused, the resolver's pass (last address kept, moved machine followed, dropped
names), URL rewriting, the transport and the local model call reaching the address.
`tests/unit/test_compose_topology.py`: only the resolver shares the host's network, it holds no
secrets, and every service that calls the GPU machine mounts its answers.
