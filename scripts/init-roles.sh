#!/bin/bash
# Meridian database roles. Run once by the Postgres entrypoint at first boot.
# See docs/spec/meridian-project-scaffold.md §4.
#
#   worker, orchestrator, /api/admin/*  -> meridian_rw
#   /api/explore/*, run_readonly_query  -> meridian_ro
#   anything a guest can reach          -> meridian_guest  (P3-07)
#
# meridian_ro can SELECT every table, `agent_tokens` included, and that table
# holds the one secret in the schema. It is the right role for the operator's
# own read path and the wrong one to put behind a shared surface, so
# meridian_guest exists with SELECT on the corpus and the graph and nothing
# else. Its *grants* are a migration (tables must exist first); only the
# credential is here.
#
# Enforcing read-only at the database, not in application code, is what makes the
# read-only escape hatch (spec §12.4) safe.
#
# This is a shell script rather than plain SQL because the entrypoint runs .sql
# files through psql with no variable bindings, so `:'rw_password'` would be a
# syntax error and the container would die during init.

set -euo pipefail

: "${PG_RW_PASSWORD:?PG_RW_PASSWORD must be set for role creation}"
: "${PG_RO_PASSWORD:?PG_RO_PASSWORD must be set for role creation}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE ROLE meridian_rw LOGIN PASSWORD '${PG_RW_PASSWORD}';
	CREATE ROLE meridian_ro LOGIN PASSWORD '${PG_RO_PASSWORD}';

	GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO meridian_rw, meridian_ro;
	GRANT USAGE ON SCHEMA public TO meridian_rw, meridian_ro;
	GRANT CREATE ON SCHEMA public TO meridian_rw;

	-- Nothing exists yet at init time; these cover anything already present.
	GRANT ALL ON ALL TABLES IN SCHEMA public TO meridian_rw;
	GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO meridian_rw;
	GRANT SELECT ON ALL TABLES IN SCHEMA public TO meridian_ro;

	-- The rules that actually matter: applied to tables migrations create later.
	--
	-- Default privileges only cover objects created by the role that declared
	-- them, so they are declared for BOTH writers. Migrations are meant to run as
	-- $POSTGRES_USER, but if anything ever creates a table as meridian_rw, the
	-- read-only role must not silently lose SELECT on it — that failure mode is
	-- invisible until an Explore query 500s in production.
	ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
	  GRANT ALL ON TABLES TO meridian_rw;
	ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
	  GRANT ALL ON SEQUENCES TO meridian_rw;
	ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
	  GRANT SELECT ON TABLES TO meridian_ro;
	ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
	  GRANT SELECT ON SEQUENCES TO meridian_ro;

	ALTER DEFAULT PRIVILEGES FOR ROLE meridian_rw IN SCHEMA public
	  GRANT SELECT ON TABLES TO meridian_ro;
	ALTER DEFAULT PRIVILEGES FOR ROLE meridian_rw IN SCHEMA public
	  GRANT SELECT ON SEQUENCES TO meridian_ro;

	CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

# The guest role, only when a password is configured. Absent, the migration
# still creates it NOLOGIN and grants it the corpus — so the privileges are
# always right and the role simply cannot connect, which is the correct state
# for a deployment that is not sharing anything.
#
# Deliberately NOT given ALTER DEFAULT PRIVILEGES anywhere: a table added later
# must be granted explicitly. Forgetting means a guest cannot read something
# they should, which gets reported; the opposite failure does not.
if [ -n "${PG_GUEST_PASSWORD:-}" ]; then
	psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
		CREATE ROLE meridian_guest LOGIN PASSWORD '${PG_GUEST_PASSWORD}';
		GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO meridian_guest;
		GRANT USAGE ON SCHEMA public TO meridian_guest;
	EOSQL
	echo "meridian: role meridian_guest created (grants applied by migration)"
fi

echo "meridian: roles meridian_rw / meridian_ro created, pgvector enabled"
