# Decisions

## D-001 — Lite scope over goal-full.md
The full platform spec (multi-worker leases, execution service, Redis, Postgres,
metrics, Kanban) is overkill for a single supervised user. Keep the strong parts
(approval gate, bounded rounds, persistence, one LLM gateway) and drop the rest.
Original spec preserved in `goal-full.md`.

## D-002 — SQLite instead of PostgreSQL
Single operator, single host. SQLite in WAL mode gives durable persistence with
zero ops. SQLAlchemy keeps the door open to Postgres later.

## D-003 — No Redis; in-process orchestration
Pipeline runs as asyncio tasks inside the backend process. Durable state lives in
SQLite; resume-on-startup replays from persisted state. WS fan-out is in-process.

## D-004 — OpenCode executes inside the backend container
Docker Compose is the deployment target, so generated code runs in the backend
container with cwd scoped to `/workspaces/<project>`. Accepted limitation:
isolation is per-container, not per-build. Verified OpenCode v2.3.2 CLI:
`opencode run --format json --model <provider/model> --auto [--session <id>]`
with cwd scoping via the subprocess working directory.

## D-005 — Agents are single-shot structured calls
Each agent turn = one LLM request with role system prompt + assembled context +
JSON output schema, validated by Pydantic, one repair retry on invalid output.
Multi-step agent loops add risk (unreliable tool-calling) for little gain here.

## D-006 — Mock LLM everywhere in tests
`lxc-strata2` is LAN-only. All automated tests use a scripted MockLLM keyed by
role/step. Live endpoint is exercised only via the manual smoke checklist.

## D-007 — Sequential scheduler, resume by replay
Tasks run one at a time. On startup, projects in PLANNING/BUILDING resume:
tasks stuck in `running` with no live executor are requeued (idempotent: an
OpenCode run's effect is scoped to the workspace and re-runnable).
