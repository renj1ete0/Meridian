# Meridian — common tasks. See docs/spec/meridian-project-scaffold.md §5.
#
# Local development reads .env.dev if present, so `make migrate` and `make seed`
# work with no manual exports. In production the environment comes from the
# container (env_file in docker-compose.yml) and this include is simply absent.
#
# Targets that shell out to application code (migrate, seed, test, ...) are
# stubs until the corresponding phase is built (README.md build order). They
# are declared now so the interface is stable as pieces land.

-include .env.dev
export

.PHONY: promote dev-up dev-down up down logs migrate seed quickstart preflight \
        local-up local-down local-logs \
        snapshot-corpus restore-corpus backup lint test coverage mutate stale-dist-info clock-check leak-check bench-search build-push build-worker

# --- Running it, rather than developing it (tasks B-05, B-06, B-08) ---------
#
# `quickstart` is the one command: check the machine, build from source, bring
# up the full stack, migrate, seed, and print the URL. Everything below it is
# the same stack with the steps separated, for when something has gone wrong.
quickstart:
	./scripts/quickstart.sh $(ARGS)

preflight:
	./scripts/preflight.sh

local-up:
	docker compose -f docker-compose.local.yml up -d --build

local-down:
	docker compose -f docker-compose.local.yml down

local-logs:
	docker compose -f docker-compose.local.yml logs -f

# Rebuild and restart named services only, leaving the rest running: `make rebuild svc="api web"`.
# `--no-deps` matters: `up --build <svc>` also recreated Postgres, mid-write (handover, 2026-10-08).
rebuild:
	@test -n "$(svc)" || { echo 'usage: make rebuild svc="api web"'; exit 2; }
	docker compose -f docker-compose.local.yml build $(svc)
	docker compose -f docker-compose.local.yml up -d --no-deps $(svc)

# --- Local development (infra only) -----------------------------------------
dev-up:
	docker compose -f docker-compose.dev.yml up -d

dev-down:
	docker compose -f docker-compose.dev.yml down

# --- Full stack (Pi) ---------------------------------------------------------
up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

# --- Database -----------------------------------------------------------------
# Both read credentials from .env.dev locally, or the environment in production.
# Migrations run as the owner (PG_MIGRATION_URL), never as meridian_rw — see
# migrations/env.py for why that distinction matters.
migrate:
	uv run alembic upgrade head

revision:
	@test -n "$(m)" || { echo 'usage: make revision m="what changed"'; exit 1; }
	uv run alembic revision --autogenerate -m "$(m)"

seed:
	uv run python scripts/seed.py

# --- Dev corpus (scaffold §6) -------------------------------------------------
snapshot-corpus:
	./scripts/snapshot_corpus.sh

restore-corpus:
	./scripts/restore_corpus.sh

# --- Ops -----------------------------------------------------------------------
backup:
	./scripts/backup.sh

# PEP 8 / PEP 257 via ruff, and the formatter, before the suite; the web package
# through oxlint and Prettier (B-142): AGENTS.md "Code and comment standards".
lint: stale-dist-info
	uv run ruff check .
	uv run ruff format --check .
	cd web && npm run --silent lint

test: lint
	uv run pytest

# Branch coverage over the whole suite (`Q-01`); see docs/guides/testing.md.
coverage:
	uv run pytest --cov --cov-report=term --cov-report=html:.coverage-html

# Mutation testing of the pure modules (`Q-02`), in a staging tree laid out as mutmut expects;
# slow. See docs/guides/testing.md.
mutate:
	scripts/mutate.sh

# A version bump leaves the old workspace dist-info without a RECORD, and uv then warns on every
# command and rebuilds; deleting them before running is what the handover used to say to do.
stale-dist-info:
	@for d in .venv/lib/python3*/site-packages/meridian_*.dist-info; do \
	  [ -d "$$d" ] && [ ! -f "$$d/RECORD" ] && rm -rf "$$d"; true; \
	done

# Every test again with all clocks DAYS ahead; prints what fails only then (`B-147`).
clock-check:
	./scripts/clockshift/run.sh $(or $(DAYS),400)

# The integration suite on a fresh database; prints any rows or settings it left (`B-148`).
leak-check:
	./scripts/clockshift/leakcheck.sh

# --- Measurement (P2-04) ------------------------------------------------------
# Index recall, latency and arm agreement. Reports whether the planner actually
# used the HNSW index, because below a few thousand vectors it will not — and a
# recall figure measured against a sequential scan is 1.0 by construction.
bench-search:
	uv run python scripts/benchmark_search.py $(ARGS)

build-push:
	./scripts/build_and_push.sh

# Point the `stable` channel at a pushed commit; servers running Watchtower
# follow it (`P3-12`). Rollback is promoting the previous SHA.
promote:
	@test -n "$(SHA)" || { echo 'usage: make promote SHA=<commit>'; exit 1; }
	./scripts/promote.sh $(SHA)

# Build the worker image locally, for the host's own architecture. The build
# context is the repo root because the services share a uv workspace; multi-arch
# release builds go through `build-push` (scaffold §5).
build-worker:
	docker build -f services/worker/Dockerfile -t meridian-worker:dev .
