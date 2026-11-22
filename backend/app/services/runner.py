"""Background workflow dispatch: one live task per project, idempotent.

On startup, active projects are resumed from persisted state; nothing is
re-run blindly.
"""

import asyncio
import logging

from sqlalchemy import select

from ..llm.gateway import LLMError
from ..models import Project, ProjectState
from .build import BuildContext, run_build
from .discovery import DiscoveryContext, advance_discovery
from .planning import plan_project

logger = logging.getLogger("cf.runner")

ACTIVE_STATES = {
    ProjectState.DISCOVERY: "discovery",
    ProjectState.PLANNING: "build",
    ProjectState.BUILDING: "build",
}


class Runner:
    def __init__(self, discovery: DiscoveryContext, build: BuildContext):
        self.discovery = discovery
        self.build = build
        self._live: dict[str, asyncio.Task] = {}

    def ensure_discovery(self, project_id: str) -> None:
        self._ensure(f"discovery:{project_id}", lambda: self._discovery(project_id))

    def ensure_build(self, project_id: str) -> None:
        self._ensure(f"build:{project_id}", lambda: self._build(project_id))

    def _ensure(self, key: str, factory) -> None:
        existing = self._live.get(key)
        if existing is not None and not existing.done():
            return  # idempotent: already running
        task = asyncio.create_task(self._guard(key, factory()))
        self._live[key] = task
        task.add_done_callback(lambda _t: self._live.pop(key, None))

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

    async def _build(self, project_id: str) -> None:
        with self.discovery.sessions() as session:
            project = session.get(Project, project_id)
            if project is None or project.state == ProjectState.CANCELLED:
                return
            if project.state == ProjectState.PLANNING:
                await plan_project(
                    self.build.sessions, self.build.llm, self.build.bus, self.build.settings, project_id
                )
        outcome = await run_build(self.build, project_id)
        if outcome == "steering":
            # A queued REPLAN moved the project back to DISCOVERY; run the
            # delta cycle now. It ends at AWAITING_INPUT/AWAITING_APPROVAL,
            # and those endpoints re-dispatch the build.
            await advance_discovery(self.discovery, project_id)

    async def resume(self) -> None:
        """Re-dispatch projects that were mid-workflow when the process died."""
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
                self.ensure_build(project_id)

    async def shutdown(self) -> None:
        for task in list(self._live.values()):
            task.cancel()
        await asyncio.gather(*self._live.values(), return_exceptions=True)
