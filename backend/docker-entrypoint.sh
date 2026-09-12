#!/bin/sh
# Bring the schema up to date before serving.
#
# The migration runner is forward-only and every file is idempotent, so running
# this on every start is safe and means a fresh machine needs no manual step --
# which is exactly the step that gets skipped at 6am on a launch day.
#
# Only the API does this. The worker waits for it rather than racing it: two
# processes applying the same migration at once is a deadlock waiting to be
# discovered in production.
set -e

if [ "${AGRIN_RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] applying migrations"
  python -m app.db.migrate
fi

# The port is decided here, not in the Dockerfile's CMD, because a managed host
# assigns it at runtime: Render injects PORT and routes to nothing else, so an
# image that binds a hardcoded port fails its health check and restarts for
# ever. Defaulting to 8000 keeps the compose file, its healthcheck and the
# EXPOSE line all correct with PORT unset.
if [ "$1" = "uvicorn" ]; then
  exec "$@" --port "${PORT:-8000}"
fi

exec "$@"
