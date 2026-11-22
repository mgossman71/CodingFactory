"""Development Manager: approved artifacts -> validated task plan."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..agents.prompts import DEV_MANAGER_SYSTEM
from ..agents.schemas import PlannedTask, TaskPlan
from ..config import Settings
from ..llm.gateway import LLMClient
from ..llm.structured import chat_json
from ..models import Artifact, ArtifactVersion, Project, Task, TaskStatus
from .events import EventBus

logger = logging.getLogger("cf.planning")


class PlanValidationError(Exception):
    pass


def validate_plan(plan: TaskPlan, max_tasks: int, preexisting: set[int] = frozenset()) -> list[PlannedTask]:
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
    return tasks


async def plan_project(
    sessions: sessionmaker[Session], llm: LLMClient, bus: EventBus, settings: Settings, project_id: str
) -> list[Task]:
    with sessions() as session:
        project = session.get(Project, project_id)
        if project is None:
            return []
        spec_text = _approved_spec(session, project)
        existing = session.execute(
            select(Task).where(Task.project_id == project.id).order_by(Task.number)
        ).scalars().all()

        prompt = f"APPROVED SPECIFICATION:\n\n{spec_text}"
        if existing:
            done = "\n".join(f"- Task {t.number}: {t.title} [{t.status.value}]" for t in existing)
            next_num = max(t.number for t in existing) + 1
            prompt += (
                f"\n\nALREADY PLANNED TASKS (delivered or scheduled — never re-plan these numbers):\n{done}\n\n"
                f"This is an ITERATION: the specification has changed. Emit ONLY NEW tasks that "
                f"implement what is new or changed, numbered from {next_num} upward. New tasks may "
                f"depend on already-planned task numbers."
            )

        plan = await chat_json(
            llm,
            [
                {
                    "role": "system",
                    "content": DEV_MANAGER_SYSTEM.format(max_tasks=settings.max_tasks),
                },
                {"role": "user", "content": prompt},
            ],
            TaskPlan,
            repair_attempts=settings.max_agent_output_repair_attempts,
        )
        tasks = validate_plan(plan, settings.max_tasks, preexisting={t.number for t in existing})

        for t in tasks:
            session.add(
                Task(
                    project_id=project.id,
                    number=t.number,
                    title=t.title[:300],
                    description=t.description,
                    acceptance_criteria=t.acceptance_criteria,
                    dependencies=sorted(set(t.dependencies)),
                    status=TaskStatus.BACKLOG,
                    max_attempts=settings.max_task_attempts,
                )
            )
        _promote_ready(session, project.id)
        bus.record(session, project.id, "plan_created", {"tasks": len(tasks), "iteration": project.iteration})
        session.commit()
        return list(
            session.execute(
                select(Task).where(Task.project_id == project.id).order_by(Task.number)
            ).scalars()
        )


def _approved_spec(session: Session, project: Project) -> str:
    parts: list[str] = []
    for artifact in project.artifacts:
        version = session.execute(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.version == artifact.current_version)
        ).scalar_one_or_none()
        if version is not None:
            parts.append(f"## {artifact.slug}\n\n{version.content[:8000]}")
    return "\n\n".join(parts)


def _promote_ready(session: Session, project_id: str) -> None:
    """BACKLOG tasks whose dependencies are all COMPLETED become READY."""
    tasks = session.execute(select(Task).where(Task.project_id == project_id)).scalars().all()
    done = {t.number for t in tasks if t.status == TaskStatus.COMPLETED}
    for t in tasks:
        if t.status == TaskStatus.BACKLOG and all(d in done for d in t.dependencies):
            t.status = TaskStatus.READY
