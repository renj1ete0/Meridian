# Shared: work out how to reach this deployment's Postgres.
#
# Sourced, not executed. `pg_dump` and `pg_restore` run *inside* the container
# rather than on the host, which is not a convenience — the client and server
# versions have to match, and a host with an older `pg_dump` than the server
# fails with "server version mismatch" after you have waited for the dump.
# The container always has the right one.

meridian_compose() {
  if [ -n "${MERIDIAN_COMPOSE:-}" ]; then
    echo "$MERIDIAN_COMPOSE"
    return
  fi
  # Dev first: a developer with both files present is almost always working
  # against the dev stack, and picking production by accident is the expensive
  # way round to be wrong.
  if docker compose -f docker-compose.dev.yml ps --status running postgres 2>/dev/null | grep -q postgres; then
    echo "docker compose -f docker-compose.dev.yml"
  else
    echo "docker compose"
  fi
}

meridian_psql() {
  # shellcheck disable=SC2086
  $COMPOSE exec -T postgres psql -qtAX --username "${PG_USER:-meridian}" --dbname meridian "$@"
}

meridian_require_postgres() {
  # Two failures that look identical from here and have nothing to do with each
  # other. "Postgres is not running" sends you to `make dev-up`; "the Docker
  # daemon is unreachable" sends you to DOCKER_HOST or the socket permissions,
  # and being told to check your compose invocation instead costs a real detour.
  #
  # It also matters for which compose file got picked: the dev/production probe
  # above asks Docker a question, so an unreachable daemon silently answers
  # "not dev" and the message would name the production file on a dev machine.
  if ! docker info >/dev/null 2>&1; then
    echo "meridian: the Docker daemon is not reachable." >&2
    echo "  Check it is running, and that DOCKER_HOST points at its socket" >&2
    echo "  (rootless installs commonly need DOCKER_HOST=unix:///var/run/docker.sock)." >&2
    exit 1
  fi
  if ! meridian_psql -c 'SELECT 1' >/dev/null 2>&1; then
    echo "meridian: Docker is up, but Postgres is not answering via '$COMPOSE'." >&2
    echo "  Start it with 'make dev-up', or set MERIDIAN_COMPOSE if this deployment" >&2
    echo "  uses a different compose invocation." >&2
    exit 1
  fi
}
