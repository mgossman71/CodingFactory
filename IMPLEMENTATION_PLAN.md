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

## Milestone 3 — Planning & handoff
- Dev Manager task decomposition (≤12 tasks, deps + cycle validated).
- Deterministic **handoff assembly**: combine the approved artifact versions +
  the ordered task plan + a build/test brief into one `handoff-prompt.md`.
- `GET /projects/{id}/handoff` serves it as a downloadable text file.
- COMPLETED state; no build execution in this tool (delegated to an external
  coding agent via the prompt).
- **Tests:** plan validation (dups/cycles/unknown deps/delta re-plan), handoff
  assembly (embeds specs + ordered tasks, excludes itself), e2e idea→handoff.

## Milestone 4 — Web UI
- React app: dashboard, new project, project page (Discussion / Specs / Tasks /
  Build prompt / Activity tabs), live WS updates, artifact editor,
  approve/reject/revise, change-request panel, Copy/Download of the prompt.
- **Tests:** Vitest on state/utilities; manual walkthrough.

## Milestone 5 — Deployment & hardening
- Dockerfile (single image: backend + built React UI; python3 only — no coding
  CLI, git, or workspaces), docker-compose.yml with healthcheck, named `data`
  volume, extra_hosts, .env.example.
- `docker compose up` verification; README.
- Full test suite green in container.

## Standing rules
- Mock LLM in all automated tests; live Strata2 only for manual smoke runs.
- Update PROGRESS.md and DECISIONS.md as we go; commit per milestone.
