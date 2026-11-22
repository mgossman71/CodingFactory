"""Structured output schemas for every agent role."""

from pydantic import BaseModel, Field


class Question(BaseModel):
    id: str
    question: str
    why_it_matters: str = ""


class UserStory(BaseModel):
    id: str
    title: str
    as_a: str = "user"
    i_want: str
    so_that: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    mvp: bool = True


class PMAnalysis(BaseModel):
    summary: str
    confirmed_requirements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[Question] = Field(default_factory=list)
    user_stories: list[UserStory] = Field(default_factory=list)
    mvp_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class ArchitectReview(BaseModel):
    feasibility: str = Field(pattern="^(feasible|risky|infeasible)$")
    recommended_stack: list[str] = Field(default_factory=list)
    architecture_summary: str = ""
    risks: list[str] = Field(default_factory=list)
    challenges_for_pm: list[str] = Field(default_factory=list)
    open_questions: list[Question] = Field(default_factory=list)


class DocumentOut(BaseModel):
    """Generic wrapper for markdown artifacts."""

    markdown: str


class UserStoriesOut(BaseModel):
    stories: list[UserStory]


class AcceptanceCriterion(BaseModel):
    id: str
    story_id: str = ""
    criterion: str
    testable: bool = True


class AcceptanceCriteriaOut(BaseModel):
    criteria: list[AcceptanceCriterion]


class PlannedTask(BaseModel):
    number: int
    title: str
    description: str
    acceptance_criteria: str = ""
    dependencies: list[int] = Field(default_factory=list)


class TaskPlan(BaseModel):
    notes: str = ""
    tasks: list[PlannedTask] = Field(min_length=1)


class ReviewVerdict(BaseModel):
    verdict: str = Field(pattern="^(approve|request_changes)$")
    summary: str
    issues: list[str] = Field(default_factory=list)
    fix_instructions: list[str] = Field(default_factory=list)
