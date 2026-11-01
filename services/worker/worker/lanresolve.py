"""Answer ``.local`` names for the containers (`B-250`).

Runs on the host's network, where mDNS works, and writes what it finds to the file
`meridian_core.lanhosts` reads. Only names that appear in the URL settings are asked
for. A name that stops answering keeps its last address: the machine is probably off,
and its clients' own timeouts say so. See docs/features/lan-names.md.

    python -m worker.lanresolve            # every minute, until stopped
    python -m worker.lanresolve --once     # one pass, printing what it found
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import struct
import time

from meridian_core.lanhosts import URL_VARIABLES, hosts_file, mdns_names
from meridian_core.logging import get_logger

log = get_logger(__name__)

MDNS_GROUP = ("224.0.0.251", 5353)
TYPE_A = 1
#: Class IN with the "unicast response" bit (RFC 6762 §5.4): the answer comes back to
#: this socket rather than to the group.
CLASS_IN_QU = 0x8001
DEFAULT_EVERY_S = 60.0
DEFAULT_TIMEOUT_S = 2.0


def encode_query(name: str, query_id: int = 0) -> bytes:
    """One mDNS question for ``name``'s IPv4 address."""
    labels = name.rstrip(".").split(".")
    if not all(0 < len(label.encode()) < 64 for label in labels):
        raise ValueError(f"not a DNS name: {name!r}")
    question = b"".join(bytes([len(p.encode())]) + p.encode() for p in labels) + b"\0"
    return (
        struct.pack("!HHHHHH", query_id, 0, 1, 0, 0, 0)
        + question
        + struct.pack("!HH", TYPE_A, CLASS_IN_QU)
    )


def _read_name(data: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    """A possibly compressed name at ``offset``, and the offset after it."""
    if depth > 16:
        raise ValueError("name compression loops")
    labels: list[str] = []
    while True:
        if offset >= len(data):
            raise ValueError("name runs past the packet")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            rest, _ = _read_name(data, pointer, depth + 1)
            labels.append(rest)
            return ".".join(label for label in labels if label), offset + 2
        if length == 0:
            return ".".join(labels), offset + 1
        labels.append(data[offset + 1 : offset + 1 + length].decode(errors="replace"))
        offset += 1 + length


def parse_answers(data: bytes, name: str) -> list[str]:
    """The IPv4 addresses ``data`` gives for ``name``; refuses a malformed packet."""
    if len(data) < 12:
        raise ValueError("shorter than a DNS header")
    _, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", data[:12])
    if not flags & 0x8000:
        return []  # a question, not an answer (another host asking)
    offset = 12
    for _ in range(qdcount):
        _, offset = _read_name(data, offset)
        offset += 4
    wanted = name.lower().rstrip(".")
    found: list[str] = []
    for _ in range(ancount + nscount + arcount):
        owner, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("truncated record")
        rtype, _, _, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(data):
            raise ValueError("record data runs past the packet")
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rtype == TYPE_A and rdlength == 4 and owner.lower().rstrip(".") == wanted:
            found.append(socket.inet_ntoa(rdata))
    return found


def resolve(name: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str | None:
    """``name``'s address by mDNS, or None when nothing answered in time."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.settimeout(timeout_s)
        sock.sendto(encode_query(name), MDNS_GROUP)
        deadline = time.monotonic() + timeout_s
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, _ = sock.recvfrom(9000)
            except TimeoutError:
                return None
            try:
                addresses = parse_answers(data, name)
            except ValueError:
                continue
            if addresses:
                return addresses[0]
    return None


def wanted_names(environ: dict[str, str] | None = None) -> set[str]:
    """The ``.local`` names in the URL settings, plus any in ``MERIDIAN_LAN_NAMES``."""
    env = os.environ if environ is None else environ
    names = mdns_names(env.get(var, "") for var in URL_VARIABLES)
    names |= {
        n.strip().lower().rstrip(".")
        for n in env.get("MERIDIAN_LAN_NAMES", "").split(",")
        if n.strip()
    }
    return names


def write_hosts(path: str, entries: dict[str, dict[str, str]]) -> None:
    """Write atomically, so a reader never sees half a file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(entries, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_hosts(path: str) -> dict[str, dict[str, str]]:
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except OSError, ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def one_pass(names: set[str], path: str, resolver=None) -> dict[str, dict[str, str]]:
    """Look each name up once and write the file; a name that did not answer keeps its entry."""
    entries = {name: entry for name, entry in read_hosts(path).items() if name in names}
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    ask = resolver or resolve
    for name in sorted(names):
        address = ask(name)
        if address is None:
            log.info("no mDNS answer; keeping the last address", extra={"host": name})
            continue
        previous = entries.get(name, {}).get("address")
        if previous != address:
            log.info("address found", extra={"host": name, "address": address, "was": previous})
        entries[name] = {"address": address, "seen": now}
    write_hosts(path, entries)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="one pass, then print the file")
    args = parser.parse_args(argv)
    path = hosts_file()
    names = wanted_names()
    if args.once:
        print(json.dumps(one_pass(names, path), indent=1))
        return 0
    every = float(os.environ.get("MERIDIAN_LAN_RESOLVE_EVERY_S") or DEFAULT_EVERY_S)
    if not names:
        log.info("no .local names in the URL settings; nothing to resolve")
    while True:
        if names:
            one_pass(names, path)
        time.sleep(max(every, 5.0))


if __name__ == "__main__":
    raise SystemExit(main())
