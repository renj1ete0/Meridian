"""SSRF guard (spec §6.4, §11.8).

Every case here is a way a crawler following untrusted links reaches something
it must not. They are rejection tests almost exclusively — that a public URL
passes is the weak half; that these do not is the point.
"""

from __future__ import annotations

import ipaddress

import pytest

from meridian_core.netguard import (
    BlockedTarget,
    address_verdict,
    assert_redirect_allowed,
    assert_url_allowed,
    check_scheme,
    normalise_address,
)


def resolver_returning(*addresses: str):
    async def _resolve(host: str, port: int = 443):
        return [ipaddress.ip_address(a) for a in addresses]

    return _resolve


def failing_resolver(exc: Exception | None = None):
    # Built inside the function: an exception instance in a default argument is
    # shared across every call, and accumulates a traceback from the first raise.
    exc = exc or OSError("nxdomain")

    async def _resolve(host: str, port: int = 443):
        raise exc

    return _resolve


# ------------------------------------------------------------ classification


@pytest.mark.parametrize(
    "addr,expected_reason",
    [
        ("127.0.0.1", "loopback address"),
        ("::1", "loopback address"),
        ("10.0.0.5", "private address"),
        ("172.16.4.1", "private address"),
        ("192.168.1.1", "private address"),
        ("fc00::1", "private address"),
        ("169.254.1.1", "link-local address"),
        ("fe80::1", "link-local address"),
        ("169.254.169.254", "cloud metadata endpoint"),
        ("100.100.100.200", "cloud metadata endpoint"),
        ("0.0.0.0", "unspecified address"),
        ("224.0.0.1", "multicast address"),
        ("100.64.0.1", "non-global address"),  # CGNAT
    ],
)
def test_unsafe_addresses_are_refused(addr: str, expected_reason: str) -> None:
    assert address_verdict(ipaddress.ip_address(addr)) == expected_reason


@pytest.mark.parametrize("addr", ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700::1111"])
def test_public_addresses_pass(addr: str) -> None:
    assert address_verdict(ipaddress.ip_address(addr)) is None


def test_ipv4_mapped_loopback_is_still_loopback() -> None:
    """``::ffff:127.0.0.1`` is loopback in an IPv6 costume. Judging it as IPv6
    would let it through, since IPv6 loopback is only ``::1``."""
    assert normalise_address(ipaddress.ip_address("::ffff:127.0.0.1")) == ipaddress.ip_address(
        "127.0.0.1"
    )
    assert address_verdict(ipaddress.ip_address("::ffff:127.0.0.1")) == "loopback address"


def test_ipv4_mapped_private_is_still_private() -> None:
    assert address_verdict(ipaddress.ip_address("::ffff:10.0.0.1")) == "private address"


# -------------------------------------------------------------------- schemes


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "gopher://x/", "data:text/html,hi", "ftp://x/"]
)
async def test_dangerous_schemes_are_refused(url: str) -> None:
    with pytest.raises(BlockedTarget) as exc:
        check_scheme(url, ["http", "https"])
    assert "scheme" in exc.value.reason or exc.value.reason == "no scheme"


def test_allowed_scheme_passes() -> None:
    check_scheme("https://example.org/x", ["http", "https"])


# ------------------------------------------------------------- URL resolution


async def test_public_hostname_passes() -> None:
    addrs = await assert_url_allowed(
        "https://example.org/x", resolver=resolver_returning("93.184.216.34")
    )
    assert addrs


async def test_public_name_resolving_to_loopback_is_refused() -> None:
    """The reason the check is on addresses, not on the hostname string: a
    perfectly ordinary name can carry an A record pointing inside."""
    with pytest.raises(BlockedTarget) as exc:
        await assert_url_allowed(
            "https://totally-fine.example/", resolver=resolver_returning("127.0.0.1")
        )
    assert exc.value.reason == "loopback address"


async def test_literal_private_address_is_refused() -> None:
    with pytest.raises(BlockedTarget):
        await assert_url_allowed("http://192.168.1.1/admin", resolver=resolver_returning())


async def test_decimal_encoded_loopback_is_refused() -> None:
    """``http://2130706433/`` is 127.0.0.1 written in decimal."""
    with pytest.raises(BlockedTarget) as exc:
        await assert_url_allowed("http://2130706433/", resolver=resolver_returning())
    assert exc.value.reason == "loopback address"


async def test_metadata_hostname_is_refused() -> None:
    with pytest.raises(BlockedTarget) as exc:
        await assert_url_allowed(
            "http://metadata.google.internal/computeMetadata/v1/",
            resolver=resolver_returning("1.1.1.1"),
        )
    assert exc.value.reason == "cloud metadata endpoint"


async def test_dns_rebinding_mixed_answer_is_refused() -> None:
    """A name answering with one public and one private address would otherwise
    pass, then connect to whichever the OS happened to pick."""
    with pytest.raises(BlockedTarget):
        await assert_url_allowed(
            "https://rebind.example/", resolver=resolver_returning("93.184.216.34", "10.0.0.1")
        )


async def test_mixed_answer_may_be_permitted_when_the_defence_is_disabled() -> None:
    addrs = await assert_url_allowed(
        "https://rebind.example/",
        block_mixed_dns=False,
        resolver=resolver_returning("93.184.216.34", "10.0.0.1"),
    )
    assert len(addrs) == 2


async def test_dns_failure_is_a_refusal_not_a_crash() -> None:
    with pytest.raises(BlockedTarget) as exc:
        await assert_url_allowed("https://nope.example/", resolver=failing_resolver())
    assert exc.value.reason == "dns resolution failed"


async def test_empty_dns_answer_is_refused() -> None:
    with pytest.raises(BlockedTarget):
        await assert_url_allowed("https://nope.example/", resolver=resolver_returning())


async def test_url_without_a_host_is_refused() -> None:
    with pytest.raises(BlockedTarget):
        await assert_url_allowed("https:///nohost", resolver=resolver_returning("1.1.1.1"))


# ------------------------------------------------------------------ redirects


async def test_redirect_into_the_network_is_refused() -> None:
    """A 200 from a public host can 302 to the metadata endpoint, which is why
    every hop is re-checked rather than only the first URL."""
    with pytest.raises(BlockedTarget) as exc:
        await assert_redirect_allowed(
            "http://169.254.169.254/latest/meta-data/", resolver=resolver_returning()
        )
    assert exc.value.reason == "cloud metadata endpoint"


async def test_plaintext_final_response_is_refused() -> None:
    with pytest.raises(BlockedTarget) as exc:
        await assert_redirect_allowed(
            "http://example.org/",
            require_https_final=True,
            is_final=True,
            resolver=resolver_returning("93.184.216.34"),
        )
    assert exc.value.reason == "plaintext final response"


async def test_plaintext_intermediate_hop_is_allowed() -> None:
    """An http:// link may be followed — most sites simply redirect. Only the
    end of the chain has to be encrypted."""
    addrs = await assert_redirect_allowed(
        "http://example.org/",
        require_https_final=True,
        is_final=False,
        resolver=resolver_returning("93.184.216.34"),
    )
    assert addrs


async def test_https_final_response_passes() -> None:
    addrs = await assert_redirect_allowed(
        "https://example.org/",
        require_https_final=True,
        is_final=True,
        resolver=resolver_returning("93.184.216.34"),
    )
    assert addrs
