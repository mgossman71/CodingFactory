"""Database models. UUID PKs, UTC timestamps, no plaintext secrets."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectState(str, enum.Enum):
    IDEA = "IDEA"
    DISCOVERY = "DISCOVERY"
    AWAITING_INPUT = "AWAITING_INPUT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PLANNING = "PLANNING"
    BUILDING = "BUILDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(str, enum.Enum):
    BACKLOG = "BACKLOG"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    TESTING = "TESTING"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ApprovalDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    idea: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[ProjectState] = mapped_column(
        Enum(ProjectState, native_enum=False, length=30),
        default=ProjectState.IDEA,
        nullable=False,
    )
    discovery_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="AgentMessage.seq"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Artifact.slug"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Task.number"
    )
    events: Mapped[list["WorkflowEvent"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="WorkflowEvent.id"
    )
    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Approval.created_at"
    )


class AgentMessage(TimestampMixin, Base):
    """One turn of agent (or user) conversation, persisted for replay."""

    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_messages_project_seq", "project_id", "seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[str] = mapped_column(String(60), nullable=False)  # role name or "user"
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False, default="message"
    )  # message | question | answer | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped[Project] = relationship(back_populates="messages")


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_artifact_project_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project: Mapped[Project] = relationship(back_populates="artifacts")
    versions: Mapped[list["ArtifactVersion"]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan", order_by="ArtifactVersion.version"
    )


class ArtifactVersion(TimestampMixin, Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(60), nullable=False)  # agent role or "user"

    artifact: Mapped[Artifact] = relationship(back_populates="versions")


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(ApprovalDecision, native_enum=False, length=30), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="approvals")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_task_project_number"),
        Index("ix_tasks_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=30),
        default=TaskStatus.BACKLOG,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    dependencies: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=list
    )  # task numbers that must complete first

    project: Mapped[Project] = relationship(back_populates="tasks")
    attempts_log: Mapped[list["TaskAttempt"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskAttempt.attempt_number"
    )


class TaskAttempt(TimestampMixin, Base):
    """A single execution attempt of a task (OpenCode run + test + review result)."""

    __tablename__ = "task_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_number", name="uq_attempt_task_num"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # RUNNING|PASSED|FAILED
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    task: Mapped[Task] = relationship(back_populates="attempts_log")
    logs: Mapped[list["ExecutionLog"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", order_by="ExecutionLog.id"
    )


class ExecutionLog(TimestampMixin, Base):
    """Audit trail of commands run during an attempt (secrets redacted at write)."""

    __tablename__ = "execution_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("task_attempts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # command|stdout|stderr|review|note
    content: Mapped[str] = mapped_column(Text, nullable=False)

    attempt: Mapped[TaskAttempt] = relationship(back_populates="logs")


class WorkflowEvent(TimestampMixin, Base):
    """Append-only activity feed; also replayed to WebSocket subscribers."""

    __tablename__ = "workflow_events"
    __table_args__ = (Index("ix_workflow_events_project", "project_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON

    project: Mapped[Project] = relationship(back_populates="events")
