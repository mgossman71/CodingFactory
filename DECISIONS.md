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

## D-004 — OpenCode executes inside the backend container *(superseded by D-018)*
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

## D-007 — Sequential scheduler, resume by replay *(superseded by D-018)*
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

## D-012 — OpenCode v2 in Docker: host-fetched release binary, COPYed in *(superseded by D-018)*
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

## D-016 — Pipeline provisions the generated project's test environment *(superseded by D-018)*
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

## D-015 — Interruptions never consume the retry budget *(superseded by D-018)*
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

## D-010 — OpenCode shares the single LLM *(superseded by D-018)*
The docker entrypoint writes an OpenCode provider config generated from
LLM_BASE_URL/LLM_API_KEY/LLM_MODEL, so the coding agent uses the same model
as the planning agents (principle #1 extended to the executor).

## D-018 — Drop autonomous build; deliver a build handoff prompt
The autonomous build phase (OpenCode executor, sequential scheduler, per-task
test runs, Reviewer verdicts, fix loops, workspaces/git) was far too slow to be
useful and its output quality did not justify the runtime. The tool now **stops
at planning** and emits a single, self-contained **build handoff prompt**
(`handoff-prompt.md`): the approved specs + the Dev Manager's ordered task plan +
a build/test brief, assembled **deterministically** (no LLM call) so it can't
hallucinate. The operator copies/downloads it and hands it to a more efficient
coding agent to run the build + test phases.

Removed with it: `services/build.py`, `services/opencode.py`, `services/workspace.py`;
the `TaskAttempt`, `ExecutionLog`, `UserFeedback` models and the `BUILDING`
project state / `TaskStatus` enum; the Reviewer role (`REVIEWER_SYSTEM`,
`ReviewVerdict`) and `DEV_TASK_PREAMBLE`; the OpenCode config in the entrypoint,
the `live_e2e*` scripts, the fetched OpenCode binary, and the opencode/git
packages + `workspaces` mount in Docker. This supersedes D-004, D-007, D-010,
D-012, D-015, D-016 and the mid-build NOTE/REPLAN half of D-017 (kept: the
change-request loop, which now regenerates a new prompt version). A guarded
`ensure_columns` migration drops the now-unused tables/columns on existing dev
DBs; fresh installs never touch it. (D-018 also reverts the earlier "Reviewer
sees the diff" change — it was build-phase.)

## D-019 — Migrate pre-pivot rows, don't just drop schema
D-018's migration only dropped tables/columns, so build-era rows survived with
`state='BUILDING'` — a value the trimmed `ProjectState` enum no longer defines.
SQLAlchemy raised LookupError while loading them, which surfaced as `500` on
`GET /api/projects`. `ensure_columns` now also rewrites those values
(`BUILDING` -> `COMPLETED`, since such a project already had approved specs plus
a task plan), deletes the build-era `delivery-report.md` artifact, and drops
`ix_tasks_project_status` first (SQLite refuses DROP COLUMN on an indexed
column). Because a remapped project has tasks but no prompt artifact,
`Runner.resume()` backfills `handoff-prompt.md` at startup — deterministic, no
LLM call. Never delete-and-recreate the DB: existing projects are the user's
work product.

## D-020 — Prompts v5: spec what the product must *feel like*, not just what it must do
The first live package (gaming-PC-builder site) built correct plumbing and a
bland, improvised UI. Root cause: every quality gate in the pipeline was a
boolean/string check, and agents spend exactly the energy the artifacts demand.
25 ACs (7 of them absence checks), no design/brand/copy artifact anywhere, a
`testable: true` flag that literally discarded design criteria, and rationale
prose that read as "don't spend craft here."

Response (prompts v5):
- **Designer voice + three new artifacts** for user-facing products
  (`DESIGNER_SYSTEM`; `design-spec.md`, `brand-and-copy.md`, `quality-rubric.md`):
  palette/type/mood/imagery policy/reference sites; a verbatim copy deck; and a
  checklist of explicitly non-automatable feel criteria. The rubric is judged
  by self-review, never converted into string-checked ACs.
- **Genre reconciliation**: the PM infers genre and typical expectations; any
  exclusion of a genre-typical feature must be tagged
  "(intentionally excluded — confirm at approval)" so scope control is visible,
  not silent. Terse/ambiguous user answers are echoed as explicit
  interpretations into `open-questions.md` (system-assembled from the
  requirements call's `answer_interpretations`, keeping that doc's
  no-hallucinated-closure property).
- **Binding constraints vs rationale** in PM/Architect docs; Architect
  `recommended_stack` is schema-validated to one concrete choice per component
  (no "X or Y" menus — the implementing agent picks the laziest option).
- **Task categories** (`feature|infra|design|content|polish|test`): a
  user-facing project's first plan is hard-rejected (`PlanCoverageError`) if it
  lacks a design/polish and a content task, and the Dev Manager is re-asked
  once with the rejection as feedback. Delta (iteration > 0) plans are exempt.
- **Deferred assets get a placeholder strategy** in spec and handoff: empty
  visual fields are a quality failure, never a compliant state.
- **NFR bar** owned by the Architect doc: WCAG AA, fluid 320/768/1280,
  image-richness, no unstyled native elements above the fold.
- Handoff states the frame directly: **"PASSING TESTS IS THE FLOOR, NOT THE
  DEFINITION OF DONE"** and "scope limits features, never visual quality";
  the builder must self-review against the rubric and report per-item.

`user_facing` (bool on Project, from the PM analysis) gates the design
artifacts and plan coverage: CLIs/APIs/data tools don't get a design spec.
Additive migration: `projects.user_facing`, `tasks.category`.
Prompt version bumped 4 -> 5; `HANDOFF_BRIEF` changes are attributed by it.

## D-021 — Start-over restart and permanent delete
Two missing lifecycle controls:
- **`POST /projects/{id}/restart`** reprocesses from scratch: cancels any live
  discovery/plan task (`Runner.abort`, so a running LLM turn can't race the
  wipe), deletes all derived rows (messages, artifacts+versions incl. the
  handoff, tasks, approvals, and event history), resets the derived columns
  (`iteration`, `discovery_round`, `user_facing`, `error`), records
  `project_restarted` as the *first* event of the new run, and re-enters
  discovery. Allowed from any state. Kept: `id`, `name`, `idea`, `created_at`.
  Because `iteration` resets to 0, D-020's plan-coverage rule re-applies.
  Deliberate choices: the idea is not editable at restart (a new project is
  the vehicle for a different idea), and events are wiped too — restart is a
  genuinely blank slate, not an audit trail.
- **`DELETE /projects/{id}`** (204): `Runner.abort` then ORM cascade delete of
  the project and everything under it. No soft-delete/trash: single-user tool,
  the confirm dialog in the UI is the guard.
No schema changes. Restarting mid-workflow leaves the project in exactly the
fresh-creation shape, so crash-resume composes with it unchanged.

## D-022 — Resume routes by approval, planning is idempotent per iteration
Code review of the D-018..D-021 pivot found resume/plan bugs:
- **Resume** used "has any tasks" to pick PLANNING, so a change request that
  failed in discovery (or was rejected) at iteration > 0 skipped its own
  discovery + approval gate and planned deltas against the old spec. Resume now
  re-plans only if the latest approval *for the current iteration* is
  APPROVED; otherwise it re-runs discovery.
- **Plan idempotency**: tasks now carry `iteration`. If the current iteration
  already has tasks (failure after the plan was committed), `plan_project`
  returns them instead of asking for a duplicate "delta" set.
- **One spec view**: `handoff.approved_specs` is shared by the planner and the
  handoff — it excludes `handoff-prompt.md` (the planner used to read the old
  prompt as spec) and design docs when the product is no longer user-facing.
- Runner done-callbacks only evict their own task (abort + re-ensure race);
  the restart marker event is pinned past the old max id so event ids stay
  monotonic; the architect stack hedge check ignores parenthetical rationale.
Additive migration: `tasks.iteration`, `approvals.iteration` (existing rows
default to 0). Dropped the redundant `ix_tasks_project_number` index.
