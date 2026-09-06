"""Test doubles for the HTTP layer.

``httpx.MockTransport`` is a real ``httpx.AsyncClient`` with a fake socket, not
a mocked client — request construction, header handling, redirect semantics and
streaming all run the production code path. What it replaces is only the
network, which is exactly the part a unit test cannot assert against.

Importable rather than living in a conftest so the fetcher tests can use the
classes directly; ``pythonpath = ["tests"]`` in the pytest config puts it on the
path.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx


class ChunkStream(httpx.AsyncByteStream):
    """An async byte stream that yields exactly the chunks it was given.

    ``httpx.Response(content=...)`` produces a response that is already fully
    read, which the fetcher would refuse to stream. Since the fetcher's caps are
    enforced *between* chunks, a test that cannot control chunk boundaries
    cannot test them — so responses in these tests are built as real streams.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def streamed(
    status: int = 200,
    *,
    headers: dict[str, str] | None = None,
    chunks: list[bytes] | None = None,
) -> httpx.Response:
    """A streaming httpx.Response, the shape a real socket would produce."""
    return httpx.Response(status, headers=headers or {}, stream=ChunkStream(chunks or []))


class RecordingTransport:
    """A MockTransport that keeps every request it was handed.

    The recorded requests are the assertion surface for pinning: what host the
    socket would have been opened to, what ``Host`` header went out, and what
    hostname TLS would have been verified against are all visible there and
    nowhere else.
    """

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, follow_redirects=False)

    @property
    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]

    @property
    def host_headers(self) -> list[str]:
        return [r.headers.get("host", "") for r in self.requests]

    @property
    def sni_hostnames(self) -> list[str | None]:
        return [r.extensions.get("sni_hostname") for r in self.requests]
