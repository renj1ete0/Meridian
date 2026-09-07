# The ingestion worker (task P1-30, scaffold §5, spec §13.4).
#
# Built from the repository root, not from this directory: the worker depends on
# `meridian_core` through a uv workspace, and a build context narrowed to
# `services/worker` cannot see it. Hence `-f services/worker/Dockerfile .`.
#
# Multi-arch by construction (scaffold §5): no `platform:` key anywhere, a base
# image that publishes a manifest list, and nothing architecture-specific in the
# build. Production is arm64 on the Pi; development is x86_64. Build on x86,
# verify on arm64, never develop under emulation.

# --- builder ---------------------------------------------------------------
# 3.12 is the floor every pyproject declares, and pinning the image to the floor
# means a wheel that exists only for a newer interpreter fails here rather than
# on the Pi at 3am.
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies before source. They change on the order of once a release and the
# source changes every commit, so splitting the two is the difference between a
# rebuild that takes seconds and one that resolves the whole tree again.
COPY pyproject.toml uv.lock ./
COPY packages/meridian_core/pyproject.toml packages/meridian_core/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --package meridian-worker

COPY packages/meridian_core/ packages/meridian_core/
COPY services/worker/ services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package meridian-worker

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# poppler-utils is `pdftotext` and `pdfinfo`, which are the PDF extractor
# (`P1-09`). Not optional: `extract_pdf` raises `PdftotextMissing` rather than
# degrading, deliberately, because a worker that had quietly lost poppler would
# store every PDF and extract none of them with no other symptom. An image
# without this is an image where every PDF in the corpus fails loudly on the
# first crawl — which is the intended failure, and still not one to ship.
#
# ca-certificates because every fetch is HTTPS and the slim image ships none.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        poppler-utils \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged, and created here rather than relying on a stray uid: the compose
# file runs this `read_only` with `cap_drop: [ALL]`, and the raw store is a bind
# mount whose ownership has to match something.
RUN groupadd --system --gid 1001 meridian \
    && useradd --system --uid 1001 --gid meridian --create-home meridian

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # The filesystem is read-only at runtime; bytecode was compiled in the
    # builder, so an attempt to write more of it is only a warning to nobody.
    PYTHONDONTWRITEBYTECODE=1 \
    # Where the raw store lives inside the container (§5.4). The compose file
    # bind-mounts $DATA_ROOT/raw here.
    MERIDIAN_RAW_ROOT=/data/raw \
    # The filesystem is read-only apart from the tmpfs, and libraries that want
    # a dotfile in $HOME are not always polite about failing: onnxruntime (via
    # markitdown's file sniffing) prints a plain-text warning to stderr when it
    # cannot persist a telemetry id, which lands in the middle of a JSON log
    # stream a health check greps by severity. Pointing HOME at the tmpfs costs
    # nothing and keeps the stream parseable.
    HOME=/tmp

WORKDIR /app
COPY --from=builder --chown=meridian:meridian /app/.venv /app/.venv
COPY --from=builder --chown=meridian:meridian /app/packages /app/packages
COPY --from=builder --chown=meridian:meridian /app/services /app/services
# Read at first boot by `scripts/seed.py`, and by `policy.file_defaults()` as the
# floor under every fetch policy (§13.1).
COPY --chown=meridian:meridian config/ /app/config/

USER meridian

# Fails while poppler is missing or the package tree is broken — the two things
# that would otherwise surface as a corpus that quietly stopped growing.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import worker.main, worker.extract.pdf as p; raise SystemExit(0 if p.available() else 1)"]

# No `--max-tasks`: this runs until signalled. `SIGTERM` from `docker stop`
# finishes the fetches in flight and hands the leases back (`P1-15`).
STOPSIGNAL SIGTERM
CMD ["python", "-m", "worker.main"]
