"""API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from ..models import ApprovalDecision, ProjectState, TaskStatus


class ProjectCreate(BaseModel):
    idea: str = Field(min_length=10, max_length=20000)
    name: str | None = Field(default=None, max_length=200)


class ProjectOut(BaseModel):
    id: str
    name: str
    idea: str
    state: ProjectState
    discovery_round: int
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetail(ProjectOut):
    task_counts: dict[str, int]
    artifacts: list[str]


class MessageOut(BaseModel):
    id: str
    agent: str
    kind: str
    content: str
    round: int
    seq: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AnswerIn(BaseModel):
    question_id: str | None = None
    question: str = Field(default="", max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)


class AnswersIn(BaseModel):
    answers: list[AnswerIn] = Field(min_length=1, max_length=20)


class ArtifactOut(BaseModel):
    slug: str
    title: str
    current_version: int
    content: str
    updated_at: datetime


class ArtifactVersionOut(BaseModel):
    version: int
    created_by: str
    created_at: datetime
    content: str


class ArtifactEditIn(BaseModel):
    content: str = Field(min_length=1)


class ApprovalIn(BaseModel):
    decision: ApprovalDecision
    comment: str | None = Field(default=None, max_length=8000)


class TaskAttemptOut(BaseModel):
    id: str
    attempt_number: int
    status: str
    exit_code: int | None
    result_summary: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: str
    number: int
    title: str
    description: str
    acceptance_criteria: str
    status: TaskStatus
    attempts: int
    max_attempts: int
    dependencies: list[int]
    attempts_log: list[TaskAttemptOut]

    model_config = {"from_attributes": True}


class LogOut(BaseModel):
    id: str
    kind: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: int
    type: str
    payload: str
    created_at: datetime

    model_config = {"from_attributes": True}
