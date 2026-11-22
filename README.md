# CodingFactory Lite

Idea → AI refinement → your approval → autonomous build with OpenCode → tested
software. Self-hosted, single operator, one shared LLM.

Full design rationale in `goal.md`; the original maximal spec is `goal-full.md`.

## How it works

1. Submit an idea in the web UI.
2. A **Product Manager** agent analyzes it (confirmed requirements vs assumptions,
   MVP scope, user stories) and an **Architect** agent critiques feasibility.
   Questions that materially change the build are surfaced to you in the UI.
3. Artifacts are generated (`product-requirements.md`, `architecture.md`,
   `user-stories.json`, `acceptance-criteria.json`, `open-questions.md`),
   versioned, and editable.
4. **Nothing is built until you click Approve.** Reject / request-revisions
   re-enter discovery.
5. A **Dev Manager** decomposes the approved spec into ≤12 dependency-ordered
   tasks. Each runs sequentially through **OpenCode** in `workspaces/<project>/`,
   then the project's test command runs, then a **Reviewer** agent gates the
   result (bounded fix loops).
6. A delivery report is produced; the workspace holds the git history.

## Quick start (dev, macOS/Linux)

```bash
cp .env.example .env          # set LLM_API_KEY
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd ..
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000
# UI (dev, hot reload): cd frontend && npm install && npm run dev  -> :5173
# or serve the built UI from the backend: cd frontend && npm run build, then use :8000
```

Requires on PATH: `opencode` (v2.3.2 verified), `git`. For JS projects: `node`/`npm`.

## Docker deployment

```bash
cp .env.example .env          # fill LLM_API_KEY + STRATA2_IP (LLM host LAN IP)
./scripts/fetch_opencode.sh   # downloads pinned opencode v2.3.2 binary
docker compose up -d --build
open http://localhost:8000
```

One container: FastAPI + built React UI + OpenCode CLI, running non-root.
`./data` (SQLite) and `./workspaces` (generated code) are bind-mounted.

## Testing

```bash
cd backend && .venv/bin/python -m pytest        # 40 tests, no network needed
cd frontend && npm test                          # vitest
./scripts/live_smoke.sh                          # optional: real-LLM discovery smoke
```

Tests use a scripted MockLLM and MockExecutor; OpenCode itself is exercised
against a fake binary (NDJSON parsing, exit codes, timeouts).

## Ops notes

- **One pipeline per data directory.** Never run the host dev server and the
  Docker container against the same `./data` at the same time: two schedulers
  will race on the same projects and workspaces (observed: cross-killed task
  attempts). Stop one before starting the other.
- **Resume**: restart the process and mid-flight projects are re-dispatched from
  persisted state; the **Resume** button retries FAILED projects with fresh
  task attempts.
- **Limits** (env): `MAX_DISCOVERY_ROUNDS=2`, `MAX_TASKS=12`, `MAX_TASK_ATTEMPTS=3`,
  `OPENCODE_TIMEOUT=1800`, `LLM_MAX_CONCURRENT_REQUESTS=1`.
- **Logs/audit**: every attempt stores command/stdio/review records (secrets
  redacted) in `task_attempts`/`execution_logs`; the Activity tab shows the
  event stream.

## Known limitations (accepted by design)

- Generated code runs in the backend container — shared isolation, not per-build.
- No auth: single trusted operator; do not expose port 8000 publicly.
- Sequential builds only; with `LLM_MAX_CONCURRENT_REQUESTS=1` full runs take
  tens of minutes.
- GitHub PR delivery is not wired yet (workspace git history is the artifact).
