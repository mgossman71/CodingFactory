# Progress

## Status: Milestone 1 — in progress

### Environment verified
- Python 3.15.0, Node v26.9.0, Docker 29.8.0, git 2.54, gh 2.101
- OpenCode v2.3.2 — `opencode run` flags inspected (see DECISIONS D-004)
- Workspace was empty at start; `git init` done (branch `main`)

### Done
- [x] Lite spec written to `goal.md`; full spec preserved in `goal-full.md`
- [x] IMPLEMENTATION_PLAN.md, DECISIONS.md, .gitignore, scaffold underway

### Next
- [ ] Backend core: config, db, models, LLM gateway (+mock), health/project API, tests
- [ ] OpenCode CLI smoke test result (background)

### Blockers / notes
- `lxc-strata2` does not resolve from this machine; live LLM runs must happen on
  the LAN. All tests use MockLLM (D-006).
