# Progress

## Status: Discovery depth — follow-up round + flagged assumptions (D-047)

- [x] **Conditional follow-up Q&A round**: `max_discovery_rounds` (was dead
      config) wired into `advance_discovery`; PM ⇄ Architect turns are
      round-scoped, and after answers the pair re-runs ONCE with a narrow
      mandate — ask only what the answers themselves opened, at most 2 new
      questions, empty batch → straight to artifacts. Round count is derived
      from persisted turns (crash-resume stays idempotent; no new column).
- [x] **Blast-radius rule (prompts v8)**: material unknowns stay questions,
      never assumption bullets; `PMAnalysis.material_assumptions` feeds a
      `## Flagged assumptions (confirm these)` section in the requirements doc.
- [x] **Approval review surfaces them**: new `flagged_assumptions` slice
      (`_gather` exclude), rendered in SpecsTab as an amber confirm block
      above In scope.
- [x] **Tests**: `test_discovery_rounds.py` (6: follow-up asks only what
      answers opened / empty-batch fast path / cap-of-2 trim / no third pass /
      partial answers never start a round / rounds=1 legacy); conftest
      `settings` indirect-parameterizable; +2 vitest (flagged block
      present/absent). 126 backend + 63 vitest, tsc + production build clean.

## Previous status: Generation-guard redesign (D-044)

- [x] **Shared `useDraftGeneration` hook**: projectId is part of the
      generation (no parent-effect epoch bump on project switch — child
      effects run first and used to drop the new project's responses);
      `resetGeneration()` updates ref synchronously (no same-tick window).
- [x] **SpecsTab keyed `{projectId}:{draftsEpoch}`**: Start over / project
      switch remounts the editor — stale selection, review, history, and
      error state cannot survive; the fresh mount refetches (the real
      restart handler never bumped `version`; tests had papered over it).
- [x] **Pending saves**: reset zeroes the count; save()'s finally
      decrements only for a live generation, so a late save from a dead
      project/restart can no longer re-enable Save/Approve mid-save.
- [x] **Tests use the real hook**, not a copy: +3 hook unit tests, +2
      component regressions (project switch keeps the new project's data;
      dead-generation save cannot release the live save's block).
- [x] **Verification**: 109 backend + 55 vitest (6 files), tsc +
      production build clean.

## Previous status: Request-lifecycle hardening (D-042, D-043)

- [x] **P2 Generation checks moved to ProjectPage (D-042)**: the save
      response check lived in SpecsTab, whose view freezes on unmount —
      leave the tab mid-save, restart while away, return, and the old
      callback's check passed against its own dead generation and restamped
      the new drafts. ProjectPage owns `captureGeneration` /
      `isCurrentGeneration` over its live ref; SpecsTab only stamps and
      asks.
- [x] **P2 Late artifact fetches re-seeded cleared drafts (D-043)**: a
      pre-restart fetch landing after the clear seeded old text, and the
      regenerated same-slug/same-version artifact made `refetched` keep it
      forever. The artifact fetch, review, and version-history responses are
      now stamped and discarded unless their generation is still current.
- [x] **Verification**: 109 backend + 50 vitest (+2 component tests:
      delayed save across tab-switch AND restart; stale artifact fetch
      across restart), tsc + production build clean.

## Previous status: Editor restart-flow hardening (D-040, D-041)

- [x] **P2 Restart seeded the wrong artifact's text (D-040)**: the refresh
      kept the stale selection and initialized it from the FIRST artifact's
      content (criteria text inside the requirements editor — Save wrote it
      there). Drafts now always initialize from the artifact matching the
      slug (`resolveSelectedSlug`); a selection whose slug vanished resets
      instead of leaving a ghost draft that blocks approval.
- [x] **P2 Late save responses survived restart (D-041)**: a response
      landing after the drafts reset updated a cleared/regenerated draft —
      throwing or stamping an old version number. Saves are stamped with
      `{projectId, draftsEpoch}`; responses from a dead generation are
      discarded; `applySaved` never resurrects a missing draft.
- [x] **Verification**: 109 backend + 48 vitest (+6: 3 pure-helper, 3
      component restart tests), tsc + production build clean.

## Previous status: Approval-boundary hardening, round 3 (D-038, D-039)

- [x] **P1 Approve validated one read and froze another (D-038)**: the
      endpoint read the numeric snapshot (for the D-029 check AND the stored
      freeze) and the immutable version ids in separate queries and accepted
      either field independently — an edit in between let an ids-only request
      naming v2 return 200 with v1 pinned. The APPROVED branch now derives
      the freeze map and BOTH validation maps from a single `current_rows`
      read. Regressions: stored freeze == exactly the validated rows (one
      read, recorded); ids-only approval freezes what its ids name.
- [x] **P2 Tab switch bypassed the pending-save guard (D-039)**: the
      pending-save count moved from SpecsTab to ProjectPage, next to the
      drafts — a save in flight stays blocked across tab switches. Component
      test: toggle Specs off/on during a delayed save → Save still
      disabled/"Saving…", Approve still blocked, no overlapping request,
      only the delayed response releases the block.
- [x] **Verification**: 109 backend tests (+2) + 42 vitest (+1), tsc +
      production build clean.

## Previous status: Approval-flow code-review hardening, round 2 (D-034..D-037)

- [x] **P1 Restart defeats the review map (D-034)**: approval is bound to the
      immutable artifact-version row IDs the review rendered, not just
      slug+version numbers — "Start over" recreates artifacts under the same
      names at the same numbers, so a pre-restart map used to approve the new
      spec (200). Now it is a 409 with nothing recorded; regression drives
      restart → collision → 409 → fresh click → 200.
- [x] **P1 Revision comments supersede by message order (D-035)**: one rule
      for change requests AND approval-gate revision comments — answers
      recorded before the latest operator intent (by `seq`) are HISTORY,
      never binding; the review's decision split follows.
- [x] **P2 Drafts outlive tab switches (D-036)**: the per-artifact draft map
      is owned by ProjectPage, so Discussion/Tasks/Activity unmounts can't
      destroy unsaved edits and silently re-enable Approve.
- [x] **P2 Overlapping saves (D-037)**: pending saves are counted (not one
      shared boolean) and Save is disabled while saving — a completed save
      can't re-enable Approve while another is in flight.
- [x] **Verification**: 107 backend tests (+2) + 41 vitest (+3, including the
      first jsdom/Testing-Library component tests: delayed save responses and
      unmount/remount draft survival), tsc + production build clean.

## Previous status: Approval-flow code-review hardening (D-030..D-033)

- [x] **P1 Review atomicity (D-030)**: contents + versions come from ONE
      query set (`review.current_rows`); the displayed text and the source
      map can no longer disagree, so a stale-map approval can't freeze text
      the operator never saw. Regression: the review issues exactly one
      artifact query. Also: paragraphs count as section items, and a missing
      section (None) is distinct from an explicitly empty one ([]).
- [x] **P1 Decision supersession (D-031)**: change requests + revision
      comments are MOST RECENT INTENT in every agent prompt; answers that
      predate a change request move to HISTORY (never re-issued as binding)
      and to `decision_history` in the review (struck-through "may have been
      revised" in the UI).
- [x] **P2 Per-artifact drafts (D-032)**: `Record<slug, DraftPolicy>` —
      switching artifacts never discards typing or re-enables Approve; a
      save clears dirty only for the exact content saved.
- [x] **P2 Self-contradictory submissions (D-033)**: duplicate question ids
      or blank answers reject the whole /answers request (422, no rows).
- [x] **Verification**: 105 backend tests (+6) + 38 vitest (+5), tsc +
      production build clean.

## Previous status: Better questioning + compact approval review (D-028, D-029)

- [x] **One ranked question batch (D-028)**: the PM proposes candidates
      (`kind="question_candidate"`, never operator-facing); the Architect
      consolidates into ONE batch of ≤3, ranked, with why-it-matters,
      2-3 options + recommendation (free-text when options would hide
      context) persisted in the new `agent_messages.meta` JSON column
      (NULL = legacy plain-text questions, still answerable). Gate is
      current-iteration only; partial submissions re-arm it; stale,
      cross-iteration and cross-project question ids are 422-rejected;
      resume between the PM/Architect turns never duplicates calls or
      questions. Operator decisions ride into every artifact prompt via an
      `OPERATOR DECISIONS` block, beyond the 30-message window. Prompts v7.
- [x] **Compact approval review (D-029)**: `GET /projects/{id}/review`
      (`services/review.py`) slices current artifact versions into
      in/out of scope, assumptions, decisions & interpreted answers,
      unresolved, and acceptance criteria — with full sources (slug+version)
      listed. Approve optionally carries the reviewed version map: a stale
      map is a 409 with per-slug diffs that records nothing; the frozen
      snapshot stays server-computed (D-023 intact). Approve is disabled on
      dirty drafts / in-flight saves / unresolved conflicts.
- [x] **Verification**: 99 backend tests (+22: consolidation/truncation,
      meta persistence, partial + invalid answers, legacy rows, resume,
      decision carry-through, review parsing/edits, version-map 409,
      reminders E2E in-app-vs-email, well-specified idea straight to
      approval) + 33 vitest (+15: question form policy, approval gate
      helpers). tsc + production build clean.

## Previous status: Code-review hardening — five findings (D-023..D-027)

- [x] **P1 Approval freeze (D-023)**: approval captures
      `artifact_versions` (slug → version); the planner, the handoff, the
      startup backfill, and Resume retries all read that snapshot — an edit
      landing during planning can no longer leak into the build prompt.
      Additive migration; legacy rows fall back to current versions.
- [x] **P1 Iteration handoff brief (D-024)**: prompts v6 — change-request
      handoffs get an existing-repository brief; delta plans carry
      `supersedes`; every task in a delta handoff is labeled NEW / delivered /
      SUPERSEDED (planner context + Tasks tab too). Additive migration.
- [x] **P1 Spec editor drafts (D-027)**: refreshes preserve dirty drafts and
      flag a newer server version instead of wiping the textarea
      (`src/specDraft.ts` pure policy + banner + "discard my edits, load vN").
- [x] **P2 Cancel race (D-026)**: cancel aborts the live runner task; every
      post-model-call write in discovery (and planning) re-checks the live
      project state before persisting.
- [x] **P2 Export order (D-025)**: the handoff emits tasks in topological
      order with task number as tie-breaker.
- [x] Tests: **77 backend** green (16 new: snapshot capture/pinning/leak-resume,
      supersedes flow + validation, topological order, cancel-during-model-call,
      discovery write guard), **vitest 18** green (+9 specDraft),
      `tsc --noEmit` + `npm run build` clean.

## Previous status: Project lifecycle — start-over + delete (D-021)

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
