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

## D-008 — Single-container deployment (changed from two services)
FastAPI serves the built React bundle; one container runs backend + UI +
OpenCode CLI (node:22 base, python3, git). Simpler ops than nginx + backend
for a single-user tool. Volumes: ./data, ./workspaces.

## D-009 — Prompts v2 after live smoke: stack constraints + question dedup
The live Strata2 smoke showed the Architect proposing Tauri/Rust (unbuildable
headless in our executor) and duplicating the PM's questions. Prompts now
constrain stacks to Python/Node web apps and forbid re-asking questions.
Question messages carry `answered`; the answers endpoint links answers to
questions via `question_id`, and open-questions.md excludes resolved ones.

## D-011 — Structured-output hardening after first live build failure
The live pipeline failed once mid-artifact-generation: the model replied in
prose (no JSON at all). Response: (1) the JSON-only rule is repeated in the
system message by chat_json, (2) repair attempts default raised 1→2,
(3) artifact prompts cap length (~900 words), (4) gateway detects
finish_reason=length as a retryable `truncated` error and can send
`max_tokens` (LLM_MAX_TOKENS, default 0 = server default).

## D-012 — OpenCode v2 in Docker: host-fetched release binary, COPYed in
`opencode.ai/install` and the `opencode-ai` npm package only ship the v1
line; v2.3.2 exists only as release binaries (anomalyco/opencode). GitHub
release downloads also 404 from inside Docker Desktop's build network, so
`scripts/fetch_opencode.sh` downloads the arch-correct tarball on the host
and the Dockerfile COPYs it (chown'd to the non-root user). Verified:
`opencode v2.3.2` runs inside the container.

## D-017 — Iteration: change requests & mid-execution user input
A COMPLETED project had no path back in. Now: `POST /projects/{id}/feedback`
accepts user input at any point after the approval gate.
- **COMPLETED** → the feedback is a change request: `iteration += 1`, the
  text is stored as a `change_request` message, and the project re-enters
  DISCOVERY. Discovery idempotency (PM/Architect turns, Q&A gate) is scoped
  per iteration so a delta cycle runs a fresh PM/Architect pass while keeping
  the whole prior discussion + current specs as context. Approval gate again,
  then the Dev Manager plans *delta* tasks (numbered from max+1; may depend
  on prior task numbers; re-planning an existing number is a plan error).
  The same workspace and git history carry the new commits; the delivery
  report is a versioned artifact and re-saves (v2, v3, …).
- **BUILDING/PLANNING** → stored in `user_feedback`: a NOTE is injected into
  the next task's developer instructions at attempt start; a REPLAN pauses
  the build at the next attempt boundary (current attempt always finishes)
  and enters the same change-request cycle. Consumption is persisted
  (consumed_at), so crash/restart can't double-apply feedback.
- Schema changes ride an additive SQLite migration (`ensure_columns`):
  `projects.iteration`, `agent_messages.iteration`, new `user_feedback` table.

## D-016 — Pipeline provisions the generated project's test environment
Live gaming-pc build: OpenCode wrote a FastAPI+Jinja2 app, but the pipeline's
test phase ran `python -m pytest` in the backend's venv → `ModuleNotFoundError:
jinja2` (and OpenCode itself couldn't pip install into the read-only system
Python). Now before every test run the pipeline provisions the workspace:
`python3 -m venv .venv` if missing, `pip install -r requirements.txt`,
`pip install pytest` only if absent, `npm ci/install` for Node projects;
tests then run with `.venv/bin/python`. Dev-task prompt v3 tells the agent
the same rules. Resume semantics: explicit /resume marks old FAILED attempts
SUPERSEDED (retry budget resets; attempt numbers stay cumulative to respect
the UNIQUE constraint).

## D-015 — Interruptions never consume the retry budget
A container restart SIGTERM'd an in-flight OpenCode run; the attempt was
recorded FAILED and burned the task's last retry, failing the whole project
even though the build was mid-success. Now: attempts ending in 143/137
(SIGTERM/SIGKILL) or left RUNNING across a restart are marked INTERRUPTED;
the retry cap counts only consumed attempts (PASSED/FAILED), with a separate
bound (3 consecutive interruptions) so a genuinely fatal loop can't spin
forever. `task.attempts` remains the total try count for display.

## D-014 — SQLite on a named volume in Docker, never a bind mount
The container run failed task attempts with exit 143 (SIGTERM): SQLite WAL
mode threw `disk I/O error` on the `./data` bind mount — virtiofs (Docker
Desktop macOS) lacks the mmap/flock semantics WAL's -shm file needs. The I/O
errors raised inside the scheduler, whose cleanup then terminated the running
OpenCode child. Fix: `cf-data` named volume for `/app/data` (ext4 in the VM).
`./workspaces` stays bind-mounted (plain file I/O is fine there). Host dev
mode keeps using the local `./data` file.

## D-013 — `STRATA2_IP` for compose extra_hosts
On the Mac dev host, `lxc-strata2` resolves via local DNS (`.gossman.cloud`
search domain) to a LAN address; `host-gateway` alone sends the container to
Docker's VM gateway instead, so connections failed. `.env` now sets
`STRATA2_IP=<LAN IP>` and compose interpolates it into `extra_hosts`.
Verified: `llm:200` from inside the container.

## D-010 — OpenCode shares the single LLM
The docker entrypoint writes an OpenCode provider config generated from
LLM_BASE_URL/LLM_API_KEY/LLM_MODEL, so the coding agent uses the same model
as the planning agents (principle #1 extended to the executor).
