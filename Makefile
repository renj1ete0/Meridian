# Meridian — common tasks. See docs/spec/meridian-project-scaffold.md §5.
#
# Targets that shell out to application code (migrate, seed, test, ...) are
# stubs until the corresponding phase is built (README.md build order). They
# are declared now so the interface is stable as pieces land.

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
migrate:
	uv run alembic upgrade head

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
