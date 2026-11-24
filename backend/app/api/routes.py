"""REST endpoints."""

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.gateway import LLMGateway
from ..models import (
    AgentMessage,
    Approval,
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    Project,
    ProjectState,
    Task,
    WorkflowEvent,
)
from ..services.discovery import add_message
from ..services.handoff import HANDOFF_SLUG, current_content, spec_snapshot
from .deps import bus, runner
from .schemas import (
    AnswersIn,
    ApprovalIn,
    ArtifactEditIn,
    ArtifactOut,
    ArtifactVersionOut,
    EventOut,
    FeedbackIn,
    MessageOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    TaskOut,
)

router = APIRouter(prefix="/api")


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.sessions()
    try:
        yield session
    finally:
        session.close()


def _get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@router.get("/health/llm")
async def llm_health(request: Request):
    gateway = request.app.state.llm
    if isinstance(gateway, LLMGateway):
        return await gateway.test_connection()
    return {"ok": True, "note": "mock LLM in use"}


# ---------- projects ----------


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate, request: Request, session: Session = Depends(get_db)):
    name = payload.name or _derive_name(payload.idea)
    project = Project(name=name, idea=payload.idea.strip(), state=ProjectState.IDEA)
    session.add(project)
    session.flush()  # assigns UUID before referencing project.id
    bus(request).record(session, project.id, "project_created", {"name": name})
    session.commit()
    runner(request).ensure_discovery(project.id)
    return project


def _derive_name(idea: str) -> str:
    words = idea.strip().split()
    return " ".join(words[:8])[:200] or "New project"


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_db)):
    return session.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, session: Session = Depends(get_db)):
    project = _get_project(session, project_id)
    task_count = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id)
    ).scalar_one()
    return ProjectDetail(
        **{c: getattr(project, c) for c in ("id", "name", "idea", "state", "discovery_round", "error", "created_at")},
        task_count=task_count,
        artifacts=[a.slug for a in project.artifacts],
    )


@router.post("/projects/{project_id}/cancel", response_model=ProjectOut)
async def cancel_project(project_id: str, request: Request, session: Session = Depends(get_db)):
    project = _get_project(session, project_id)
    if project.state in (ProjectState.COMPLETED, ProjectState.CANCELLED):
        raise HTTPException(409, f"project is already {project.state.value}")
    # D-026: kill the live workflow task too — a pending model call returning
    # after this commit used to write the project back to AWAITING_INPUT.
    runner(request).abort(project.id)
    project.state = ProjectState.CANCELLED
    bus(request).record(session, project.id, "cancelled", {})
    session.commit()
    return project


@router.post("/projects/{project_id}/resume", response_model=ProjectOut)
async def resume_project(project_id: str, request: Request, session: Session = Depends(get_db)):
    """Retry a FAILED project from persisted state. If the current iteration's
    specs were approved, re-run planning + the handoff (planning is idempotent
    per iteration); otherwise the failure happened in discovery, or the specs
    were rejected, so re-run discovery and go through the approval gate again."""
    project = _get_project(session, project_id)
    if project.state in (
        ProjectState.COMPLETED,
        ProjectState.CANCELLED,
        ProjectState.AWAITING_INPUT,
        ProjectState.AWAITING_APPROVAL,
    ):
        raise HTTPException(409, f"nothing to resume (state={project.state.value})")
    last_decision = session.execute(
        select(Approval.decision)
        .where(Approval.project_id == project.id, Approval.iteration == project.iteration)
        .order_by(Approval.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last_decision == ApprovalDecision.APPROVED:
        project.state = ProjectState.PLANNING
        project.error = None
        bus(request).record(session, project.id, "resumed", {"kind": "plan"})
        session.commit()
        runner(request).ensure_plan(project.id)
    else:
        project.state = ProjectState.DISCOVERY
        project.error = None
        bus(request).record(session, project.id, "resumed", {"kind": "discovery"})
        session.commit()
        runner(request).ensure_discovery(project.id)
    return project


@router.post("/projects/{project_id}/restart", response_model=ProjectOut)
async def restart_project(project_id: str, request: Request, session: Session = Depends(get_db)):
    """Start the project over from its original idea: wipe everything derived
    (discussion, artifacts, task plan, approvals, event history), reset to the
    fresh-creation shape, and re-run discovery. Allowed from any state; a live
    workflow task is cancelled first so it can't race the wipe."""
    project = _get_project(session, project_id)
    runner(request).abort(project.id)
    for artifact in list(project.artifacts):  # versions cascade via relationship
        session.delete(artifact)
    for msg in list(project.messages):
        session.delete(msg)
    for task in list(project.tasks):
        session.delete(task)
    for approval in list(project.approvals):
        session.delete(approval)
    # Event ids are SQLite rowids (no AUTOINCREMENT): deleting this project's
    # newest rows would let the next ids reuse them, behind any after_id cursor.
    # Pin the restart marker just past the old maximum so ids stay monotonic.
    next_event_id = (session.execute(select(func.max(WorkflowEvent.id))).scalar() or 0) + 1
    for event in list(project.events):  # clean slate; the marker below starts the new history
        session.delete(event)
    session.flush()
    project.state = ProjectState.DISCOVERY
    project.iteration = 0
    project.discovery_round = 0
    project.user_facing = True  # re-derived by the next PM turn
    project.error = None
    bus(request).record(session, project.id, "project_restarted", {}, event_id=next_event_id)
    session.commit()
    runner(request).ensure_discovery(project.id)
    return project


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request, session: Session = Depends(get_db)):
    """Permanently remove a project and everything under it (cascade)."""
    project = _get_project(session, project_id)
    runner(request).abort(project.id)
    session.delete(project)  # ORM cascades: messages, artifacts+versions, tasks, approvals, events
    session.commit()
    return None


# ---------- discovery / Q&A ----------


@router.get("/projects/{project_id}/messages", response_model=list[MessageOut])
def list_messages(project_id: str, session: Session = Depends(get_db)):
    return (
        session.execute(
            select(AgentMessage).where(AgentMessage.project_id == project_id).order_by(AgentMessage.seq)
        )
        .scalars()
        .all()
    )


@router.post("/projects/{project_id}/answers", response_model=ProjectOut)
async def answer_questions(
    project_id: str, payload: AnswersIn, request: Request, session: Session = Depends(get_db)
):
    project = _get_project(session, project_id)
    if project.state != ProjectState.AWAITING_INPUT:
        raise HTTPException(409, f"project is not awaiting input (state={project.state.value})")
    for a in payload.answers:
        text = a.answer.strip()
        if a.question:
            text = f"Q: {a.question.strip()}\nA: {text}"
        if a.question_id:
            q = session.get(AgentMessage, a.question_id)
            if q is not None and q.project_id == project.id:
                q.answered = True
        next_seq = (
            session.execute(
                select(func.max(AgentMessage.seq)).where(AgentMessage.project_id == project.id)
            ).scalar()
            or 0
        )
        session.add(
            AgentMessage(
                project_id=project.id, agent="user", kind="answer", content=text,
                round=project.discovery_round, seq=next_seq + 1, iteration=project.iteration,
            )
        )
    project.discovery_round += 1
    project.state = ProjectState.DISCOVERY
    bus(request).record(session, project.id, "user_answered", {"answers": len(payload.answers)})
    session.commit()
    runner(request).ensure_discovery(project.id)
    return project


# ---------- user feedback (change requests after delivery) ----------


@router.post("/projects/{project_id}/feedback", response_model=ProjectOut)
async def submit_feedback(
    project_id: str, payload: FeedbackIn, request: Request, session: Session = Depends(get_db)
):
    """Change request on a delivered project: start a delta discovery cycle
    (revised specs -> approval gate -> regenerated handoff prompt)."""
    project = _get_project(session, project_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "feedback content is empty")
    if project.state != ProjectState.COMPLETED:
        raise HTTPException(409, f"change requests only accepted when COMPLETED (state={project.state.value})")
    project.iteration += 1
    add_message(
        session, project.id, "user", content, kind="change_request",
        round_=project.discovery_round, iteration_=project.iteration,
    )
    project.discovery_round += 1
    project.state = ProjectState.DISCOVERY
    bus(request).record(session, project.id, "change_requested", {"feedback": content[:500]})
    session.commit()
    runner(request).ensure_discovery(project.id)
    return project


# ---------- artifacts ----------


def _artifact_or_404(session: Session, project_id: str, slug: str) -> Artifact:
    artifact = session.execute(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.slug == slug)
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(404, f"artifact {slug} not found")
    return artifact


def _current_content(session: Session, artifact: Artifact) -> str:
    version = session.execute(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.version == artifact.current_version
        )
    ).scalar_one_or_none()
    return version.content if version else ""


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(project_id: str, session: Session = Depends(get_db)):
    out = []
    for a in session.execute(
        select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.slug)
    ).scalars():
        out.append(
            ArtifactOut(
                slug=a.slug, title=a.title, current_version=a.current_version,
                content=_current_content(session, a), updated_at=a.created_at,
            )
        )
    return out


@router.get("/projects/{project_id}/artifacts/{slug}", response_model=list[ArtifactVersionOut])
def artifact_history(project_id: str, slug: str, session: Session = Depends(get_db)):
    artifact = _artifact_or_404(session, project_id, slug)
    return (
        session.execute(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .order_by(ArtifactVersion.version.desc())
        )
        .scalars()
        .all()
    )


@router.put("/projects/{project_id}/artifacts/{slug}", response_model=ArtifactOut)
def edit_artifact(
    project_id: str, slug: str, payload: ArtifactEditIn, request: Request, session: Session = Depends(get_db)
):
    artifact = _artifact_or_404(session, project_id, slug)
    artifact.current_version += 1
    session.add(
        ArtifactVersion(
            artifact_id=artifact.id, version=artifact.current_version, content=payload.content, created_by="user"
        )
    )
    bus(request).record(session, project_id, "artifact_edited", {"slug": slug, "version": artifact.current_version})
    session.commit()
    return ArtifactOut(
        slug=artifact.slug, title=artifact.title, current_version=artifact.current_version,
        content=payload.content, updated_at=artifact.created_at,
    )


# ---------- approval ----------


@router.post("/projects/{project_id}/approval", response_model=ProjectOut)
async def approve_project(
    project_id: str, payload: ApprovalIn, request: Request, session: Session = Depends(get_db)
):
    project = _get_project(session, project_id)
    if project.state != ProjectState.AWAITING_APPROVAL:
        raise HTTPException(409, f"approval only allowed at AWAITING_APPROVAL (state={project.state.value})")
    # D-023: an approval is a freeze of the exact artifact versions on screen at
    # this moment. The planner, the handoff generator, and every retry read this
    # snapshot — an edit landing while planning runs can no longer leak into
    # the build prompt next to a plan built from the earlier text.
    snapshot = spec_snapshot(project) if payload.decision == ApprovalDecision.APPROVED else None
    session.add(
        Approval(
            project_id=project.id, decision=payload.decision, comment=payload.comment,
            iteration=project.iteration, artifact_versions=snapshot,
        )
    )
    if payload.decision == ApprovalDecision.APPROVED:
        project.state = ProjectState.PLANNING
        bus(request).record(session, project.id, "approved", {})
        session.commit()
        runner(request).ensure_plan(project.id)
    elif payload.decision == ApprovalDecision.REJECTED:
        project.state = ProjectState.FAILED
        project.error = "rejected by user"
        bus(request).record(session, project.id, "rejected", {"comment": payload.comment or ""})
        session.commit()
    else:  # REVISION_REQUESTED
        if not payload.comment:
            raise HTTPException(422, "revision requests need a comment")
        next_seq = (
            session.execute(
                select(func.max(AgentMessage.seq)).where(AgentMessage.project_id == project.id)
            ).scalar()
            or 0
        )
        session.add(
            AgentMessage(
                project_id=project.id, agent="user", kind="message",
                content=f"REVISION REQUEST: {payload.comment}", round=project.discovery_round,
                seq=next_seq + 1, iteration=project.iteration,
            )
        )
        project.state = ProjectState.DISCOVERY
        project.discovery_round += 1
        bus(request).record(session, project.id, "revision_requested", {"comment": payload.comment})
        session.commit()
        runner(request).ensure_discovery(project.id)
    return project


# ---------- tasks / handoff / events ----------


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(project_id: str, session: Session = Depends(get_db)):
    return session.execute(
        select(Task).where(Task.project_id == project_id).order_by(Task.number)
    ).scalars().all()


@router.get("/projects/{project_id}/handoff", response_class=PlainTextResponse)
def get_handoff(project_id: str, request: Request, session: Session = Depends(get_db)):
    """The generated build prompt, served as a downloadable text file."""
    artifact = session.execute(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.slug == HANDOFF_SLUG)
    ).scalar_one_or_none()
    content = current_content(session, artifact) if artifact is not None else None
    if content is None:
        raise HTTPException(404, "handoff prompt not generated yet")
    filename = f"{project_id}-handoff-prompt.md"
    return PlainTextResponse(
        content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/events", response_model=list[EventOut])
def list_events(project_id: str, session: Session = Depends(get_db), after_id: int = 0, limit: int = 200):
    return session.execute(
        select(WorkflowEvent)
        .where(WorkflowEvent.project_id == project_id, WorkflowEvent.id > after_id)
        .order_by(WorkflowEvent.id)
        .limit(min(limit, 1000))
    ).scalars().all()
