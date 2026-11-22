# Progress

## Status: Milestones 1–6 in live verification; M7 (hardening/docs) underway

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

### In flight
- [ ] Docker end-to-end run (real LLM + OpenCode **inside the container** → COMPLETED)

### Blockers / notes
- Earlier partial failure (task 2 FAILED after 3 attempts) was a test artifact: a
  stray compose container shared ./data and raced the host pipeline (container
  paths in host-run attempt logs prove it). Resolved by tearing it down; re-testing
  cleanly in the container itself.
- LLM concurrency=1 ⇒ long runs by design; timeouts are generous.
- GitHub PR automation deliberately out of scope (lite).
