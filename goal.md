# CodingFactory Lite — Personal Idea-to-Software Pipeline

A self-hosted tool: submit a software idea in plain English, an AI Product Manager and
AI Architect refine it with you, you approve the specification, and an autonomous
pipeline builds the software using OpenCode — then runs tests, reviews, and delivers
a working project.

This is the deliberately lean version of `goal-full.md`. Scope is one supervised
operator on one machine. The enterprise layers (multi-worker queues, Docker
execution service, metrics, auth) are out of scope by design, not by oversight.

## Principles

1. **One LLM.** Every agent uses the same configurable OpenAI-compatible endpoint
   (Strata2). All requests pass through a shared gateway: concurrency-limited
   (default 1), timeouts, bounded retries, validated structured outputs.
2. **Roles, not one prompt.** Product Manager, Architect, Dev Manager, Reviewer —
   each with its own system prompt and output schema.
3. **Approval gate.** Nothing is built until the human approves the artifacts.
4. **Persistent and resumable.** Every agent turn, artifact edit, task, and run is
   committed as it happens. Restarting mid-build resumes at the current task.
5. **Bounded everything.** Discussion rounds, task retries, fix loops, and run
   timeouts all have hard caps. No infinite agent conversations.
6. **Observable.** Live event stream (WebSocket) into the web UI.
7. **Verified, not assumed.** OpenCode adapter built against the installed CLI
   (v2.3.2: `opencode run --format json --model provider/model --auto`, cwd-scoped).
   Tests never require the real LLM; a mock LLM drives all automated tests.

## Stack

- Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite (WAL)
- Frontend: React + TypeScript + Vite + Tailwind
- Execution: OpenCode CLI (`opencode run`) inside the backend container
- Deployment: Docker Compose — 2 services (`backend`, `web`), volumes for `data/`
  and `workspaces/`, `extra_hosts` for `lxc-strata2`
- Tests: pytest (mock LLM), Vitest for frontend utilities

## Workflow

1. **IDEA** — user submits an idea.
2. **DISCOVERY** — PM analyzes (scope, assumptions, stories, open questions);
   Architect critiques (feasibility, risks, stack, missing constraints). Rounds
   are capped (`MAX_DISCOVERY_ROUNDS`, default 2).
3. **AWAITING_INPUT** — targeted questions presented in UI; user answers inline.
4. Artifacts generated: `product-requirements.md`, `architecture.md`,
   `user-stories.json`, `acceptance-criteria.json`, `open-questions.md`.
   Versioned and user-editable.
5. **AWAITING_APPROVAL** — user approves, rejects (back to DISCOVERY with note),
   or edits artifacts directly.
6. **PLANNING** — Dev Manager decomposes approved artifacts into ≤12 small tasks
   with dependencies.
7. **BUILDING** — sequential task execution in `workspaces/<project-slug>/`
   (git-init'd). Per task: OpenCode run → project test command → Reviewer
   verdict → bounded fix loop (default 3). Failures stay inspectable and
   resumable.
8. **COMPLETED** — final report artifact: what was built, test results,
   how to run it.

Project states: `IDEA, DISCOVERY, AWAITING_INPUT, AWAITING_APPROVAL, PLANNING,
BUILDING, COMPLETED, FAILED, CANCELLED`.

## Data model

`projects`, `messages`, `artifacts`, `artifact_versions`, `approvals`, `tasks`,
`task_logs`, `events`. UUID PKs, UTC timestamps. No secrets in the DB; the API
key lives only in the environment.

## Security posture (lite)

- Generated code runs inside the `backend` container, scoped to
  `/workspaces/<project>` — it cannot touch the host filesystem.
- No secrets passed into OpenCode runs; `.env` holds only non-secret config for
  generated projects.
- Hard timeout per OpenCode run; command audit in `task_logs`.
- Known limitation (accepted): isolation is per-container, not per-build.
  Concurrent builds share one container.

## Out of scope (deliberate)

Multi-user auth, per-build Docker sandboxes, GitHub PR automation, Prometheus
metrics, Kanban UI, artifact diffing, multi-worker queues, Redis, Postgres.

## Definition of done

1. `docker compose up -d` brings up the app; UI at `http://localhost:8000`.
2. Submit an idea through the UI; PM/Architect refine against live Strata2.
3. Questions surface in the UI; answers feed the loop.
4. Artifacts are viewable, editable, versioned; approval required before build.
5. Approved project produces tasks; tasks execute via OpenCode in
   `workspaces/`; test results visible; fix loops bounded.
6. Process restart resumes without duplicating in-flight work.
7. `pytest` suite (mock LLM) passes, including an end-to-end pipeline test.
8. README documents setup and operations.
