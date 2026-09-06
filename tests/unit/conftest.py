"""Fixtures for the fetcher tests.

``httpx.MockTransport`` is a real ``httpx.AsyncClient`` with a fake socket, not
a mocked client — request construction, header handling, redirect semantics and
streaming all run the production code path. What it replaces is only the network,
which is exactly the part that cannot be asserted against in a unit test.

The resolver is stubbed for the same reason: these tests are about what the
fetcher *does with* an answer from DNS, and a test whose verdict depends on the
live DNS for some domain is a test that fails on a train.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

import pytest
from http_doubles import RecordingTransport, streamed

from meridian_core.netguard import IPAddress
from meridian_core.policy import ResolvedPolicy


@pytest.fixture
def policy() -> Callable[..., ResolvedPolicy]:
    """Build a ResolvedPolicy with the shipped defaults, overridden per test."""

    def make(**overrides: object) -> ResolvedPolicy:
        return ResolvedPolicy(domain=overrides.pop("domain", "example.test"), **overrides)

    return make


@pytest.fixture
def resolver() -> Callable[..., object]:
    """A stub resolver over a host → addresses mapping.

    ``sequence`` gives a *different* answer per call for one host, which is how
    DNS rebinding is expressed: the check sees one address and the connection
    would see another.
    """

    def make(
        mapping: dict[str, list[str]] | None = None,
        *,
        sequence: dict[str, list[list[str]]] | None = None,
        fail: set[str] | None = None,
    ):
        calls: dict[str, int] = {}

        async def resolve(host: str, port: int = 443) -> list[IPAddress]:
            calls[host] = calls.get(host, 0) + 1
            if fail and host in fail:
                raise OSError(f"Name or service not known: {host}")
            if sequence and host in sequence:
                answers = sequence[host]
                index = min(calls[host] - 1, len(answers) - 1)
                return [ipaddress.ip_address(a) for a in answers[index]]
            if mapping and host in mapping:
                return [ipaddress.ip_address(a) for a in mapping[host]]
            raise OSError(f"Name or service not known: {host}")

        resolve.calls = calls  # type: ignore[attr-defined]
        return resolve

    return make


@pytest.fixture
def recorder() -> type[RecordingTransport]:
    return RecordingTransport


@pytest.fixture
def stream_response():
    return streamed
