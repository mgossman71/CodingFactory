"""Development Manager: approved artifacts -> validated task plan."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..agents.prompts import DEV_MANAGER_SYSTEM
from ..agents.schemas import PlannedTask, TaskPlan
from ..config import Settings
from ..llm.gateway import LLMClient
from ..llm.structured import chat_json
from ..models import Project, Task
from .events import EventBus
from .handoff import approved_specs

logger = logging.getLogger("cf.planning")

# D-020: for a user-facing product's first plan, a plan that only names plumbing
# tasks is rejected once and the Dev Manager is re-asked with explicit feedback.
_COVERAGE_RETRIES = 1
_DESIGN_CATEGORIES = {"design", "polish"}


class PlanValidationError(Exception):
    pass


class PlanCoverageError(PlanValidationError):
    """Missing required task categories (distinct from structural errors, which
    are never retried — only coverage misses get a re-ask)."""


def _missing_categories(tasks: list[PlannedTask]) -> list[str]:
    cats = {t.category for t in tasks}
    missing = []
    if not (cats & _DESIGN_CATEGORIES):
        missing.append("design/polish")
    if "content" not in cats:
        missing.append("content")
    return missing


def validate_plan(
    plan: TaskPlan,
    max_tasks: int,
    preexisting: set[int] = frozenset(),
    require_categories: bool = False,
) -> list[PlannedTask]:
    tasks = plan.tasks[:max_tasks]
    numbers = [t.number for t in tasks]
    if len(set(numbers)) != len(numbers):
        raise PlanValidationError("duplicate task numbers in plan")
    reused = [n for n in numbers if n in preexisting]
    if reused:
        raise PlanValidationError(f"plan re-plans existing tasks {reused}")
    known = set(numbers) | set(preexisting)
    for t in tasks:
        bad = [d for d in t.dependencies if d not in known]
        if bad:
            raise PlanValidationError(f"task {t.number} depends on unknown tasks {bad}")
        if t.number in t.dependencies:
            raise PlanValidationError(f"task {t.number} depends on itself")
    # Cycle detection (Kahn). Dependencies on preexisting tasks are already
    # satisfiable (they exist outside the plan) and cannot close a cycle.
    local = {t.number: {d for d in t.dependencies if d not in preexisting} for t in tasks}
    indeg = {n: len(deps) for n, deps in local.items()}
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m, deps in local.items():
            if n in deps:
                deps.discard(n)
                if not deps:
                    queue.append(m)
    if seen != len(tasks):
        raise PlanValidationError("task plan contains a dependency cycle")
    if require_categories:
        missing = _missing_categories(tasks)
        if missing:
            raise PlanCoverageError(
                f"plan is missing required task categories: {', '.join(missing)} "
                "(a user-facing product needs at least one design/polish task and one content task, "
                "each with acceptance criteria)"
            )
    return tasks


async def plan_project(
    sessions: sessionmaker[Session], llm: LLMClient, bus: EventBus, settings: Settings, project_id: str
) -> list[Task]:
    with sessions() as session:
        project = session.get(Project, project_id)
        if project is None:
            return []
        all_tasks = session.execute(
            select(Task).where(Task.project_id == project.id).order_by(Task.number)
        ).scalars().all()
        # Idempotent per iteration: if this iteration was already planned (e.g. a
        # failure after the tasks were committed, then Resume), keep that plan
        # rather than asking for a second, duplicate set of "delta" tasks.
        if any(t.iteration == project.iteration for t in all_tasks):
            return list(all_tasks)
        existing = [t for t in all_tasks if t.iteration < project.iteration]
        spec_text = "\n\n".join(
            f"## {slug}\n\n{content[:8000]}" for slug, content in approved_specs(session, project)
        )

        # Category coverage applies to a user-facing product's first, whole plan —
        # never to delta replans (iteration > 0), which only carry the change.
        require_categories = project.user_facing and project.iteration == 0 and not existing

        prompt = f"APPROVED SPECIFICATION:\n\n{spec_text}"
        if existing:
            done = "\n".join(f"- Task {t.number}: {t.title}" for t in existing)
            next_num = max(t.number for t in existing) + 1
            prompt += (
                f"\n\nALREADY PLANNED TASKS (delivered or scheduled — never re-plan these numbers):\n{done}\n\n"
                f"This is an ITERATION: the specification has changed. Emit ONLY NEW tasks that "
                f"implement what is new or changed, numbered from {next_num} upward. New tasks may "
                f"depend on already-planned task numbers."
            )

        messages = [
            {
                "role": "system",
                "content": DEV_MANAGER_SYSTEM.format(max_tasks=settings.max_tasks),
            },
            {"role": "user", "content": prompt},
        ]
        for attempt in range(_COVERAGE_RETRIES + 1):
            plan = await chat_json(
                llm,
                messages,
                TaskPlan,
                repair_attempts=settings.max_agent_output_repair_attempts,
            )
            try:
                tasks = validate_plan(
                    plan, settings.max_tasks, preexisting={t.number for t in existing},
                    require_categories=require_categories,
                )
                break
            except PlanCoverageError as exc:
                if attempt == _COVERAGE_RETRIES:
                    raise
                logger.warning("plan rejected on category coverage (attempt %d): %s", attempt + 1, exc)
                bus.record(session, project.id, "plan_coverage_rejected", {"error": str(exc)[:500]})
                session.commit()
                messages = messages + [
                    {"role": "assistant", "content": plan.model_dump_json()},
                    {
                        "role": "user",
                        "content": (
                            f"That plan was REJECTED: {exc}\n"
                            "Re-emit the full plan with the missing categories present, keeping every "
                            "existing task and adding real design/polish and content tasks."
                        ),
                    },
                ]

        for t in tasks:
            session.add(
                Task(
                    project_id=project.id,
                    number=t.number,
                    iteration=project.iteration,
                    title=t.title[:300],
                    description=t.description,
                    acceptance_criteria=t.acceptance_criteria,
                    category=t.category,
                    dependencies=sorted(set(t.dependencies)),
                )
            )
        bus.record(session, project.id, "plan_created", {"tasks": len(tasks), "iteration": project.iteration})
        session.commit()
        return list(
            session.execute(
                select(Task).where(Task.project_id == project.id).order_by(Task.number)
            ).scalars()
        )

