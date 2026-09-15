"""The embedding sidecar (task P2-17, spec §4, §12.5).

`python -m worker.embedserver` — one model, held resident, answering HTTP.

**It lives in the worker's image because that image already carries the model.**
`worker.embed` loads bge-m3 for the backfill; a separate service would put a
second 2.3GB download and a second resident copy on a machine that has one of
each. Same image, different command, the way `worker.embed` and `worker.novelty`
already are.

**It holds no credentials and needs no egress.** It takes text and returns
vectors; it has no database connection and nothing to reach on the internet
once the weights are in the image. On the compose topology that puts it on
`internal`, with no `env_file` — the same reasoning that keeps `crawl4ai` from
holding database passwords (`P1-22`).

**Loading is lazy and reported.** The first request after a cold start pays for
the model; `/health` says whether the weights are resident yet, so an
orchestrator can tell "starting" from "wedged" rather than inferring it from a
slow response.
"""

from __future__ import annotations

import argparse
import os
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

from meridian_core.logging import configure_logging, get_logger

from .embeddings import BGEEmbedder, EmbedderSettings, EmbeddingError

log = get_logger(__name__)

#: Matches `meridian_core.embedder.MAX_TEXTS`. A cap on both sides so the
#: refusal is the same whichever end is older after a deploy.
MAX_TEXTS = 256


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

    @app.get("/health")
    async def health() -> dict[str, object]:
        """Liveness, and whether the weights are resident.

        Two facts, because they mean different things to whoever is waiting: a
        process that is up with no model loaded is starting, and the first
        request will be slow; one that has been up for ten minutes and still
        reports `loaded: false` has never been asked for anything.
        """
        return {
            "status": "ok",
            "model": settings.model_name,
            "dimensions": settings.dimensions,
            "loaded": bool(getattr(model, "loaded", False)),
        }

    @app.post("/embed", response_model=EmbedResponse)
    async def embed(request: Annotated[EmbedRequest, Body()]) -> EmbedResponse:
        import asyncio

        try:
            # In a thread: the model is CPU-bound and synchronous, and running
            # it on the event loop would stall every other request behind it —
            # including `/health`, which is what a supervisor uses to decide
            # whether this process is alive.
            vectors = await asyncio.to_thread(model.embed, request.texts)
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
