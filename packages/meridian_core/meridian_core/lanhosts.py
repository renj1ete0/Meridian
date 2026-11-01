"""``.local`` names for machines on the LAN, inside containers (`B-250`).

A container cannot resolve a ``.local`` name: mDNS is multicast on the LAN, and it does not
cross a bridge network. The ``lan-resolver`` service, on the host's network, answers them
(`worker.lanresolve`) into a file this module reads, so a URL such as
``http://gpu-machine.local:8101`` reaches the address the machine announces. See
docs/features/lan-names.md.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx

#: Where the resolver writes, and every service reads.
DEFAULT_HOSTS_FILE = "/run/meridian-lan/hosts.json"

#: The URL settings whose hosts the resolver looks up.
URL_VARIABLES = (
    "MERIDIAN_EMBEDDER_URL",
    "MERIDIAN_RERANKER_URL",
    "LOCAL_LLM_URL",
    "LOCAL_LLM_HEALTH_URL",
    "LOCAL_CHAT_LLM_URL",
)

_cache: tuple[str, float, dict[str, str]] | None = None


def hosts_file() -> str:
    return os.environ.get("MERIDIAN_LAN_HOSTS_FILE", "").strip() or DEFAULT_HOSTS_FILE


def is_mdns(host: str | None) -> bool:
    """Whether ``host`` is a name only mDNS answers."""
    return bool(host) and host.lower().rstrip(".").endswith(".local")


def mdns_names(urls: Iterable[str]) -> set[str]:
    """The ``.local`` hosts in ``urls``, lower-cased."""
    names = set()
    for url in urls:
        host = urlsplit(url.strip()).hostname if url and url.strip() else None
        if is_mdns(host):
            names.add(host.lower().rstrip("."))
    return names


def addresses() -> dict[str, str]:
    """The resolver's answers, name to address; empty when there is no file.

    Re-read when the file changes, so a machine given a new address is followed.
    """
    global _cache
    path = hosts_file()
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    if _cache and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except OSError, ValueError:
        return {}
    found = {
        name.lower(): entry["address"]
        for name, entry in (raw.items() if isinstance(raw, dict) else [])
        if isinstance(entry, dict) and isinstance(entry.get("address"), str)
    }
    _cache = (path, mtime, found)
    return found


def lan_url(url: str) -> str:
    """``url`` with a ``.local`` host swapped for its address, when the resolver knows it.

    Unchanged otherwise: outside a container the system resolves the name itself.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if not is_mdns(host):
        return url
    address = addresses().get(host.lower().rstrip("."))
    if not address:
        return url
    netloc = address if parts.port is None else f"{address}:{parts.port}"
    if "@" in parts.netloc:
        netloc = parts.netloc.rsplit("@", 1)[0] + "@" + netloc
    return urlunsplit(parts._replace(netloc=netloc))


class LanTransport(httpx.AsyncBaseTransport):
    """An httpx transport that sends a ``.local`` host to its resolved address.

    The ``Host`` header keeps the name the URL was written with.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if is_mdns(request.url.host):
            swapped = lan_url(str(request.url))
            if swapped != str(request.url):
                request.url = httpx.URL(swapped)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
