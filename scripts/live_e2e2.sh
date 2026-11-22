#!/bin/bash
# Phase 2 of live e2e: resume the discovery-failed urlshort project, approve, build for real.
set -u
cd "$(dirname "$0")/.."
export PATH="$PWD/backend/.venv/bin:$PATH"
PORT=8780
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port $PORT >/tmp/cf-e2e2-uvicorn.log 2>&1 &
UV_PID=$!
trap 'kill $UV_PID 2>/dev/null' EXIT
sleep 3
py() { backend/.venv/bin/python -c "$1"; }

PID=$(curl -s http://localhost:$PORT/api/projects | py 'import json,sys; print(next(p["id"] for p in json.load(sys.stdin) if p["name"]=="urlshort-e2e"))')
echo "project=$PID"

wait_state() {
  for i in $(seq 1 $2); do
    ST=$(curl -s http://localhost:$PORT/api/projects/$PID | py 'import json,sys; print(json.load(sys.stdin)["state"])')
    [ "$i" = "1" ] || [ $((i % 6)) = "0" ] && echo "[$i] state=$ST"
    [ "$ST" = "$1" ] && return 0
    [ "$ST" = "FAILED" ] && { curl -s http://localhost:$PORT/api/projects/$PID | py 'import json,sys; print("ERR:", json.load(sys.stdin)["error"])'; return 1; }
    sleep 10
  done
  echo "TIMEOUT waiting for $1 (last=$ST)"; return 1
}

curl -s -X POST http://localhost:$PORT/api/projects/$PID/resume >/dev/null
wait_state AWAITING_APPROVAL 60 || exit 1
echo "== approving =="
curl -s -X POST http://localhost:$PORT/api/projects/$PID/approval -H 'Content-Type: application/json' -d '{"decision":"APPROVED"}' | py 'import json,sys; print(json.load(sys.stdin)["state"])'

wait_state COMPLETED 400 || { echo "=== tasks ==="; curl -s http://localhost:$PORT/api/projects/$PID/tasks | py 'import json,sys;[print(t["number"],t["status"],t["attempts"]) for t in json.load(sys.stdin)]'; exit 1; }

echo "=== tasks ==="
curl -s http://localhost:$PORT/api/projects/$PID/tasks | py 'import json,sys;[print("task",t["number"],t["status"],"attempts="+str(t["attempts"])) for t in json.load(sys.stdin)]'
echo "=== workspace ==="
ls workspaces/urlshort-e2e && cd workspaces/urlshort-e2e && git log --oneline | head -5 && (python -m pytest -q 2>&1 | tail -2)
echo "E2E_OK"
