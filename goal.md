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
7. **Verified, not assumed.** The tool stops at a build-ready prompt: the
   handoff document is assembled deterministically from the approved spec +
   task plan, never LLM-hallucinated. Tests never require the real LLM; a mock
   LLM drives all automated tests.

## Stack

- Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite (WAL)
- Frontend: React + TypeScript + Vite + Tailwind
- Delivery: a **build handoff prompt** (specs + ordered task plan + build/test
  brief) for an external coding agent to execute — no build execution here
- Deployment: Docker Compose — one container (backend + built UI), `data/`
  named volume, `extra_hosts` for `lxc-strata2`
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
   with dependencies; then the **build handoff prompt** (`handoff-prompt.md`)
   is assembled deterministically from the approved specs + the task plan.
7. **COMPLETED** — the handoff prompt is the deliverable: copy or download it and
   hand it to a coding agent to run the build + test phases. A change request
   from here starts a new delta cycle and regenerates the prompt.

Project states: `IDEA, DISCOVERY, AWAITING_INPUT, AWAITING_APPROVAL, PLANNING,
COMPLETED, FAILED, CANCELLED`.

## Data model

`projects`, `messages`, `artifacts`, `artifact_versions`, `approvals`, `tasks`,
`events`. UUID PKs, UTC timestamps. No secrets in the DB; the API key lives only
in the environment. (The build-phase tables — task attempts and execution logs —
were removed when execution was dropped.)

## Security posture (lite)

- The tool never executes generated code, so there is no untrusted-code sandbox
  concern; it produces a text artifact only.
- No secrets in the DB or in generated prompts beyond what the user provided.
- No auth: single trusted operator; do not expose port 8000 publicly.

## Out of scope (deliberate)

Multi-user auth, per-build Docker sandboxes, GitHub PR automation, Prometheus
metrics, Kanban UI, artifact diffing, multi-worker queues, Redis, Postgres.

## Definition of done

1. `docker compose up -d` brings up the app; UI at `http://localhost:8000`.
2. Submit an idea through the UI; PM/Architect refine against live Strata2.
3. Questions surface in the UI; answers feed the loop.
4. Artifacts are viewable, editable, versioned; approval required to finalize.
5. Approved project produces a task plan and a downloadable **build handoff
   prompt** (specs + ordered tasks + build/test brief); a change request
   regenerates a new prompt version.
6. Process restart resumes without duplicating in-flight work.
7. `pytest` suite (mock LLM) passes, including an end-to-end pipeline test.
8. README documents setup and operations.
