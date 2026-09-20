# Schema and configuration tooling, for a stack with no checkout behind it.
# Task B-05/B-06.
#
# **Why this image exists at all.** `alembic` is in the root project's `dev`
# dependency group, and every application image syncs with `--no-dev` — which is
# right: the API and the worker have no business carrying migration tooling.
#
# Neither stack can assume a host with `uv`. For `make quickstart` not needing
# one is the entire premise; on the server, docs/setup.md §2 never installs it,
# and the database URLs name `postgres`, which only resolves inside the compose
# network. So the tooling gets an image of its own rather than being smuggled
# into a service image that does not want it.
#
# Used by both stacks, and profile-gated in both (`B-17`). The thing worth
# preventing is production migrating *itself* on boot, with nobody watching, and
# the profile is what prevents it: this starts only when somebody names it. The
# alternative was worse — the deploy docs told operators to run `alembic` and
# `scripts/seed.py` in the `worker` image, which carries neither.

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The dependency layer first, as the service images do: it changes once a
# release and the source changes every commit.
COPY pyproject.toml uv.lock ./
COPY packages/meridian_core/pyproject.toml packages/meridian_core/
COPY services/worker/pyproject.toml services/worker/
COPY services/api/pyproject.toml services/api/

# `meridian-core` and its dependencies: that is what `migrations/env.py` and
# `scripts/seed.py` import, and narrowing to it keeps the worker's torch and
# browser toolchain out of an image whose whole job is two short commands.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package meridian-core

COPY packages/meridian_core/ packages/meridian_core/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package meridian-core

# The two the root project declares and `--package meridian-core` therefore does
# not install: `alembic` is in the root's `dev` group and `pyyaml` is a root
# dependency, and narrowing the sync to one workspace member drops both.
#
# Pinned to the versions in `uv.lock` by hand, which is the one unsatisfying
# part of this file. `uv sync` cannot express "this package plus the root's dev
# group", and an unpinned install here would let a tool image drift from the
# lockfile every other image is built against. `tests/unit/test_scripts.py`
# asserts these match `uv.lock`, so the copy cannot rot silently.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv alembic==1.19.2 pyyaml==6.0.3

# Everything the two jobs read. `alembic.ini` and `migrations/` for the schema;
# `scripts/` and `config/` for the seed, which reads `config/*.yaml` at first
# boot and never again (scaffold §1.6).
COPY alembic.ini ./
COPY migrations/ migrations/
COPY scripts/ scripts/
COPY config/ config/

# No CMD. This image is only ever run with an explicit command — `alembic
# upgrade head` or `python scripts/seed.py` — and a default would make it
# ambiguous which of the two a bare `run` performs.
