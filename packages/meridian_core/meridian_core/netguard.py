"""SSRF protection for the fetcher (spec §6.4, §11.8).

Crawl targets come from untrusted pages. Frontier expansion follows links and
citations out of documents the system did not write, so a page linking to
``http://169.254.169.254/`` or ``http://192.168.1.1/`` is not hypothetical — it
is the ordinary case this has to refuse.

Three things make this harder than a substring check on the hostname, and each
has caught real crawlers out:

**A public name can resolve to a private address.** ``evil.example`` with an A
record of ``127.0.0.1`` looks fine as a string. So the check happens *after* DNS
resolution, on the addresses, never on the text.

**A safe response can redirect into the network.** A 200 from a public host can
302 to the metadata endpoint, so every hop is re-checked rather than only the
first URL.

**Addresses have many spellings.** ``::ffff:127.0.0.1`` is loopback wearing an
IPv6 costume, and ``http://2130706433/`` is ``127.0.0.1`` in decimal. Both are
normalised before judgement.

The guard is deliberately allow-nothing-by-default: an address it cannot classify
is refused, because a crawler that fails closed loses a page, and one that fails
open loses the network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Cloud metadata endpoints. 169.254.169.254 is link-local and would be caught
# anyway, but naming them makes the refusal reason legible in a log, and the
# others are ordinary-looking public addresses that are anything but.
METADATA_ADDRESSES: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS, GCP, Azure, DigitalOcean, Oracle
        "fd00:ec2::254",  # AWS IPv6
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud legacy
    }
)

METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {"metadata.google.internal", "metadata.goog", "instance-data"}
)


class BlockedTarget(Exception):
    """Raised when a URL or address must not be fetched.

    Carries the reason so the refusal can be recorded as a ``fetch_attempts``
    outcome rather than vanishing into a generic error.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def normalise_address(ip: IPAddress) -> IPAddress:
    """Unwrap IPv4-mapped and 6to4 IPv6 addresses to the IPv4 they really are.

    ``::ffff:127.0.0.1`` is loopback; judging it as an IPv6 address would let it
    through, since IPv6 loopback is only ``::1``.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
    return ip


def address_verdict(ip: IPAddress) -> str | None:
    """Return a refusal reason for ``ip``, or None if it is safe to fetch.

    Ordered most-specific first so the logged reason is the useful one:
    "cloud metadata endpoint" beats "link-local address".
    """
    ip = normalise_address(ip)

    if str(ip) in METADATA_ADDRESSES:
        return "cloud metadata endpoint"
    if ip.is_unspecified:
        return "unspecified address"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address"
    if ip.is_private:
        return "private address"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_reserved:
        return "reserved address"
    if not ip.is_global:
        # Anything left that is not globally routable — shared CGNAT space,
        # benchmarking ranges, and whatever a future RFC adds. Failing closed
        # here is the point: an unclassifiable address is refused.
        return "non-global address"
    return None


def _as_literal_address(host: str) -> IPAddress | None:
    """Parse a host that is already an address, in any of its spellings.

    Dotted-quad and bracketed IPv6 are the obvious ones. The integer forms are
    the interesting ones: ``http://2130706433/`` and ``http://0x7f000001/`` are
    both 127.0.0.1, and ``getaddrinfo`` resolves them without complaint. The
    post-resolution check would catch them anyway, but refusing here fails fast
    and logs the honest reason rather than a DNS result.
    """
    bare = host.strip("[]")
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    try:
        if bare.lower().startswith("0x"):
            value = int(bare, 16)
        elif bare.isdigit():
            value = int(bare, 10)
        else:
            return None
    except ValueError:
        return None
    if 0 <= value <= 0xFFFFFFFF:
        return ipaddress.ip_address(value)
    return None


async def _default_resolver(host: str, port: int = 443) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]


def check_scheme(url: str, allowed_schemes: list[str]) -> None:
    """Refuse anything outside the allowlist.

    ``file:``, ``gopher:`` and ``data:`` are the classic SSRF escalations, and a
    crawler has no reason to speak any of them.
    """
    scheme = urlsplit(url).scheme.lower()
    if not scheme:
        raise BlockedTarget("no scheme", url)
    if scheme not in {s.lower() for s in allowed_schemes}:
        raise BlockedTarget("scheme not allowed", scheme)


async def assert_url_allowed(
    url: str,
    *,
    allowed_schemes: list[str] | None = None,
    block_private: bool = True,
    block_mixed_dns: bool = True,
    resolver: Resolver | None = None,
) -> list[IPAddress]:
    """Refuse ``url`` if it is unsafe to fetch; return its resolved addresses.

    ``block_mixed_dns`` refuses a hostname when *any* of its addresses is
    private, not merely when all of them are. That is the DNS-rebinding defence:
    a name answering with one public and one private address would otherwise
    pass the check and then connect to whichever the OS picked.
    """
    check_scheme(url, allowed_schemes or ["http", "https"])

    parts = urlsplit(url)
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise BlockedTarget("no host", url)

    if host in METADATA_HOSTNAMES:
        raise BlockedTarget("cloud metadata endpoint", host)

    # A literal address in the URL still gets judged; ip_address() also
    # normalises the decimal and hex spellings of 127.0.0.1.
    literal = _as_literal_address(host)

    if literal is not None:
        addresses = [literal]
    else:
        resolve = resolver or _default_resolver
        try:
            addresses = await resolve(host, parts.port or (80 if parts.scheme == "http" else 443))
        except Exception as exc:  # DNS failure is a refusal, not a crash
            raise BlockedTarget("dns resolution failed", f"{host}: {exc}") from exc

    if not addresses:
        raise BlockedTarget("dns returned no addresses", host)

    if block_private:
        verdicts = [(ip, address_verdict(ip)) for ip in addresses]
        blocked = [(ip, why) for ip, why in verdicts if why]
        if blocked and (block_mixed_dns or len(blocked) == len(verdicts)):
            ip, why = blocked[0]
            raise BlockedTarget(why, f"{host} -> {ip}")

    return addresses


async def assert_redirect_allowed(
    location: str,
    *,
    require_https_final: bool = False,
    is_final: bool = False,
    **kwargs,
) -> list[IPAddress]:
    """Re-check a redirect target. A 200 can 302 into the LAN.

    ``require_https_final`` is checked only on the last hop: an ``http://`` link
    may be *followed*, since most sites simply redirect, but content still
    served in plaintext at the end of the chain is refused — on plain HTTP
    anyone on the path can rewrite the page, and for a crawler feeding a model
    that holds write tools that needs no compromise of the origin.
    """
    if is_final and require_https_final and urlsplit(location).scheme.lower() != "https":
        raise BlockedTarget("plaintext final response", location)
    return await assert_url_allowed(location, **kwargs)
