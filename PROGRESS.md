# Progress

## Status: Project lifecycle — start-over + delete (D-021)

- [x] `POST /projects/{id}/restart`: cancels the live workflow task
      (`Runner.abort`), wipes all derived state (discussion, artifacts +
      versions incl. handoff, tasks, approvals, event history), resets
      `iteration`/`discovery_round`/`user_facing`/`error`, records
      `project_restarted` as event #1, re-runs discovery from the original
      idea. Allowed from any state.
- [x] `DELETE /projects/{id}` (204): cascade-removes the project and all
      children.
- [x] UI: **Start over** and **Delete** buttons on the project page (confirm
      dialogs; delete returns to the dashboard).
- [x] Tests: **55 backend** green (6 new lifecycle tests: wipe-and-reprocess,
      re-questioning, 404s, cascade counts, `Runner.abort` cancellation),
      vitest 3 green, `tsc --noEmit` clean.

## Previous status: Prompts v5 — craft pipeline for user-facing products (D-020)

The first live package built correct plumbing but a bland, improvised UI: the
pipeline gated only on string-checkable ACs and said nothing about feel. v5
makes craft a first-class artifact:

- [x] Designer voice + `design-spec.md`, `brand-and-copy.md`, `quality-rubric.md`
      artifacts, generated only when the PM marks the product `user_facing`
      (new `Project.user_facing` column, additive migration).
- [x] PM genre inference: genre expectations listed; excluded genre-typical
      features tagged "(intentionally excluded — confirm at approval)"; terse
      answers echoed as explicit interpretations in `open-questions.md`.
- [x] `AcceptanceCriterion.testable` removed (it discarded design criteria);
      AC prompt is now "independently verifiable".
- [x] Architect stack is schema-validated: one concrete choice per component,
      no "X or Y" menus; NFR block (WCAG AA, 320/768/1280, above-the-fold
      styling) required in the architecture doc.
- [x] Task `category` field (`feature|infra|design|content|polish|test`);
      user-facing first plans hard-rejected without design + content tasks and
      the Dev Manager re-asked once (`plan_coverage_rejected` event); category
      shown in UI tasks tab and handoff prompt.
- [x] Handoff brief: "PASSING TESTS IS THE FLOOR, NOT THE DEFINITION OF DONE";
      rubric self-review + per-item reporting; copy deck verbatim; deferred
      assets get placeholder visuals, never empty fields.
- [x] Tests: 49 backend green (9 new in `test_craft.py`; legacy-DB migration
      test extended for the new columns), vitest + `tsc --noEmit` clean.

## Previous status: Pivot complete — build delegated via handoff prompt (D-018)

The autonomous build phase (OpenCode executor, scheduler, per-task tests,
Reviewer fix loops, workspaces) was removed: too slow, mediocre output. The
tool now plans and emits a single deterministic **build handoff prompt**
(`handoff-prompt.md`) for an external coding agent to execute.

- [x] Removed: `build.py`, `opencode.py`, `workspace.py`; `TaskAttempt`,
      `ExecutionLog`, `UserFeedback` models; `BUILDING` state, `TaskStatus`;
      Reviewer role + `DEV_TASK_PREAMBLE`; OpenCode entrypoint config,
      `live_e2e*` scripts, fetched OpenCode binary; opencode/git + workspaces
      mount in Docker. (Also reverted the earlier "Reviewer sees the diff" work.)
- [x] Added: `services/handoff.py` (`generate_handoff`), `HANDOFF_BRIEF` prompt,
      `GET /projects/{id}/handoff` (download), frontend **Build prompt** tab
      (Copy/Download), change-request loop now regenerates a prompt version.
- [x] Guarded DB migration for existing dev DBs: drops the unused tables/columns
      (index first, so `tasks.status` can go), rewrites removed `BUILDING` rows to
      `COMPLETED`, deletes the build-era `delivery-report.md` artifact.
- [x] `Runner.resume()` backfills a missing `handoff-prompt.md` for COMPLETED
      projects that already have a plan — deterministic, no LLM call — so pre-pivot
      projects get their build prompt on startup.
- [x] Tests rewritten: **40 backend** (incl. a legacy-DB migration test that
      reproduces the 500) + vitest green; `npm run build` clean;
      grep sweep finds no build-phase symbols in source.

### Earlier history (pre-pivot)

### Environment verified
- Python 3.15.0, Node v26.9.0, Docker 29.8.0, git 2.54, gh 2.101
- OpenCode v2.3.2 — `opencode run --format json --auto [--model]` verified
- Live LLM reachable: `lxc-strata2:8080` answers (key taken from opencode
  config into gitignored `.env`)

### Done
- [x] Lite spec (`goal.md`), plan, decisions D-001..D-010
- [x] **M1 Backend core**: config, models (9 tables), LLM gateway (semaphore,
      bounded retries, structured errors), events + WebSocket. Tests green.
- [x] **M2 Agent runtime + discovery**: PM/Architect roles, JSON-schema
      validated outputs w/ one repair, question/answer loop, versioned
      artifacts, approval gate. Tests green.
- [x] **M3 Build pipeline**: Dev Manager planning w/ cycle+dep validation,
      sequential dep-aware scheduler, OpenCode adapter (real + mock + fake-bin
      tests), test detection/run, reviewer verdicts, bounded fix loops,
      stale-task recovery, resume endpoint. Tests green (40).
- [x] **LIVE SMOKE passed**: full discovery vs real Strata2 — real questions,
      answer flow, all 5 artifacts, AWAITING_APPROVAL reached (SMOKE_OK).
      Findings fixed in prompts v2 (D-009): stack constraints, question dedup.
- [x] **M4 Web UI**: dashboard, discussion viewer w/ live answers, spec editor
      + approval controls, task board w/ attempt logs, activity feed; WS +
      polling fallback. Built with Vite; served by backend. Vitest green.
- [x] **M5 Docker**: single-image deployment (backend+UI+OpenCode, non-root,
      pinned opencode@2.3.2), compose with healthcheck/volumes/restart,
      entrypoint pins OpenCode to the same LLM (D-010).

### Hardening found via live runs (all fixed)
- [x] SPA deep-link 404: history fallback + router Links (test added)
- [x] Entrypoint heredoc escaping: OpenCode provider model key literal
- [x] D-013: STRATA2_IP extra_hosts (container → LLM verified 200)
- [x] D-014: SQLite on named volume (WAL breaks on macOS bind mounts)
- [x] D-015: interrupted attempts (SIGTERM/restart) don't burn retry budget

### In flight
- [x] **Iteration (D-017)**: change requests from COMPLETED + mid-build
      NOTE/REPLAN feedback via `POST /feedback`, iteration-scoped discovery,
      delta planning, steering in the scheduler, UI FeedbackPanel. 54 tests.
- [x] **FIRST FULL END-TO-END SUCCESS**: user's "Gaming PC builder website." —
      idea → real-LLM discovery → approval → 12 tasks built by OpenCode in
      Docker → 553 tests passed → delivery report. ~59 min, all 12 tasks
      COMPLETED (see workspaces/gaming-pc-builder-website/).

### Blockers / notes
- Earlier partial failure (task 2 FAILED after 3 attempts) was a test artifact: a
  stray compose container shared ./data and raced the host pipeline (container
  paths in host-run attempt logs prove it). Resolved by tearing it down; re-testing
  cleanly in the container itself.
- LLM concurrency=1 ⇒ long runs by design; timeouts are generous.
- GitHub PR automation deliberately out of scope (lite).
