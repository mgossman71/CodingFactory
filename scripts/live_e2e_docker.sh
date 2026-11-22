#!/bin/bash
# Full end-to-end against the Docker deployment (:8000): real LLM discovery ->
# answer -> approve -> real OpenCode build inside the container -> COMPLETED.
set -u
cd "$(dirname "$0")/.."
PORT=8000
py() { backend/.venv/bin/python -c "$1"; }

echo "== create project =="
PID=$(curl -s -X POST http://localhost:$PORT/api/projects -H 'Content-Type: application/json' -d '{"idea":"A tiny URL shortener web service. FastAPI + SQLite. POST /shorten takes a long URL and returns a short code; GET /{code} redirects. Include pytest tests and a README with run instructions. No auth, no accounts, no docker — keep it as small as possible.","name":"urlshort-docker-e2e"}' | py 'import json,sys; print(json.load(sys.stdin)["id"])')
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

wait_state AWAITING_INPUT 60 || exit 1
backend/.venv/bin/python - "$PORT" "$PID" <<'EOF'
import json, sys, urllib.request
port, pid = sys.argv[1], sys.argv[2]
msgs = json.load(urllib.request.urlopen(f"http://localhost:{port}/api/projects/{pid}/messages"))
qs = [m for m in msgs if m["kind"] == "question" and not m["answered"]]
answers = [{"question_id": q["id"], "question": q["content"], "answer": "Whatever keeps scope minimal; a plain web app with no frontend polish beyond a simple HTML form is fine."} for q in qs]
body = json.dumps({"answers": answers}).encode()
req = urllib.request.Request(f"http://localhost:{port}/api/projects/{pid}/answers", data=body, headers={"Content-Type": "application/json"})
print("answered", len(answers), "questions:", urllib.request.urlopen(req).status)
EOF

wait_state AWAITING_APPROVAL 90 || exit 1
echo "== approving =="
curl -s -X POST http://localhost:$PORT/api/projects/$PID/approval -H 'Content-Type: application/json' -d '{"decision":"APPROVED"}' | py 'import json,sys; print(json.load(sys.stdin)["state"])'

wait_state COMPLETED 400 || { echo "=== tasks ==="; curl -s http://localhost:$PORT/api/projects/$PID/tasks | py 'import json,sys;[print(t["number"],t["status"],t["attempts"]) for t in json.load(sys.stdin)]'; exit 1; }

echo "=== tasks ==="
curl -s http://localhost:$PORT/api/projects/$PID/tasks | py 'import json,sys;[print("task",t["number"],t["status"],"attempts="+str(t["attempts"])) for t in json.load(sys.stdin)]'
echo "=== container-side workspace tests ==="
docker compose exec -T app sh -c 'cd /app/workspaces/urlshort-docker-e2e && ls && /opt/venv/bin/python -m pytest -q 2>&1 | tail -3'
echo "DOCKER_E2E_OK"
