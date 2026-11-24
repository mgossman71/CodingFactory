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
    iteration: int  # change-request cycle (0 = first pass); scopes the question gate + UI answer form
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
    iteration: int
    # D-028: structured question detail (why_it_matters, options,
    # recommended_option); None on older questions -> plain text in the UI.
    meta: dict | None
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
    # D-029: the artifact version map (slug -> version) the UI actually
    # reviewed. Optional for existing API callers; when supplied it is compared
    # with the live spec versions in the same transaction that records the
    # approval, and a mismatch returns 409 instead of approving unseen edits.
    artifact_versions: dict[str, int] | None = None
    # D-034: the immutable artifact-version row ids behind that map
    # (slug -> ArtifactVersion.id). Slug+version numbers collide across
    # "Start over" (artifacts are deleted and recreated); ids do not. When
    # supplied, a map from before a restart is rejected even when the version
    # numbers match.
    artifact_version_ids: dict[str, str] | None = None


class ReviewCriterionOut(BaseModel):
    id: str
    criterion: str


class ReviewSourceOut(BaseModel):
    slug: str
    title: str
    version: int
    version_id: str  # immutable ArtifactVersion row id (D-034)


class ReviewOut(BaseModel):
    """The compact approval review (services/review.py), built from the same
    versioned artifacts the coding agent will receive. A section is None when
    the document has no such heading at all, [] when it exists but lists
    nothing (D-030); decision_history holds superseded pre-change-request
    answers (D-031)."""

    in_scope: list[str] | None
    out_of_scope: list[str] | None
    assumptions: list[str] | None
    decisions: list[str] | None
    decision_history: list[str] | None
    interpretations: list[str] | None
    unresolved: list[str] | None
    acceptance_criteria: list[ReviewCriterionOut]
    sources: list[ReviewSourceOut]


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
