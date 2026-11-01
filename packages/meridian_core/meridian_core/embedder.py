"""A remote embedder: the client for the sidecar (task P2-17, spec §4, §12.5).

A query vector must come from the model the corpus was embedded with, so the API asks
the sidecar rather than loading a model or accepting a caller's vector. No
`MERIDIAN_EMBEDDER_URL` is a supported state (lexical-only search, reported as
`degraded`). See docs/features/embedding.md#the-service.

Two APIs (`B-248`): Meridian's own sidecar (``/embed``, ``/rerank``), and an
OpenAI-compatible server such as llama.cpp (``/v1/embeddings``, ``/v1/rerank``). See
docs/features/embedding.md#a-llamacpp-server.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Sequence
from typing import Literal

import httpx

from .httpwait import WAIT_STATUSES, retry_after_s
from .lanhosts import LanTransport
from .logging import get_logger

log = get_logger(__name__)

#: How long to wait on a *query* — one short string, on the read path, with a
#: reader watching. Deliberately impatient: a search that hangs for a minute is
#: worse than one that reports a degraded arm.
DEFAULT_TIMEOUT_S = 30.0

#: How long one text may take, used to size a batch's timeout (`B-27`). Generous
#: on purpose; see docs/features/embedding.md#timeouts.
SECONDS_PER_TEXT = 2.0

#: How long to wait for the sidecar's machine to accept a connection (`B-223`). Short,
#: and apart from the read timeout: a machine that is switched off and not on this
#: subnet never refuses, so one timeout for both made a search wait all 30 s. See
#: docs/features/embedding.md#when-the-gpu-machine-is-off.
DEFAULT_CONNECT_TIMEOUT_S = 3.0
#: Texts per request to an OpenAI-compatible server (`B-269`): a reader's query waits behind
#: at most this many. 16 bge-m3 passages took about a second on the operator's card, where
#: a 256-passage request held a query past its 30 s timeout three times in four.
DEFAULT_REQUEST_TEXTS = 16

#: How long a sidecar that refused a connection is not asked again, by any client in
#: this process (`B-223`). The API opens a client per search, so this lives here.
DEFAULT_DOWN_FOR_S = 30.0

#: Until when each sidecar, by base URL, is taken to be down.
_down_until: dict[str, float] = {}


#: What each OpenAI-compatible server lists at ``/v1/models``, by URL, and when it was read.
_listed: dict[str, tuple[float, frozenset[str]]] = {}

#: Servers found to have no ``/tokenize`` (anything but llama.cpp), by URL.
_no_tokenizer: set[str] = set()

#: How long a server's model list is trusted before it is read again.
LISTED_FOR_S = 300.0


def forget_outages() -> None:
    """Clear every remembered outage; for tests, and for an operator's shell."""
    _down_until.clear()
    _listed.clear()
    _no_tokenizer.clear()


#: A query is one short string. This cap is about batch calls from a backfill,
#: and exists so a caller cannot ask one HTTP request to hold a corpus.
MAX_TEXTS = 256


class EmbeddingUnavailable(RuntimeError):
    """The embedder is configured and did not answer.

    Distinct from *absent* (never configured, lexical search): this is an outage.
    """


class EmbedderMismatch(EmbeddingUnavailable):
    """The sidecar answered, with a different model than this corpus was built on.

    A subclass of "unavailable": callers degrade or fall back the same way. See
    docs/features/embedding.md#design-choices.
    """


class RerankingOff(RuntimeError):
    """The sidecar answers, and has no reranker configured (`B-167`). Not an outage."""


#: The APIs a remote embedder can speak (`B-248`).
type EmbedderApi = Literal["meridian", "openai"]
EMBEDDER_APIS: tuple[EmbedderApi, ...] = ("meridian", "openai")

#: Tokens one text is cut to, special tokens included: the worker's own limit
#: (`worker.embeddings.DEFAULT_MAX_TOKENS`), so a vector made elsewhere matches the
#: stored ones. A llama.cpp server refuses a longer input rather than cutting it.
DEFAULT_MAX_TOKENS = 1024

#: Tokens of question and passage together for a rerank, as in `worker.rerank`.
DEFAULT_RERANK_MAX_TOKENS = 1024

#: Room left in a rerank pair for the separators a server adds around the two texts.
RERANK_PAIR_MARGIN = 8

#: How long a rerank may take. Generous: on an integrated GPU 30 passages took about
#: 5 s, on a CPU about 14 s (docs/features/search.md#reranking).
DEFAULT_RERANK_TIMEOUT_S = 60.0


def _said(exc: BaseException) -> str:
    """An exception as a log line can carry it: its type, and its message if it has one.

    httpx's timeouts have empty messages, so "{url}: {exc}" logged an outage as nothing
    at all (`B-269`).
    """
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class RemoteEmbedder:
    """Vectors from the embedding sidecar.

    Async, unlike `worker.embeddings.Embedder`, because the callers that need it
    are already inside an event loop and the sidecar is a network hop rather
    than CPU work to push into a thread.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.AsyncClient | None = None,
        expect_model: str | None = None,
        token: str | None = None,
        rerank_timeout_s: float = DEFAULT_RERANK_TIMEOUT_S,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        down_for_s: float = DEFAULT_DOWN_FOR_S,
        api: EmbedderApi = "meridian",
        rerank_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        rerank_max_tokens: int = DEFAULT_RERANK_MAX_TOKENS,
        served_model: str | None = None,
        rerank_model: str | None = None,
        request_texts: int = DEFAULT_REQUEST_TEXTS,
    ) -> None:
        if api not in EMBEDDER_APIS:
            raise ValueError(f"unknown embedder API {api!r}; expected one of {EMBEDDER_APIS}")
        self.base_url = base_url.rstrip("/")
        self.api: EmbedderApi = api
        #: Where reranking is asked: the embedder itself unless named apart, since a
        #: llama.cpp server serves one model (`B-248`).
        self.rerank_url = (rerank_url or base_url).rstrip("/")
        self._max_tokens = max_tokens
        self._rerank_max_tokens = rerank_max_tokens
        #: Sent as a bearer token when the sidecar asks for one (`P3-12`).
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout_s
        self._rerank_timeout = rerank_timeout_s
        self._connect_timeout = connect_timeout_s
        self._down_for = down_for_s
        self._client = client
        self._owns_client = client is None
        #: Which model this corpus was embedded with. When set, a sidecar naming a
        #: different one is refused rather than used.
        self.expect_model = expect_model
        #: The names an OpenAI-compatible server knows the two models by (`B-248`); a
        #: server can call bge-m3 something else. Sent with every request, since a
        #: server holding several models routes by it.
        self.served_model = served_model or expect_model
        self.rerank_model = rerank_model
        #: The most texts one request to an OpenAI-compatible server carries (`B-269`).
        self._request_texts = max(1, request_texts)

    @classmethod
    def from_env(cls) -> RemoteEmbedder | None:
        """Build from ``MERIDIAN_EMBEDDER_URL``, or return None.

        None rather than raising: a deployment without an embedder is degraded,
        not broken, and should still answer lexical searches.
        """
        url = os.environ.get("MERIDIAN_EMBEDDER_URL", "").strip()
        if not url:
            return None
        return cls(
            url,
            timeout_s=_float_env("MERIDIAN_EMBEDDER_TIMEOUT_S", DEFAULT_TIMEOUT_S),
            # The same variable the worker builds its own model from, so the two
            # sides of the boundary read one setting rather than two that can
            # disagree silently.
            expect_model=os.environ.get("MERIDIAN_EMBED_MODEL", "").strip() or None,
            token=os.environ.get("MERIDIAN_EMBEDDER_TOKEN", "").strip() or None,
            rerank_timeout_s=_float_env("MERIDIAN_RERANK_TIMEOUT_S", DEFAULT_RERANK_TIMEOUT_S),
            connect_timeout_s=_float_env(
                "MERIDIAN_EMBEDDER_CONNECT_TIMEOUT_S", DEFAULT_CONNECT_TIMEOUT_S
            ),
            down_for_s=_float_env("MERIDIAN_EMBEDDER_DOWN_FOR_S", DEFAULT_DOWN_FOR_S),
            api=_api_env(),
            rerank_url=os.environ.get("MERIDIAN_RERANKER_URL", "").strip() or None,
            # The worker's own setting, so both sides cut a text at the same place.
            max_tokens=int(_float_env("MERIDIAN_EMBED_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
            rerank_max_tokens=int(
                _float_env("MERIDIAN_RERANK_MAX_TOKENS", DEFAULT_RERANK_MAX_TOKENS)
            ),
            served_model=os.environ.get("MERIDIAN_EMBEDDER_MODEL", "").strip() or None,
            rerank_model=os.environ.get("MERIDIAN_RERANKER_MODEL", "").strip() or None,
            request_texts=int(_float_env("MERIDIAN_EMBED_REQUEST_TEXTS", DEFAULT_REQUEST_TEXTS)),
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # `.local` names reach the address the resolver found (`B-250`).
            self._client = httpx.AsyncClient(
                timeout=self._limits(self._timeout), transport=LanTransport()
            )
        return self._client

    def _limits(self, read_s: float) -> httpx.Timeout:
        """``read_s`` for the answer, and the short connect timeout for the machine."""
        return httpx.Timeout(read_s, connect=min(self._connect_timeout, read_s))

    def _refuse_if_down(self, url: str | None = None) -> None:
        """Fail at once while this sidecar is remembered as down (`B-223`)."""
        url = url or self.base_url
        until = _down_until.get(url)
        if until is None:
            return
        left = until - time.monotonic()
        if left <= 0:
            _down_until.pop(url, None)
            return
        raise EmbeddingUnavailable(
            f"{url}: did not accept a connection; not asked again for {left:.0f} s"
        )

    def _note(self, exc: httpx.HTTPError, url: str | None = None) -> None:
        """Remember the sidecar as down when its machine did not accept a connection.

        Only then: a slow answer or an error status is a sidecar that is up.
        """
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) and self._down_for > 0:
            _down_until[url or self.base_url] = time.monotonic() + self._down_for

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthy(self) -> bool:
        return await self.describe() is not None

    async def describe(self) -> dict | None:
        """What the sidecar says it is, or None when it cannot be reached.

        Returned rather than reduced to a boolean because "up" is not the
        question that matters: a sidecar running a different model is up, and
        using it is worse than having none.
        """
        if self.api == "openai":
            return await self._describe_openai()
        # Always asked, even while remembered as down: this is how it comes back.
        try:
            response = await self._http().get(f"{self.base_url}/health", timeout=self._limits(5.0))
            if response.status_code != 200:
                return None
            body = response.json()
        except httpx.HTTPError as exc:
            self._note(exc)
            return None
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        _down_until.pop(self.base_url, None)
        return body

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for ``texts``, in order.

        Raises :class:`EmbeddingUnavailable` rather than returning None or a short
        list, which a caller could misalign without noticing.
        """
        if not texts:
            return []
        if len(texts) > MAX_TEXTS:
            raise ValueError(f"{len(texts)} texts exceeds the {MAX_TEXTS} cap for one request")
        self._refuse_if_down()
        if self.api == "openai":
            return await self._embed_openai(texts)

        try:
            response = await self._http().post(
                f"{self.base_url}/embed",
                json={"texts": list(texts)},
                headers=self._headers,
                # Sized to the request: a batch is N times the work of one text.
                timeout=self._limits(self._batch_timeout(len(texts))),
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            self._note(exc)
            raise EmbeddingUnavailable(f"{self.base_url}: {_said(exc)}") from exc
        except ValueError as exc:
            raise EmbeddingUnavailable(f"{self.base_url}: unreadable response") from exc

        named = body.get("model")
        if self.expect_model and named and named != self.expect_model:
            # Every response, not once at startup: a sidecar can be restarted with a
            # different model under a running client.
            raise EmbedderMismatch(
                f"{self.base_url} is serving {named!r}; this corpus is embedded "
                f"with {self.expect_model!r}. Mixed vectors rank nonsense."
            )

        vectors = body.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            # The alignment check. Off-by-one here attaches each vector to the
            # wrong text, and every value involved is a perfectly valid float —
            # there is no later point at which this becomes visible.
            raise EmbeddingUnavailable(
                f"{self.base_url}: asked for {len(texts)} vectors, got "
                f"{len(vectors) if isinstance(vectors, list) else 'none'}"
            )
        return vectors

    def _batch_timeout(self, count: int) -> float:
        """The configured timeout, or what this many texts plausibly need.

        A floor rather than a replacement, so setting `MERIDIAN_EMBEDDER_TIMEOUT_S`
        higher for a slow box still raises it for every call.
        """
        return max(self._timeout, count * SECONDS_PER_TEXT)

    async def embed_one(self, text: str) -> list[float]:
        """One vector, for the query path."""
        return (await self.embed([text]))[0]

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """A relevance score per text, in order (`B-167`).

        Raises :class:`RerankingOff` when the sidecar has no reranker and
        :class:`EmbeddingUnavailable` when it failed; never a short list.
        """
        if not texts:
            return []
        if len(texts) > MAX_TEXTS:
            raise ValueError(f"{len(texts)} texts exceeds the {MAX_TEXTS} cap for one request")
        self._refuse_if_down(self.rerank_url)
        if self.api == "openai":
            return await self._rerank_openai(query, texts)
        try:
            response = await self._http().post(
                f"{self.rerank_url}/rerank",
                json={"query": query, "texts": list(texts)},
                headers=self._headers,
                timeout=self._limits(self._rerank_timeout),
            )
            if response.status_code == 404:
                raise RerankingOff(f"{self.rerank_url} has no reranker configured")
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            self._note(exc, self.rerank_url)
            raise EmbeddingUnavailable(f"{self.rerank_url}: {_said(exc)}") from exc
        except ValueError as exc:
            raise EmbeddingUnavailable(f"{self.rerank_url}: unreadable response") from exc
        scores = body.get("scores")
        if not isinstance(scores, list) or len(scores) != len(texts):
            # The same alignment check as `embed`: a misaligned score reorders silently.
            raise EmbeddingUnavailable(
                f"{self.rerank_url}: asked for {len(texts)} scores, got "
                f"{len(scores) if isinstance(scores, list) else 'none'}"
            )
        return [float(score) for score in scores]

    # -- an OpenAI-compatible server (`B-248`, ADR 0023) ----------------------------

    async def _call(
        self, method: str, url: str, path: str, *, json: object = None, read_s: float
    ) -> httpx.Response:
        """One request; an unreachable machine or a server still loading is remembered.

        A server that loads on request answers 503 or 429 with ``Retry-After``: it is
        away until then, not failed, and nothing in this process asks it again sooner.
        """
        try:
            response = await self._http().request(
                method,
                f"{url}{path}",
                json=json,
                headers=self._headers,
                timeout=self._limits(read_s),
            )
        except httpx.HTTPError as exc:
            self._note(exc, url)
            raise EmbeddingUnavailable(f"{url}: {_said(exc)}") from exc
        if response.status_code in WAIT_STATUSES:
            wait = retry_after_s(response.headers, self._down_for)
            if wait > 0:
                _down_until[url] = time.monotonic() + wait
            raise EmbeddingUnavailable(
                f"{url}: busy or loading ({response.status_code}); not asked again for {wait:.0f} s"
            )
        return response

    @staticmethod
    def _body(url: str, response: httpx.Response) -> dict:
        """The JSON object a successful response holds, or an outage."""
        try:
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"{url}: {_said(exc)}") from exc
        except ValueError as exc:
            raise EmbeddingUnavailable(f"{url}: unreadable response") from exc
        if not isinstance(body, dict):
            raise EmbeddingUnavailable(f"{url}: unreadable response")
        return body

    @staticmethod
    def _named(payload: dict[str, object], model: str | None) -> dict[str, object]:
        return {**payload, "model": model} if model else payload

    async def _tokens(
        self, url: str, model: str | None, text: str, *, special: bool
    ) -> list[int] | None:
        """The server's own tokens for ``text``, or None when it has no ``/tokenize``.

        ``/tokenize`` is llama.cpp's; any other server truncates for itself.
        """
        if url in _no_tokenizer:
            return None
        response = await self._call(
            "POST",
            url,
            "/tokenize",
            json=self._named({"content": text, "add_special": special}, model),
            read_s=self._timeout,
        )
        if response.status_code in (404, 405, 501):
            _no_tokenizer.add(url)
            return None
        tokens = self._body(url, response).get("tokens")
        if not isinstance(tokens, list) or not all(isinstance(t, int) for t in tokens):
            raise EmbeddingUnavailable(f"{url}: /tokenize returned no tokens")
        return tokens

    async def _embedding_input(self, text: str) -> str | list[int]:
        """``text``, or its first tokens when it may be longer than the model is fed.

        Cut as sentence-transformers cuts: the leading tokens and the closing special
        token, so the vector matches one the worker made. A text no longer than the limit
        in characters cannot exceed it in tokens, and is sent as it is.
        """
        if len(text) + 3 <= self._max_tokens:
            return text
        tokens = await self._tokens(self.base_url, self.served_model, text, special=True)
        if tokens is None or len(tokens) <= self._max_tokens:
            return text
        return tokens[: self._max_tokens - 1] + tokens[-1:]

    async def _listed_models(self, url: str) -> frozenset[str] | None:
        """The model names ``url`` lists, read at most every :data:`LISTED_FOR_S`."""
        cached = _listed.get(url)
        if cached and time.monotonic() - cached[0] < LISTED_FOR_S:
            return cached[1]
        response = await self._call("GET", url, "/v1/models", read_s=5.0)
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        rows = body.get("data") if isinstance(body, dict) else None
        names = frozenset(
            row["id"]
            for row in rows or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        )
        _listed[url] = (time.monotonic(), names)
        return names

    async def _check_listed(self) -> None:
        """Refuse a server that does not list the model this corpus is embedded with.

        Read from the server's list, not from a response: llama.cpp names whatever model
        the request named, so a response passes against any server.
        """
        if not self.served_model:
            return
        names = await self._listed_models(self.base_url)
        if names and self.served_model not in names:
            raise EmbedderMismatch(
                f"{self.base_url} lists {sorted(names)!r}, not {self.served_model!r}; this "
                f"corpus is embedded with {self.expect_model or self.served_model!r}. Mixed "
                "vectors rank nonsense. Name the model on the server (llama.cpp: --alias), "
                "or set MERIDIAN_EMBEDDER_MODEL to the server's name for it."
            )

    async def _embed_openai(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for ``texts``, asked in slices of `request_texts` (`B-269`).

        A model server answers one request at a time, so a backfill of 256 passages sent
        whole made a reader's one-text query wait behind all of it, past its timeout, and
        search fell back to words while new pages were embedded. In slices the query waits
        for one slice; the throughput is the same.
        """
        await self._check_listed()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._request_texts):
            vectors += await self._embed_slice(texts[start : start + self._request_texts])
        return vectors

    async def _embed_slice(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = [await self._embedding_input(text) for text in texts]
        response = await self._call(
            "POST",
            self.base_url,
            "/v1/embeddings",
            json=self._named({"input": inputs}, self.served_model),
            read_s=self._batch_timeout(len(texts)),
        )
        body = self._body(self.base_url, response)
        data = body.get("data")
        rows = data if isinstance(data, list) else []
        by_index = {row.get("index"): row.get("embedding") for row in rows if isinstance(row, dict)}
        if len(rows) != len(texts) or set(by_index) != set(range(len(texts))):
            # The alignment check, by index: a server may answer in any order.
            raise EmbeddingUnavailable(
                f"{self.base_url}: asked for {len(texts)} vectors, got {len(rows) or 'none'}"
            )
        return [_unit(by_index[i], self.base_url) for i in range(len(texts))]

    async def _rerank_document(self, query: str, text: str) -> str:
        """``text`` cut so that the pair fits the reranker's limit; most already do.

        Without ``/tokenize`` the text goes whole, and the server truncates.
        """
        if len(query) + len(text) + RERANK_PAIR_MARGIN <= self._rerank_max_tokens:
            return text
        url, model = self.rerank_url, self.rerank_model
        question = await self._tokens(url, model, query, special=False)
        tokens = await self._tokens(url, model, text, special=False)
        if question is None or tokens is None:
            return text
        budget = self._rerank_max_tokens - len(question) - RERANK_PAIR_MARGIN
        if len(tokens) <= budget:
            return text
        if budget <= 0:
            raise ValueError("the question alone is longer than the reranker reads")
        response = await self._call(
            "POST",
            url,
            "/detokenize",
            json=self._named({"tokens": tokens[:budget]}, model),
            read_s=self._timeout,
        )
        content = self._body(url, response).get("content")
        if not isinstance(content, str):
            raise EmbeddingUnavailable(f"{url}: /detokenize returned no text")
        return content

    async def _rerank_openai(self, query: str, texts: Sequence[str]) -> list[float]:
        documents = [await self._rerank_document(query, text) for text in texts]
        response = await self._call(
            "POST",
            self.rerank_url,
            "/v1/rerank",
            json=self._named(
                {"query": query, "documents": documents, "top_n": len(documents)},
                self.rerank_model,
            ),
            read_s=self._rerank_timeout,
        )
        if response.status_code in (404, 501):
            # llama.cpp answers 501 when the server was started without `--reranking`.
            raise RerankingOff(f"{self.rerank_url} serves no reranker")
        body = self._body(self.rerank_url, response)
        results = body.get("results")
        rows = results if isinstance(results, list) else []
        scores: dict[int, float] = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("index"), int):
                score = row.get("relevance_score")
                if isinstance(score, (int, float)):
                    scores[row["index"]] = float(score)
        if len(rows) != len(texts) or set(scores) != set(range(len(texts))):
            # Results come sorted by score; each must name its text exactly once.
            raise EmbeddingUnavailable(
                f"{self.rerank_url}: asked for {len(texts)} scores, got {len(rows) or 'none'}"
            )
        return [scores[i] for i in range(len(texts))]

    async def _model_entry(self, url: str, wanted: str | None) -> dict | None | Literal[False]:
        """``wanted``'s row in ``url``'s ``/v1/models`` (else the first), or False when down.

        Always asked, even while remembered as down: this is how a server comes back.
        """
        try:
            response = await self._http().get(
                f"{url}/v1/models", headers=self._headers, timeout=self._limits(5.0)
            )
            if response.status_code != 200:
                return False
            body = response.json()
        except httpx.HTTPError as exc:
            self._note(exc, url)
            return False
        except ValueError:
            return False
        rows = body.get("data") if isinstance(body, dict) else None
        rows = [
            row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        _listed[url] = (time.monotonic(), frozenset(row["id"] for row in rows))
        if _down_until.get(url, 0) <= time.monotonic():
            _down_until.pop(url, None)
        for row in rows:
            if row["id"] == wanted:
                return row
        return rows[0] if rows and not wanted else None

    async def _describe_openai(self) -> dict | None:
        """The sidecar's ``/health`` shape, built from ``/v1/models``.

        Up means the server answers. A server that loads on request lists a model it has
        not loaded (llama.cpp's router marks it ``"status": {"value": "unloaded"}``);
        ``loaded`` says so, and the first request loads it.
        """
        entry = await self._model_entry(self.base_url, self.served_model)
        if entry is False:
            return None
        rerank = None
        if self.rerank_url != self.base_url or self.rerank_model:
            found = await self._model_entry(self.rerank_url, self.rerank_model)
            rerank = found or None
        return {
            "status": "ok",
            "model": entry["id"] if entry else None,
            "loaded": _loaded(entry),
            "rerank_model": rerank["id"] if rerank else None,
            "rerank_loaded": _loaded(rerank),
        }


def _unit(vector: object, url: str) -> list[float]:
    """``vector`` scaled to length one, as the worker stores them; refused if not a vector."""
    if not isinstance(vector, list) or not vector:
        raise EmbeddingUnavailable(f"{url}: a response row holds no vector")
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if not norm or not math.isfinite(norm):
        raise EmbeddingUnavailable(f"{url}: a zero or non-finite vector")
    return [v / norm for v in values]


def _loaded(entry: dict | None) -> bool:
    """Whether a ``/v1/models`` row is loaded: True unless its status says otherwise."""
    if not entry:
        return False
    status = entry.get("status")
    value = status.get("value") if isinstance(status, dict) else status
    return value is None or value == "loaded"


def _api_env() -> EmbedderApi:
    raw = os.environ.get("MERIDIAN_EMBEDDER_API", "").strip().lower() or "meridian"
    for api in EMBEDDER_APIS:
        if raw == api:
            return api
    # Refused, not defaulted: the wrong API reads every answer as an outage.
    raise ValueError(f"MERIDIAN_EMBEDDER_API={raw!r}; expected one of {EMBEDDER_APIS}")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("ignoring unreadable value", extra={"var": name, "value": raw})
        return default
