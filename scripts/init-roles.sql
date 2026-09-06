-- Meridian database roles. Run automatically at Postgres first boot via
-- docker-entrypoint-initdb.d. See docs/spec/meridian-project-scaffold.md §4.
--
-- worker, orchestrator          -> meridian_rw
-- api explore routes,
--   run_readonly_query          -> meridian_ro
-- api admin routes              -> meridian_rw
--
-- Enforcing read-only at the database, not in application code, is what makes
-- the read-only escape hatch (spec §12.4) safe.

CREATE ROLE meridian_rw LOGIN PASSWORD :'rw_password';
CREATE ROLE meridian_ro LOGIN PASSWORD :'ro_password';

GRANT ALL ON ALL TABLES IN SCHEMA public TO meridian_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO meridian_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO meridian_ro;
