"""API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from ..models import ApprovalDecision, ProjectState


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
    task_count: int
    artifacts: list[str]


class MessageOut(BaseModel):
    id: str
    agent: str
    kind: str
    content: str
    round: int
    seq: int
    answered: bool
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


class FeedbackIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class TaskOut(BaseModel):
    id: str
    number: int
    title: str
    description: str
    acceptance_criteria: str
    category: str
    dependencies: list[int]
    iteration: int  # change-request cycle that planned it (0 = the original plan)
    supersedes: list[int]  # earlier task numbers this task's change replaces (D-024)

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: int
    type: str
    payload: str
    created_at: datetime

    model_config = {"from_attributes": True}
