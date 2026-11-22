# Project: CodingFactory — Autonomous AI Software Development Platform

You are the lead software engineer responsible for designing, implementing, testing, and documenting **CodingFactory**, a self-hosted autonomous AI software development platform.

## Mission

Build a production-oriented web application where a user submits a software project idea, an AI Product Manager and AI Architect collaboratively refine that idea, and an autonomous development team turns the approved specification into working, tested software.

**This is an implementation assignment, not a request for a proposal. Write the actual application.**

Work autonomously through development, testing, and documentation. Do not stop after generating architecture diagrams or planning documents.

## 1. Core Principles

1. **One LLM:** All AI agents must use the same configurable OpenAI-compatible endpoint and model.
2. **Multiple roles:** Each agent has its own system prompt, context, tools, and permissions.
3. **Agent collaboration:** Agents exchange structured information and challenge each other's decisions.
4. **Persistent execution:** Tasks, conversations, decisions, artifacts, and checkpoints survive application restarts.
5. **Human approval:** The user must approve the project specification before development begins.
6. **Autonomous development:** After approval, agents should implement, test, review, and correct code without unnecessary intervention.
7. **Observable execution:** The web UI must display what agents are doing in near real time.
8. **Controlled inference:** All LLM requests pass through a shared concurrency-limited gateway.
9. **Security:** AI-generated commands must execute in isolated environments, never directly on the application host.
10. **Extensibility:** Agent roles, workflows, prompts, models, and execution strategies must be configurable.

## 2. Technology Stack

Use:

- Backend: Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic
- Frontend: React, TypeScript, Vite, Tailwind CSS
- Database: PostgreSQL
- Queue/coordination: Redis
- Live updates: WebSockets
- AI integration: OpenAI-compatible API
- Development worker: OpenCode CLI adapter
- Source control: Git and GitHub CLI
- Deployment: Docker Compose
- Testing: pytest, Vitest, Playwright
- Containerized execution: isolated Docker workers

Prefer well-maintained libraries and straightforward implementations over unnecessary frameworks.

## 3. AI Configuration

All agents share one model.

The model configuration must be provided through environment variables:

```env
LLM_BASE_URL=http://lxc-strata2:8080/v1
LLM_API_KEY=
LLM_MODEL=qwen3.8-flash-next-iq3_s
LLM_MAX_CONCURRENT_REQUESTS=1
LLM_REQUEST_TIMEOUT=600
```

Do not hardcode credentials.

The application must allow the user to configure the endpoint, model name, inference concurrency, and timeouts through an administrative settings interface.

Provide an endpoint connectivity test.

Support streaming responses when available.

Implement retry handling, request cancellation, timeouts, and structured error reporting.

Do not assume the model's tool-calling implementation is perfectly reliable. Validate all tool-call arguments and structured responses.

## 4. Agent System

Implement a reusable agent execution framework.

Each agent must have:

- Unique identity and role
- Versioned system prompt
- Independent conversation context
- Defined tool permissions
- Structured input and output schemas
- Execution state and history
- Maximum execution steps
- Timeout and retry policies
- Persistent checkpoints
- Access to relevant project artifacts

### Required agents

**Product Manager**

Responsible for:

- Understanding the user's initial idea
- Identifying missing requirements
- Defining product scope
- Writing user stories
- Creating acceptance criteria
- Identifying MVP features
- Prioritizing features
- Raising questions requiring user input

The Product Manager must distinguish confirmed requirements from assumptions.

**Software Architect**

Responsible for:

- Evaluating technical feasibility
- Designing application architecture
- Selecting technologies
- Defining APIs and data models
- Identifying dependencies
- Establishing security and deployment requirements
- Identifying technical risks
- Challenging unrealistic requirements

**Development Manager**

Responsible for:

- Converting approved specifications into development tasks
- Defining dependencies
- Establishing implementation order
- Assigning tasks to development agents
- Tracking progress
- Managing retries and blocked tasks

**Developer**

Responsible for:

- Understanding assigned tasks
- Inspecting repository code
- Creating and modifying files
- Implementing functionality
- Writing unit tests
- Running appropriate development tools
- Reporting implementation results

Use OpenCode as the initial coding execution adapter.

**QA Engineer**

Responsible for:

- Verifying acceptance criteria
- Running automated tests
- Identifying defects
- Creating actionable bug reports
- Confirming defect resolution

**Code Reviewer**

Responsible for:

- Reviewing implementation quality
- Identifying bugs and security problems
- Checking adherence to architecture
- Detecting missing functionality
- Approving changes or requesting corrections

**Release Manager**

Responsible for:

- Verifying completion criteria
- Coordinating integration
- Producing release notes
- Creating GitHub pull requests
- Preparing delivery summaries

All roles must use the same configured model.

## 5. Product Discovery Workflow

The user creates a project by entering an idea in plain English.

Example:

"Build a vehicle maintenance application that tracks maintenance schedules for cars, motorcycles, ATVs, and lawn equipment."

The system should:

1. Create a project record.
2. Ask the Product Manager to analyze the idea.
3. Ask the Architect to evaluate technical implications.
4. Let the Product Manager and Architect exchange structured feedback.
5. Identify disagreements, assumptions, and missing requirements.
6. Resolve issues where possible.
7. Ask the user targeted questions when necessary.
8. Incorporate user answers into the specifications.
9. Generate a complete product requirements document.
10. Generate a technical architecture document.
11. Present the combined proposal for user approval.

Agent collaboration must not become an endless conversation.

Implement configurable maximum discussion rounds, explicit completion conditions, and escalation when agents cannot resolve an issue.

Every discussion turn must be visible in the UI and persisted.

### Required artifacts

- product-requirements.md
- architecture.md
- user-stories.json
- acceptance-criteria.json
- technical-decisions.md
- open-questions.json

Artifacts must be versioned and editable.

The user can approve, reject, or request revisions.

**Development cannot begin without explicit approval.**

Changes after approval must be tracked against the approved baseline.

## 6. Development Workflow

After approval:

1. Development Manager reads approved artifacts.
2. Generate an implementation plan.
3. Break work into small, testable tasks.
4. Establish task dependencies.
5. Create a Git branch and isolated workspace.
6. Assign the next eligible task to a Developer.
7. Execute the implementation using OpenCode.
8. Run build, lint, and test commands.
9. Send results to QA.
10. Send implementation to Code Reviewer.
11. Generate correction tasks for failed reviews.
12. Repeat until acceptance criteria pass or retry limits are reached.
13. Integrate approved work.
14. Prepare a GitHub pull request.
15. Generate a delivery report.

Implement a dependency-aware task scheduler.

Initial development execution should be sequential, but the architecture must support multiple workers later.

Never allow unlimited retries or unlimited agent loops.

Failed tasks should remain inspectable and resumable.

## 7. OpenCode Integration

Build an adapter around OpenCode.

The adapter must support:

- Launching OpenCode for an assigned task
- Providing task instructions and context
- Selecting the configured model
- Restricting the working directory
- Capturing stdout, stderr, and execution status
- Detecting process failure
- Enforcing timeouts
- Cancelling execution
- Returning structured results
- Retaining logs
- Recovering interrupted tasks

Inspect the installed OpenCode version and its supported CLI/API capabilities before implementing the adapter.

Do not assume specific CLI flags or configuration formats without verification.

OpenCode executions must happen inside isolated worker environments.

Provide a mock execution adapter for automated testing.

## 8. Workflow Orchestrator

Implement durable workflow orchestration using a database-backed state machine and worker queue.

Required project states:

- IDEA
- DISCOVERY
- AWAITING_INPUT
- ARCHITECTURE
- AWAITING_APPROVAL
- PLANNING
- DEVELOPMENT
- TESTING
- REVIEW
- DELIVERY
- COMPLETED
- BLOCKED
- FAILED
- CANCELLED

Support:

- Pause and resume
- Cancellation
- Human approval gates
- Crash recovery
- Task retries with backoff
- Execution timeouts
- Dependency tracking
- State transition validation
- Event history
- Idempotent task dispatch
- Lease-based worker ownership
- Recovery of expired worker leases

Avoid duplicate execution after application restarts.

Side-effecting actions, including Git operations and pull-request creation, must be reconciled before retries.

## 9. Persistent Data Model

Create database models and Alembic migrations for:

- users
- projects
- project_requirements
- agents
- agent_sessions
- agent_messages
- agent_runs
- workflows
- workflow_events
- tasks
- task_dependencies
- task_attempts
- artifacts
- artifact_versions
- approvals
- execution_logs
- workspaces
- repositories
- application_settings

Use UUID identifiers where appropriate.

Store timestamps in UTC.

Do not store API keys or secrets in plaintext database fields.

Implement appropriate indexes, foreign keys, and uniqueness constraints.

## 10. Web Application

Build a polished, responsive application.

### Main Dashboard

Display:

- Active projects
- Current project states
- Running agents
- Pending approvals
- Blocked tasks
- Recent activity
- Completed projects

### New Project

Provide an interface to submit an idea and optionally select an existing repository.

### Project Workspace

Provide separate views for:

- Overview
- Agent Discussions
- Requirements
- Architecture
- Development Tasks
- Execution Logs
- Code Changes
- Test Results
- Approvals
- Deliverables

### Agent Discussion Viewer

Show messages from the Product Manager, Architect, and other agents.

Clearly distinguish agent roles.

Support live updates, timestamps, and conversation history.

The user must be able to interrupt refinement, submit additional requirements, and request revisions.

### Development Board

Implement a Kanban-style view with:

- Backlog
- Ready
- In Progress
- Testing
- Review
- Blocked
- Completed

Show task ownership, dependencies, attempts, and results.

### Settings

Support configuring:

- LLM endpoint
- Model name
- Request timeouts
- Concurrency
- Agent prompts
- Workflow limits
- GitHub integration
- Worker limits

Provide useful configuration validation.

## 11. Execution Security

Treat generated code, repository contents, and agent outputs as untrusted.

Implement:

- Isolated disposable workers
- Non-root execution
- Resource limits
- Execution timeouts
- Workspace isolation
- Restricted network access
- Restricted credential access
- Command audit logs
- Secrets redaction
- Human approval for privileged actions

Do not mount the Docker socket inside untrusted worker containers.

Do not expose the host filesystem or Proxmox management interfaces to coding agents.

The control plane should launch workers through a narrowly scoped executor service.

Never allow AI agents to bypass approval requirements.

## 12. Observability

Record:

- Agent start and completion times
- LLM request counts
- Prompt and completion token counts when reported
- Task durations
- Tool executions
- Error rates
- Retry counts
- Development throughput
- Test pass rates
- Human intervention frequency

Expose structured logs and health endpoints.

Add Prometheus-compatible metrics.

Provide a real-time activity feed in the frontend.

## 13. Docker Deployment

Create a working Docker Compose deployment with:

- frontend
- backend
- workflow-worker
- execution-service
- PostgreSQL
- Redis

Include:

- Dockerfiles
- docker-compose.yml
- .env.example
- Health checks
- Persistent volumes
- Restart policies
- Database migrations
- Startup instructions

Avoid publishing database, Redis, or worker management ports unnecessarily.

The application should be deployable on an Ubuntu 24 system.

## 14. Automated Testing

Implement meaningful tests for:

- Agent execution
- Structured outputs
- Workflow state transitions
- Agent collaboration
- Approval enforcement
- Task dependency scheduling
- Failed task recovery
- Duplicate execution prevention
- Artifact versioning
- OpenCode adapter
- API endpoints
- Frontend components

Use mocked LLM responses so tests do not require access to Strata2.

Include at least one end-to-end test covering:

Idea submission → PM/Architect refinement → Human approval → Task generation → Mock development → QA → Review → Delivery.

## 15. Engineering Standards

- Use typed interfaces and Pydantic models.
- Keep components modular.
- Avoid giant files and tightly coupled services.
- Use database migrations rather than manual SQL setup.
- Provide descriptive errors.
- Validate all external inputs.
- Separate application configuration from secrets.
- Do not silently ignore failures.
- Use bounded retries.
- Avoid unnecessary LLM calls.
- Preserve a complete audit trail.
- Make every workflow resumable.
- Never report an action as successful unless its result has been verified.

## 16. Development Instructions

You are working inside the current project workspace.

First:

1. Inspect the current directory and existing files.
2. Check available development tools.
3. Establish the repository structure.
4. Create `IMPLEMENTATION_PLAN.md` with milestones and acceptance criteria.
5. Create `PROGRESS.md` to track implementation progress and blockers.
6. Begin implementation immediately.

Implement the application in vertical slices so each milestone produces working, testable functionality.

Development sequence:

**Milestone 1:** Backend foundation, database, migrations, Docker Compose, and health checks.

**Milestone 2:** Agent runtime, shared LLM gateway, role prompts, and persisted conversations.

**Milestone 3:** Product Manager/Architect collaboration workflow and approval system.

**Milestone 4:** React frontend with project creation, discussion viewer, specifications, and approvals.

**Milestone 5:** Development planning, task scheduler, isolated workspaces, and OpenCode adapter.

**Milestone 6:** QA, code review, correction loops, GitHub integration, and delivery.

**Milestone 7:** Full integration testing, observability, security hardening, and documentation.

At each milestone:

- Implement code.
- Run relevant tests.
- Fix discovered failures.
- Update `PROGRESS.md`.
- Record decisions in `DECISIONS.md`.
- Commit completed work locally when Git is configured.
- Continue to the next milestone.

Do not stop simply because one milestone is complete.

Do not claim that an untested component is working.

If an external dependency is unavailable, implement a testable adapter or mock, document the limitation, and continue with independent work.

Ask the user only when a missing decision materially blocks implementation or when an action requires explicit approval.

## 17. Definition of Done

The project is complete when:

1. Docker Compose starts the core application successfully.
2. The user can submit a software project idea through the UI.
3. Product Manager and Architect collaborate using the configured LLM.
4. Discussions and artifacts persist across restarts.
5. The user can review, edit, and approve specifications.
6. Approved projects generate actionable development tasks.
7. Development tasks execute in isolated environments through OpenCode.
8. Automated tests and code reviews produce visible results.
9. Failed tasks can be retried or resumed.
10. Successful projects produce deliverable code and a GitHub PR when credentials are configured.
11. The frontend displays workflow status and agent activity.
12. Automated tests pass.
13. Installation and operating instructions are documented.

## Final Instructions

**Build the actual CodingFactory application.**

Do not merely describe the implementation.

Do not stop after creating a plan.

Do not generate placeholder services that pretend to execute agents.

Prioritize functional, tested implementations over superficial feature completeness.

Use the existing workspace.

Proceed autonomously, using the configured model and available development tools.

Start now.
