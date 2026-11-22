# Implementation Plan — CodingFactory Lite

Vertical slices. Each milestone ends with running, tested code.

## Milestone 1 — Backend foundation
- FastAPI app, config from env, SQLAlchemy + SQLite (WAL), all 8 tables.
- LLM gateway: httpx, semaphore(max_concurrent), timeouts, bounded retries,
  JSON-schema-validated outputs, structured errors. Mock LLM for tests.
- `/healthz`, `/api/projects` CRUD, event recording + WebSocket stream.
- **Tests:** gateway (mock transport), retry/validation, project API, DB models.

## Milestone 2 — Discovery workflow
- Role registry (PM, Architect) with prompts + Pydantic output schemas.
- Agent runtime: context assembly, single-shot structured call, one repair retry.
- Discovery service: analyze → critique → bounded rounds → user Q&A →
  artifact generation → AWAITING_APPROVAL.
- Artifact versioning + user edit; approval endpoints (approve/reject/revise).
- **Tests:** full discovery loop against mock LLM, round caps, approval gating,
  artifact versions.

## Milestone 3 — Build pipeline
- Dev Manager task decomposition (≤12 tasks, deps validated).
- Dependency-aware sequential scheduler with resume-on-restart.
- OpenCode adapter (verified v2.3.2 flags) + mock adapter.
- Per-task: run → test command detection/run → Reviewer verdict → fix loop (≤3).
- Final report artifact; COMPLETED state.
- **Tests:** scheduler ordering/failures, adapter (mock subprocess + real smoke),
  fix-loop bounds, e2e pipeline idea→delivery with mock LLM + mock executor.

## Milestone 4 — Web UI
- React app: dashboard, new project, project page (Discussion / Specs / Tasks /
  Activity tabs), live WS updates, artifact editor, approve/reject/revise,
  task logs.
- **Tests:** Vitest on state/utilities; manual walkthrough.

## Milestone 5 — Deployment & hardening
- Dockerfiles (backend with OpenCode + git + node + python; web nginx),
  docker-compose.yml with healthchecks, volumes, extra_hosts, .env.example.
- `docker compose up` verification; README.
- Full test suite green in container.

## Standing rules
- Mock LLM in all automated tests; live Strata2 only for manual smoke runs.
- Update PROGRESS.md and DECISIONS.md as we go; commit per milestone.
