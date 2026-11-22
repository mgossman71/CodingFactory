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


def validate_plan(plan: TaskPlan, max_tasks: int) -> list[PlannedTask]:
    tasks = plan.tasks[:max_tasks]
    numbers = [t.number for t in tasks]
    if len(set(numbers)) != len(numbers):
        raise PlanValidationError("duplicate task numbers in plan")
    known = set(numbers)
    for t in tasks:
        bad = [d for d in t.dependencies if d not in known]
        if bad:
            raise PlanValidationError(f"task {t.number} depends on unknown tasks {bad}")
        if t.number in t.dependencies:
            raise PlanValidationError(f"task {t.number} depends on itself")
    # Cycle detection (Kahn).
    indeg = {t.number: len(set(t.dependencies)) for t in tasks}
    deps_by = {t.number: set(t.dependencies) for t in tasks}
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for t in tasks:
            if n in deps_by[t.number]:
                deps_by[t.number].discard(n)
                if not deps_by[t.number]:
                    queue.append(t.number)
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

        plan = await chat_json(
            llm,
            [
                {
                    "role": "system",
                    "content": DEV_MANAGER_SYSTEM.format(max_tasks=settings.max_tasks),
                },
                {"role": "user", "content": f"APPROVED SPECIFICATION:\n\n{spec_text}"},
            ],
            TaskPlan,
            repair_attempts=settings.max_agent_output_repair_attempts,
        )
        tasks = validate_plan(plan, settings.max_tasks)

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
        bus.record(session, project.id, "plan_created", {"tasks": len(tasks)})
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
