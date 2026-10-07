"""Put the weights where the sidecar can find them (task `B-14`).

The sidecar has no route out and the weights are not in the image, so this one-shot
container on `egress` downloads them into the volume the sidecar mounts, then loads the
model and embeds one string to prove the set is complete. Idempotent. See
docs/features/embedding.md#fetching-the-weights.

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

    The model is *loaded* and asked to embed one short string, so a half-downloaded
    cache fails here rather than at the first real batch.
    """
    from .embeddings import BGEEmbedder

    settings = settings or EmbedderSettings.from_env()
    log.info(
        "fetching embedding weights",
        extra={
            "model": settings.model_name,
            # Absent means the library's default cache, a container layer nothing
            # mounts: a silent re-download on every recreate.
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
