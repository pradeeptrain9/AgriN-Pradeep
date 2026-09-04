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

exec "$@"
