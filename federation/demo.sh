#!/usr/bin/env bash
# Two AgriN nodes, talking to each other.
#
# node-in (India) and node-br (Brazil) run as separate processes with separate
# databases and separate Ed25519 identities. node-br discovers node-in, pins its
# key, pulls its model cards and k-anonymised aggregates, and verifies the
# signature against the pinned key.
#
# Separate databases and separate keys are what make this a real federation
# rather than one node talking to itself. The compose file in demo.yml runs the
# same thing in containers; this script needs nothing but the existing venv.
set -euo pipefail
cd "$(dirname "$0")/../backend"

DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5433}
IN_PORT=${IN_PORT:-8099}
BR_PORT=${BR_PORT:-8100}

echo "==> creating the Brazil node's own database"
docker exec agrin-db-1 psql -U agrin -d postgres -c "CREATE DATABASE agrin_br" 2>/dev/null \
  || echo "    (agrin_br already exists)"

echo "==> migrating node-br"
AGRIN_ENV=dev NODE_ID=node-br NODE_COUNTRY=BR \
  DATABASE_URL="postgresql+asyncpg://agrin:agrin@${DB_HOST}:${DB_PORT}/agrin_br" \
  .venv/bin/python -m app.db.migrate

echo "==> starting node-br on :${BR_PORT}"
AGRIN_ENV=dev NODE_ID=node-br NODE_COUNTRY=BR \
  DATABASE_URL="postgresql+asyncpg://agrin:agrin@${DB_HOST}:${DB_PORT}/agrin_br" \
  nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${BR_PORT}" \
  --log-level warning > /tmp/agrin_node_br.log 2>&1 &

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:${BR_PORT}/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:${BR_PORT}/health" >/dev/null || {
  echo "node-br failed to start; see /tmp/agrin_node_br.log"; exit 1; }

echo "==> both nodes up"
echo "    node-in  http://127.0.0.1:${IN_PORT}"
echo "    node-br  http://127.0.0.1:${BR_PORT}"
