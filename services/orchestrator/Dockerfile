# The synthesis orchestrator (task `P4-17`, spec §2.1, §6.3, §11.7).
#
# **This image exists to be the one that may call a model.** `meridian-worker`
# says of itself "never calls an LLM", and §2.1 makes that an invariant rather
# than a habit: ingestion has to keep acquiring while every agent is down,
# which is only true if it cannot depend on one. The worker image is therefore
# built without `meridian-core[agent]` and physically lacks the SDK. This one
# asks for it.
#
# **The code is the same tree, and that is deliberate.** `worker/orchestrate.py`
# holds the cycle, and `services/orchestrator/` holds no Python at all. A second
# copy of the stages would be a second thing to keep in step with the write
# tools; what differs between the crawl and the synthesis run is the dependency
# set and the entry point, so that is all that differs here. When the module
# moves, this Dockerfile changes one line.
#
# Built from the repository root: `-f services/orchestrator/Dockerfile .`

# --- builder ---------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# `--extra agent` and *not* `--extra embed`. The orchestrator reads chunks and
# writes edges; it never embeds, so the 2.3 GB of model weights the worker
# image carries for the sidecar would be pure image size here.
COPY pyproject.toml uv.lock ./
COPY packages/meridian_core/pyproject.toml packages/meridian_core/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --package meridian-worker --extra agent

COPY packages/meridian_core/ packages/meridian_core/
COPY services/worker/ services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package meridian-worker --extra agent

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# ca-certificates because every provider call is HTTPS and the slim image ships
# none. No poppler: this image does not extract documents, and an extraction
# dependency here would be the beginning of the worker image by another name.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 meridian \
    && useradd --system --uid 1001 --gid meridian --create-home meridian

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The raw store is mounted read-only (§11.4): a run cites chunks and never
    # rewrites what they were drawn from.
    MERIDIAN_RAW_ROOT=/data/raw \
    HOME=/tmp

WORKDIR /app
COPY --from=builder --chown=meridian:meridian /app/.venv /app/.venv
COPY --from=builder --chown=meridian:meridian /app/packages /app/packages
COPY --from=builder --chown=meridian:meridian /app/services /app/services
COPY --chown=meridian:meridian config/ /app/config/

USER meridian

# The SDK is the thing this image exists for, so its absence is what the probe
# checks. A build that silently dropped the extra would otherwise look healthy
# and defer every run at `extract` with a provider error.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import anthropic, worker.orchestrate; raise SystemExit(0)"]

STOPSIGNAL SIGTERM
CMD ["python", "-m", "worker.orchestrate", "--daemon"]
