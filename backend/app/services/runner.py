"""Background workflow dispatch: one live task per project, idempotent.

On startup, active projects are resumed from persisted state and COMPLETED
projects that have a plan but no prompt get one; nothing is re-run blindly. A
PLANNING project runs the Dev Manager plan and then assembles the handoff
prompt (CodingFactory no longer builds the software itself).
"""

import asyncio
import logging

from sqlalchemy import select

from ..models import Project, ProjectState, Task
from .discovery import DiscoveryContext, advance_discovery
from .handoff import HANDOFF_SLUG, write_handoff
from .planning import plan_project

logger = logging.getLogger("cf.runner")

ACTIVE_STATES = {
    ProjectState.DISCOVERY: "discovery",
    ProjectState.PLANNING: "plan",
}


class Runner:
    def __init__(self, discovery: DiscoveryContext):
        self.discovery = discovery
        self._live: dict[str, asyncio.Task] = {}

    def ensure_discovery(self, project_id: str) -> None:
        self._ensure(f"discovery:{project_id}", lambda: self._discovery(project_id))

    def ensure_plan(self, project_id: str) -> None:
        self._ensure(f"plan:{project_id}", lambda: self._plan(project_id))

    def abort(self, project_id: str) -> None:
        """Cancel any live workflow task for a project being restarted or
        deleted. _guard re-raises CancelledError without marking the project
        FAILED, so no spurious failure events."""
        for key in (f"discovery:{project_id}", f"plan:{project_id}"):
            task = self._live.pop(key, None)
            if task is not None:
                task.cancel()

    def _ensure(self, key: str, factory) -> None:
        existing = self._live.get(key)
        if existing is not None and not existing.done():
            return  # idempotent: already running
        task = asyncio.create_task(self._guard(key, factory()))
        self._live[key] = task
        task.add_done_callback(lambda t: self._forget(key, t))

    def _forget(self, key: str, task: asyncio.Task) -> None:
        # After abort() + an immediate re-ensure, the cancelled task's callback
        # fires late; it must not evict the replacement registered under its key.
        if self._live.get(key) is task:
            del self._live[key]

    async def _guard(self, key: str, coro) -> None:
        project_id = key.split(":", 1)[1]
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("workflow %s failed", key)
            self._fail(project_id, exc)

    def _fail(self, project_id: str, exc: Exception) -> None:
        with self.discovery.sessions() as session:
            project = session.get(Project, project_id)
            if project is None or project.state == ProjectState.CANCELLED:
                return
            project.state = ProjectState.FAILED
            project.error = f"{exc.__class__.__name__}: {exc}"
            self.discovery.bus.record(
                session,
                project_id,
                "workflow_failed",
                {"error": str(exc)[:500], "kind": exc.__class__.__name__},
            )
            session.commit()

    async def _discovery(self, project_id: str) -> None:
        await advance_discovery(self.discovery, project_id)

    async def _plan(self, project_id: str) -> None:
        ctx = self.discovery
        await plan_project(ctx.sessions, ctx.llm, ctx.bus, ctx.settings, project_id)
        with ctx.sessions() as session:
            project = session.get(Project, project_id)
            if project is None or project.state == ProjectState.CANCELLED:
                return
            write_handoff(session, project)
            project.state = ProjectState.COMPLETED
            project.error = None
            ctx.bus.record(session, project_id, "handoff_ready", {"iteration": project.iteration})
            session.commit()

    def _backfill_handoffs(self) -> None:
        """Write the handoff prompt for COMPLETED projects that have a task plan
        but no prompt artifact yet — pre-pivot DBs (states the build phase used)
        or a crash between planning and handoff. Deterministic: no LLM call."""
        with self.discovery.sessions() as session:
            completed = session.execute(
                select(Project).where(Project.state == ProjectState.COMPLETED)
            ).scalars().all()
            for project in completed:
                if any(a.slug == HANDOFF_SLUG for a in project.artifacts):
                    continue
                has_tasks = (
                    session.execute(
                        select(Task.id).where(Task.project_id == project.id).limit(1)
                    ).scalar_one_or_none()
                    is not None
                )
                if not has_tasks:
                    continue
                write_handoff(session, project)
                self.discovery.bus.record(session, project.id, "handoff_ready", {"iteration": project.iteration})
                session.commit()
                logger.info("generated missing handoff prompt for project %s", project.id)

    async def resume(self) -> None:
        """Re-dispatch projects that were mid-workflow when the process died."""
        self._backfill_handoffs()
        with self.discovery.sessions() as session:
            active = session.execute(
                select(Project).where(Project.state.in_(list(ACTIVE_STATES)))
            ).scalars().all()
            ids = [(p.id, ACTIVE_STATES[p.state]) for p in active]
        for project_id, kind in ids:
            logger.info("resuming %s for project %s", kind, project_id)
            if kind == "discovery":
                self.ensure_discovery(project_id)
            else:
                self.ensure_plan(project_id)

    async def shutdown(self) -> None:
        for task in list(self._live.values()):
            task.cancel()
        await asyncio.gather(*self._live.values(), return_exceptions=True)
