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

.PHONY: dev-up dev-down up down logs migrate seed \
        snapshot-corpus restore-corpus backup test build-push

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

test:
	uv run pytest

build-push:
	./scripts/build_and_push.sh
