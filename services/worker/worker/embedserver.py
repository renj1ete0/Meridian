"""The embedding sidecar (task P2-17, spec §4, §12.5).

`python -m worker.embedserver` — one model, held resident, answering HTTP.

Runs from the worker's image, which carries the model's libraries; the weights come from
a volume `fetchmodel` fills. It holds no credentials and needs no egress. Loading is lazy,
and `/health` reports whether the weights are resident. See
docs/features/embedding.md#the-service.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from meridian_core.logging import configure_logging, get_logger

from .embeddings import BGEEmbedder, EmbedderSettings, EmbeddingError

log = get_logger(__name__)

#: Matches `meridian_core.embedder.MAX_TEXTS`. A cap on both sides so the
#: refusal is the same whichever end is older after a deploy.
MAX_TEXTS = 256


#: Texts encoded per thread call (`B-82`). Between slices the server checks the
#: client is still there, so a restarted backfill does not leave a batch running for
#: nobody.
SLICE = 32


class Abandoned(Exception):
    """The client went away; the rest of the batch was not computed."""


async def embed_in_slices(
    embed: Callable[[Sequence[str]], list[list[float]]],
    texts: Sequence[str],
    gone: Callable[[], Awaitable[bool]],
    size: int = SLICE,
) -> list[list[float]]:
    """``embed(texts)`` a slice at a time, off the event loop, stopping if ``gone()``."""
    if size < 1:
        raise ValueError("a slice holds at least one text")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), size):
        if start and await gone():
            raise Abandoned(f"client left after {start} of {len(texts)} texts")
        vectors.extend(await asyncio.to_thread(embed, texts[start : start + size]))
    return vectors


class EmbedRequest(BaseModel):
    texts: Annotated[list[str], Field(min_length=1, max_length=MAX_TEXTS)]


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    dimensions: int
    model: str


def create_app(embedder: object | None = None) -> FastAPI:
    """The app. ``embedder`` is injectable so a test needs no 2.3GB download."""
    settings = EmbedderSettings.from_env()
    model = embedder or BGEEmbedder(settings)

    app = FastAPI(title="Meridian embedder", summary="Vectors for the corpus and its queries.")
    # A shared secret when the sidecar runs on another machine (`P3-12`); unset on
    # one machine. `/health` stays open so a supervisor needs no secret.
    token = os.environ.get("MERIDIAN_EMBEDDER_TOKEN", "").strip()

    @app.get("/health")
    async def health() -> dict[str, object]:
        """Liveness, and whether the weights are resident.

        Up with no model loaded means starting, or never asked for anything yet.
        """
        return {
            "status": "ok",
            "model": settings.model_name,
            "dimensions": settings.dimensions,
            "loaded": bool(getattr(model, "loaded", False)),
        }

    @app.post("/embed", response_model=EmbedResponse)
    async def embed(request: Annotated[EmbedRequest, Body()], raw: Request) -> EmbedResponse:
        if token and not hmac.compare_digest(
            raw.headers.get("authorization", ""), f"Bearer {token}"
        ):
            raise HTTPException(status_code=401, detail="A valid embedder token is required.")
        try:
            # In a thread, so a synchronous encode does not stall `/health`.
            vectors = await embed_in_slices(model.embed, request.texts, raw.is_disconnected)
        except Abandoned as exc:
            # Nobody is reading the answer; 499 is for the log, not the client.
            log.info("embedding abandoned", extra={"reason": str(exc)})
            raise HTTPException(status_code=499, detail="client closed request") from exc
        except EmbeddingError as exc:
            # 503, not 500. The model failing to load is a dependency problem
            # the caller should retry past, and `RemoteEmbedder` turns any
            # failure here into a degraded search rather than an error page.
            log.error("embedding failed", extra={"reason": str(exc)})
            raise HTTPException(
                status_code=503, detail="The embedding model is unavailable."
            ) from exc

        return EmbedResponse(
            vectors=vectors,
            dimensions=settings.dimensions,
            # Named in every response so a caller can tell it is talking to the
            # model its corpus was built with. Vectors from a different one
            # compare without erroring and rank nonsense confidently.
            model=settings.model_name,
        )

    return app


def main() -> None:
    """Entry point: ``python -m worker.embedserver``."""
    parser = argparse.ArgumentParser(description="Serve vectors from the corpus's own model.")
    parser.add_argument("--host", default=os.environ.get("MERIDIAN_EMBEDDER_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MERIDIAN_EMBEDDER_PORT", "8100"))
    )
    args = parser.parse_args()

    configure_logging("embedserver")
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
