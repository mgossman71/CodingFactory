#!/bin/bash
# Deploy CodingFactory with Docker: creates the external data volume if it
# doesn't exist yet (compose won't, by design - see docker-compose.yml),
# then builds and starts the stack. Safe to re-run on any host, any time.
set -euo pipefail
cd "$(dirname "$0")/.."

VOLUME=codingfactory_cf-data # must match `name:` under volumes: in docker-compose.yml

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and set LLM_API_KEY + STRATA2_IP." >&2
  exit 1
fi

# Idempotent: no-op if the volume already exists (protects existing DBs).
docker volume create "$VOLUME" >/dev/null

docker compose up -d --build
echo
docker compose ps
