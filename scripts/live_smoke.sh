#!/bin/bash
# Live smoke: real Strata2 LLM through the full discovery workflow.
set -u
cd "$(dirname "$0")/.."
PORT=8777
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port $PORT >/tmp/cf-uvicorn.log 2>&1 &
UV_PID=$!
trap 'kill $UV_PID 2>/dev/null' EXIT
sleep 3

echo "== health =="
curl -s http://localhost:$PORT/healthz; echo
echo "== LLM connectivity =="
curl -s http://localhost:$PORT/api/health/llm; echo

echo "== create project =="
PID=$(curl -s -X POST http://localhost:$PORT/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"idea":"Build a vehicle maintenance application that tracks maintenance schedules for cars, motorcycles, ATVs, and lawn equipment. Single user, local app, reminders when service is due.","name":"vehicle-maintenance-smoke"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "project=$PID"

state=""
for i in $(seq 1 180); do
  state=$(curl -s http://localhost:$PORT/api/projects/$PID | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["state"])')
  echo "[$i] state=$state"
  [ "$state" = "AWAITING_INPUT" ] && break
  [ "$state" = "FAILED" ] && { echo "ERROR:"; curl -s http://localhost:$PORT/api/projects/$PID | python3 -m json.tool; exit 1; }
  sleep 5
done

echo "== messages so far =="
curl -s http://localhost:$PORT/api/projects/$PID/messages | backend/.venv/bin/python - <<'EOF'
import json, sys
for m in json.load(sys.stdin):
    print("--- [%s/%s] ---" % (m["agent"], m["kind"]))
    print(m["content"][:600]); print()
EOF

if [ "$state" = "AWAITING_INPUT" ]; then
  echo "== answering question =="
  Q=$(curl -s http://localhost:$PORT/api/projects/$PID/messages | python3 -c '
import json,sys
qs=[m for m in json.load(sys.stdin) if m["kind"]=="question"]
print(qs[0]["content"] if qs else "")')
  curl -s -X POST http://localhost:$PORT/api/projects/$PID/answers \
    -H 'Content-Type: application/json' \
    -d "{\"answers\":[{\"question\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$Q"),\"answer\":\"No email reminders in MVP. Desktop notifications only. Single machine.\"}]}" >/dev/null

  for i in $(seq 1 240); do
    state=$(curl -s http://localhost:$PORT/api/projects/$PID | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
    echo "[$i] state=$state"
    [ "$state" = "AWAITING_APPROVAL" ] && break
    [ "$state" = "FAILED" ] && { echo "ERROR:"; curl -s http://localhost:$PORT/api/projects/$PID | python3 -m json.tool; exit 1; }
    sleep 5
  done

  echo "== artifacts =="
  curl -s http://localhost:$PORT/api/projects/$PID/artifacts | backend/.venv/bin/python - <<'EOF'
import json, sys
for a in json.load(sys.stdin):
    print("--- %s v%s ---" % (a["slug"], a["current_version"]))
    print(a["content"][:800]); print()
EOF
fi

[ "$state" = "AWAITING_APPROVAL" ] && echo "SMOKE_OK" || { echo "SMOKE_INCOMPLETE state=$state"; exit 1; }
