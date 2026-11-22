"""REST endpoints."""

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.gateway import LLMGateway
from ..models import (
    AgentMessage,
    Approval,
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ExecutionLog,
    Project,
    ProjectState,
    Task,
    TaskAttempt,
    TaskStatus,
    WorkflowEvent,
)
from .deps import bus, discovery_ctx, runner
from .schemas import (
    AnswersIn,
    ApprovalIn,
    ArtifactEditIn,
    ArtifactOut,
    ArtifactVersionOut,
    EventOut,
    LogOut,
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
    counts = dict(
        session.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.project_id == project.id)
            .group_by(Task.status)
        ).all()
    )
    return ProjectDetail(
        **{c: getattr(project, c) for c in ("id", "name", "idea", "state", "discovery_round", "error", "created_at")},
        task_counts={s.value: n for s, n in counts.items()},
        artifacts=[a.slug for a in project.artifacts],
    )


@router.post("/projects/{project_id}/cancel", response_model=ProjectOut)
def cancel_project(project_id: str, request: Request, session: Session = Depends(get_db)):
    project = _get_project(session, project_id)
    if project.state in (ProjectState.COMPLETED, ProjectState.CANCELLED):
        raise HTTPException(409, f"project is already {project.state.value}")
    project.state = ProjectState.CANCELLED
    bus(request).record(session, project.id, "cancelled", {})
    session.commit()
    return project


@router.post("/projects/{project_id}/resume", response_model=ProjectOut)
async def resume_project(project_id: str, request: Request, session: Session = Depends(get_db)):
    """Retry/resume a FAILED (or interrupted) project from persisted state."""
    project = _get_project(session, project_id)
    if project.state in (
        ProjectState.COMPLETED,
        ProjectState.CANCELLED,
        ProjectState.AWAITING_INPUT,
        ProjectState.AWAITING_APPROVAL,
    ):
        raise HTTPException(409, f"nothing to resume (state={project.state.value})")
    has_tasks = bool(
        session.execute(select(Task.id).where(Task.project_id == project.id).limit(1)).scalar_one_or_none()
    )
    if has_tasks:
        project.state = ProjectState.BUILDING
        for t in session.execute(
            select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.FAILED)
        ).scalars():
            t.status = TaskStatus.BACKLOG  # fresh attempts allowed on explicit resume
        project.error = None
        bus(request).record(session, project.id, "resumed", {"kind": "build"})
        session.commit()
        runner(request).ensure_build(project.id)
    else:
        project.state = ProjectState.DISCOVERY
        project.error = None
        bus(request).record(session, project.id, "resumed", {"kind": "discovery"})
        session.commit()
        runner(request).ensure_discovery(project.id)
    return project


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
        next_seq = (
            session.execute(
                select(func.max(AgentMessage.seq)).where(AgentMessage.project_id == project.id)
            ).scalar()
            or 0
        )
        session.add(
            AgentMessage(
                project_id=project.id, agent="user", kind="answer", content=text,
                round=project.discovery_round, seq=next_seq + 1,
            )
        )
    project.discovery_round += 1
    project.state = ProjectState.DISCOVERY
    bus(request).record(session, project.id, "user_answered", {"answers": len(payload.answers)})
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
    session.add(Approval(project_id=project.id, decision=payload.decision, comment=payload.comment))
    if payload.decision == ApprovalDecision.APPROVED:
        project.state = ProjectState.PLANNING
        bus(request).record(session, project.id, "approved", {})
        session.commit()
        runner(request).ensure_build(project.id)
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
                content=f"REVISION REQUEST: {payload.comment}", round=project.discovery_round, seq=next_seq + 1,
            )
        )
        project.state = ProjectState.DISCOVERY
        project.discovery_round += 1
        bus(request).record(session, project.id, "revision_requested", {"comment": payload.comment})
        session.commit()
        runner(request).ensure_discovery(project.id)
    return project


# ---------- tasks / logs / events ----------


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(project_id: str, session: Session = Depends(get_db)):
    return session.execute(
        select(Task).where(Task.project_id == project_id).order_by(Task.number)
    ).scalars().all()


@router.get("/projects/{project_id}/attempts/{attempt_id}/logs", response_model=list[LogOut])
def attempt_logs(project_id: str, attempt_id: str, session: Session = Depends(get_db)):
    attempt = session.get(TaskAttempt, attempt_id)
    if attempt is None or attempt.task.project_id != project_id:
        raise HTTPException(404, "attempt not found")
    return session.execute(
        select(ExecutionLog).where(ExecutionLog.attempt_id == attempt_id).order_by(ExecutionLog.id)
    ).scalars().all()


@router.get("/projects/{project_id}/events", response_model=list[EventOut])
def list_events(project_id: str, session: Session = Depends(get_db), after_id: int = 0, limit: int = 200):
    return session.execute(
        select(WorkflowEvent)
        .where(WorkflowEvent.project_id == project_id, WorkflowEvent.id > after_id)
        .order_by(WorkflowEvent.id)
        .limit(min(limit, 1000))
    ).scalars().all()
