"""Structured output schemas for every agent role."""

import re

from pydantic import BaseModel, Field, model_validator


class Question(BaseModel):
    """A candidate (PM) or final operator-facing (Architect) question.

    D-028: questions carry structured detail so the UI can present the choice
    instead of a bare sentence — why the answer changes what gets built, 2-3
    distinct options with a recommended one where the choices are enumerable,
    and a free-text question (empty options) when choices would hide context.
    """

    id: str
    question: str
    why_it_matters: str = ""
    options: list[str] = Field(default_factory=list)
    recommended_option: str = ""  # one of `options`; tradeoff explained in why_it_matters


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
    material_assumptions: list[str] = Field(
        default_factory=list
    )  # D-047: high-impact assumptions (a wrong guess changes what gets built) — surfaced for confirmation at approval
    open_questions: list[Question] = Field(default_factory=list)
    user_stories: list[UserStory] = Field(default_factory=list)
    mvp_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    user_facing: bool = True  # drives design/copy/rubric artifacts + plan coverage
    genre: str = ""  # product genre inferred from the idea, e.g. "marketing site"
    genre_expectations: list[str] = Field(default_factory=list)


class ArchitectReview(BaseModel):
    feasibility: str = Field(pattern="^(feasible|risky|infeasible)$")
    recommended_stack: list[str] = Field(default_factory=list)
    architecture_summary: str = ""
    risks: list[str] = Field(default_factory=list)
    challenges_for_pm: list[str] = Field(default_factory=list)
    open_questions: list[Question] = Field(default_factory=list)

    @model_validator(mode="after")
    def single_stack_no_alternatives(self) -> "ArchitectReview":
        # A stack list with an alternative ("Tailwind CSS or Bootstrap") invites
        # the implementing agent to pick the laziest option. Force a decision.
        # Parenthetical notes ("SQLite (dev or single-node)") are rationale, not alternatives.
        hedges = [s for s in self.recommended_stack if " or " in re.sub(r"\([^)]*\)", "", s).lower()]
        if hedges:
            raise ValueError(
                "recommended_stack must name ONE concrete choice per component; "
                f"alternatives found: {hedges}"
            )
        return self


class DocumentOut(BaseModel):
    """Generic wrapper for markdown artifacts."""

    markdown: str


class RequirementsDoc(DocumentOut):
    """Product requirements plus the PM's reading of each user answer.

    Interpretations are assembled deterministically into open-questions.md so
    terse/ambiguous answers (a bare "yes" to a three-part question) are
    visible at the approval gate instead of silently normalized.
    """

    answer_interpretations: list[str] = Field(default_factory=list)


class UserStoriesOut(BaseModel):
    stories: list[UserStory]


class AcceptanceCriterion(BaseModel):
    id: str
    story_id: str = ""
    criterion: str


class AcceptanceCriteriaOut(BaseModel):
    criteria: list[AcceptanceCriterion]


class PlannedTask(BaseModel):
    number: int
    title: str
    description: str
    acceptance_criteria: str = ""
    dependencies: list[int] = Field(default_factory=list)
    supersedes: list[int] = Field(
        default_factory=list
    )  # iteration plans: earlier task numbers whose functionality this task replaces
    category: str = Field(
        default="feature",
        pattern="^(feature|infra|design|content|polish|test)$",
    )


class TaskPlan(BaseModel):
    notes: str = ""
    tasks: list[PlannedTask] = Field(min_length=1)
