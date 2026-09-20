"""Put the weights where the sidecar can find them (task `B-14`).

**The bug this exists for.** `embedder` sits on `internal`, which is
`internal: true` — no gateway, no DNS, no route out. The weights are not in the
image either, despite the compose comment that said they were: the worker image
installs `sentence-transformers` and never downloads `BAAI/bge-m3`. So on a
fresh stack the sidecar starts, answers `/health` with `loaded: false`, and
fails every embed request after a 30-second timeout — for ever, because the
download it is waiting on cannot happen from where it is standing.

Nothing reports this as an outage until something asks for a vector. `/health`
is truthful and reads as fine; the model is lazy, so an unloaded model is the
normal state of a sidecar nobody has used yet.

**Why a separate one-shot rather than giving the sidecar egress.** Its isolation
is the point — it takes text derived from pages the crawler fetched, runs it
through a model, and returns numbers. A route to the internet from there is a
route out for anything that ever gets in. So the download happens once, in a
container on `egress` that exits, writing into the volume the sidecar mounts.

**Why not bake them into the image.** 2.3GB on every layer push, twice over for
a multi-arch build (scaffold §5), for weights that do not change between
releases and that a volume already keeps across recreates.

Idempotent: `sentence-transformers` resolves from the cache when it is already
populated, so a second run downloads nothing. Safe to put in front of every
`make quickstart`, which is where it is.

    python -m worker.fetchmodel
"""

from __future__ import annotations

import argparse
import sys

from meridian_core.logging import configure_logging, get_logger

from .embeddings import EmbedderSettings, EmbeddingError

log = get_logger(__name__)


def fetch(settings: EmbedderSettings | None = None) -> int:
    """Download the weights into ``settings.cache_dir`` and report dimensions.

    The model is *loaded*, not merely fetched, and then asked to embed one short
    string. Downloading the files proves they arrived; encoding with them proves
    the set is complete and the runtime can use it — and a half-downloaded cache
    that fails at the first real batch is precisely the failure this step exists
    to move forward in time.
    """
    from .embeddings import BGEEmbedder

    settings = settings or EmbedderSettings.from_env()
    log.info(
        "fetching embedding weights",
        extra={
            "model": settings.model_name,
            # Absent means the library's default cache, which inside a
            # container is a layer nothing mounted — worth saying out loud,
            # because the symptom of getting it wrong is a re-download on every
            # recreate and no error at all.
            "cache_dir": settings.cache_dir or "<library default>",
        },
    )

    embedder = BGEEmbedder(settings)
    vectors = embedder.embed(["warm"])
    dimensions = len(vectors[0])

    if dimensions != settings.dimensions:
        # The schema's vector column is fixed width. A model that returns a
        # different one cannot be stored, and finding that out here beats
        # finding it out on the first batch of a four-hour backfill.
        raise EmbeddingError(
            f"{settings.model_name} returned {dimensions} dimensions, "
            f"but the schema expects {settings.dimensions}"
        )

    log.info(
        "embedding weights ready",
        extra={"model": settings.model_name, "dimensions": dimensions},
    )
    return dimensions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    configure_logging("fetchmodel")
    try:
        fetch()
    except EmbeddingError as exc:
        # A non-zero exit so `docker compose run` fails visibly. The caller is
        # `scripts/quickstart.sh`, which must not go on to start a sidecar that
        # has nothing to serve.
        log.error("could not fetch the embedding weights", extra={"reason": str(exc)})
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
