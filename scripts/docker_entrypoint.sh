#!/bin/sh
# Start the API. The app plans and emits a build handoff prompt; no coding CLI
# is configured here because the build is delegated to an external agent.
set -eu
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir /app/backend
