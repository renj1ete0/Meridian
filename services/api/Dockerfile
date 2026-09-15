# The read surface (task P2-07, scaffold §5, spec §12.6).
#
# Built from the repository root, not from this directory: the API depends on
# `meridian_core` through a uv workspace, and a build context narrowed to
# `services/api` cannot see it. Hence `-f services/api/Dockerfile .`.
#
# Multi-arch by construction (scaffold §5): no `platform:` key, a base image
# that publishes a manifest list, nothing architecture-specific in the build.
#
# Markedly lighter than the worker image and deliberately so. No poppler, no
# browser, and above all no `sentence-transformers` — the API has no embedder
# (see api/search_service.py). An image that pulled model weights in would add
# gigabytes and tens of seconds of cold start to a service whose job is
# answering HTTP in milliseconds.

# --- builder ---------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies before source: they change once a release, the source changes
# every commit, and splitting them is the difference between a rebuild that
# takes seconds and one that resolves the whole tree again.
COPY pyproject.toml uv.lock ./
COPY packages/meridian_core/pyproject.toml packages/meridian_core/
COPY services/api/pyproject.toml services/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --package meridian-api

COPY packages/meridian_core/ packages/meridian_core/
COPY services/api/ services/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package meridian-api

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# ca-certificates only. The API makes no outbound requests today — compose puts
# it on `internal`, which has no route out at all — but the certificate store is
# a few hundred kilobytes and its absence surfaces as a TLS error nobody expects
# the first time anything here does reach outward.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 meridian \
    && useradd --system --uid 1001 --gid meridian --create-home meridian

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

WORKDIR /app
COPY --from=builder --chown=meridian:meridian /app/.venv /app/.venv
COPY --from=builder --chown=meridian:meridian /app/packages /app/packages
COPY --from=builder --chown=meridian:meridian /app/services /app/services

USER meridian

EXPOSE 8000

# Reports whether the read role can reach the database, not merely whether the
# process is alive. A container that is up and cannot read is the failure worth
# restarting *something* over, and an endpoint that only proved liveness would
# report it healthy for as long as it kept answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,json,sys; b=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)); sys.exit(0 if b.get('database') else 1)"]

STOPSIGNAL SIGTERM
# One worker. The Pi has four cores shared with the crawler and Postgres, and
# the read surface is one person plus their models — concurrency here would be
# spending the ingestion node's cores on requests that are not arriving.
# `--no-access-log`: uvicorn's access log is plain text, and this stream is
# one JSON object per line by design (meridian_core/logging.py). The app's
# own middleware logs each request structurally instead, with a run_id.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-access-log", \
     "--log-config", "/app/services/api/log_config.json"]
