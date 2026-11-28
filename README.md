# CodingFactory Lite

Idea → AI refinement → your approval → a **build-ready prompt** you hand to a
coding agent. Self-hosted, single operator, one shared LLM.

CodingFactory does the product work (refinement, architecture, an approved spec,
and a dependency-ordered task plan) and then emits a single, self-contained
**handoff prompt**. It does **not** build the software itself — you paste the
prompt into a more efficient coding agent (OpenCode, Claude, Codex, …) to run
the build and test phases.

Full design rationale in `goal.md`; the original maximal spec is `goal-full.md`.

## How it works

1. Submit an idea in the web UI.
2. A **Product Manager** agent analyzes it (confirmed requirements vs assumptions,
   MVP scope, user stories) and proposes *candidate* questions; an **Architect**
   agent critiques feasibility and consolidates all candidates into ONE ranked
   batch of at most three questions that materially change the build. Each is
   presented with why it matters, 2-3 options with a recommendation when the
   choices are known, and always a free-text answer; answer any subset and the
   team waits until the rest are answered. A well-specified idea skips
   questions entirely. If your answers themselves open new material unknowns,
   one **follow-up round** may ask up to two more questions — it only asks
   what your answers opened (`MAX_DISCOVERY_ROUNDS` caps the rounds).
3. Artifacts are generated (`product-requirements.md`, `architecture.md`,
   `user-stories.json`, `acceptance-criteria.json`, `open-questions.md`),
   versioned, and editable. Your decisions are carried into every artifact —
   even ones made many rounds ago — and consequential interpretations of your
   answers are flagged for you to verify.
4. **Nothing is finalized until you click Approve.** The Specs tab shows one
   compact review — **flagged (high-impact) assumptions to confirm** first,
   then in scope / out of scope / assumptions / decisions (with any
   superseded by a change request shown as history) & interpretations /
   acceptance criteria — sliced from the same versioned
   artifacts the coding agent will receive, with the full sources one click
   away. Approve sends the exact artifact versions that review showed; if
   anything changed since, the server refuses (409) and the review reloads for
   a fresh click. Approval then freezes those versions — later edits never
   enter the build prompt. Reject / request-revisions re-enter discovery
   (change requests use the same question + review flow).
5. A **Dev Manager** decomposes the approved spec into ≤12 dependency-ordered
   tasks. CodingFactory then assembles a **build handoff prompt** — the approved
   specs + the ordered task list + a build/test brief — and the project is
   COMPLETED.
6. Open the **Build prompt** tab: copy it to your clipboard or download it as a
   `.md` file, and hand it to any capable coding agent to do the build + tests.

To change your mind later, submit a **change request**: it re-enters discovery
for a delta cycle, you re-approve, and a fresh handoff prompt (version 2, 3, …)
is generated — this one aimed at the *existing* repository, labeling each task
as new, delivered, or superseded so the coding agent retires replaced work
instead of re-implementing it.

## Quick start (dev, macOS/Linux)

```bash
cp .env.example .env          # set LLM_API_KEY
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd ..
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000
# UI (dev, hot reload): cd frontend && npm install && npm run dev  -> :5173
# or serve the built UI from the backend: cd frontend && npm run build, then use :8000
```

Requires on PATH: `git` (optional, only for inspecting this repo). The app needs
no coding CLI, node toolchain, or project workspaces — it only plans and emits a
prompt.

## Docker deployment

```bash
cp .env.example .env          # fill LLM_API_KEY + STRATA2_IP (LLM host LAN IP)
./scripts/deploy.sh           # creates the data volume if missing, then builds + starts
open http://localhost:8000
```

On a fresh host the data volume does not exist yet; deploying with plain
`docker compose up` will fail with `external volume "codingfactory_cf-data"
not found`. `scripts/deploy.sh` creates it (a no-op if it already exists).

One container: FastAPI + built React UI, running non-root. The SQLite DB lives
on the external `codingfactory_cf-data` volume (WAL needs a named volume — bind
mounts break it on macOS; external so `docker compose down -v` can't delete it).
Backup from host: `docker compose cp app:/app/data/codingfactory.db ./backup.db`

## Testing

```bash
cd backend && .venv/bin/python -m pytest        # 99 tests, no network needed
cd frontend && npm test                          # vitest
./scripts/live_smoke.sh                          # optional: real-LLM discovery smoke
```

Tests use a scripted MockLLM. The handoff prompt is assembled deterministically
from artifacts + the task plan, so it is covered by plain unit tests (no LLM).

## Ops notes

- **One scheduler per data directory.** Don't run the host dev server and the
  Docker container against the same `./data` at the same time. Stop one first.
- **Resume**: restart the process and mid-flight projects re-dispatch from
  persisted state (discovery, or planning→handoff). The **Resume** button
  retries FAILED projects.

## Known limitations (accepted by design)

- The build is delegated: CodingFactory produces a prompt, it does not execute
  the build or run the generated project's tests.
- No auth: single trusted operator; do not expose port 8000 publicly.
- The handoff prompt is assembled from the approved spec + task plan (not
  LLM-polished); it is complete and ordered, but you may still want to tailor it
  to a specific agent before use.
