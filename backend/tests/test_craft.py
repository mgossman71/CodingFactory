"""D-020 craft pipeline: design/brand/rubric artifacts for user-facing products,
answer-interpretation echo, task-category coverage (hard reject + bounded
re-ask), single-valued stack validation, and craft flow-through to handoff."""

import pytest
from pydantic import ValidationError

from app.agents.schemas import ArchitectReview, TaskPlan
from app.models import Project, ProjectState
from app.services.planning import PlanCoverageError, validate_plan

from conftest import (
    ARCH_OK,
    ARCH_WITH_QUESTION,
    DOC_ARCH,
    DOC_COPY,
    DOC_DESIGN,
    DOC_REQ,
    DOC_RUBRIC,
    PLAN_TWO_TASKS,
    PLAN_USER_FACING,
    PM_USER_FACING,
    create_project,
    wait_for_state,
)


def _drive_to_approval(env) -> str:
    """User-facing project: PM+Architect -> answers -> artifacts -> approval gate."""
    env.llm.replies.extend([PM_USER_FACING, ARCH_WITH_QUESTION])
    pid = create_project(env, "Pro gaming PC builder marketing site with a build gallery.")
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    questions = [
        m for m in env.client.get(f"/api/projects/{pid}/messages").json() if m["kind"] == "question"
    ]
    env.llm.replies.extend(
        [DOC_REQ, {"stories": []}, {"criteria": []}, DOC_DESIGN, DOC_COPY, DOC_RUBRIC, DOC_ARCH]
    )
    resp = env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [{"question_id": questions[0]["id"], "question": questions[0]["content"],
                           "answer": "yes"}]},
    )
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    return pid


# ---- schema: single-valued stack decisions ----


def test_architect_rejects_stack_alternatives():
    with pytest.raises(ValidationError, match="ONE concrete choice"):
        ArchitectReview.model_validate(
            dict(ARCH_OK, recommended_stack=["FastAPI", "Tailwind CSS or Bootstrap"])
        )


def test_architect_allows_or_inside_parenthetical_rationale():
    review = ArchitectReview.model_validate(
        dict(ARCH_OK, recommended_stack=["SQLite (dev or single-node deploy)", "FastAPI"])
    )
    assert review.recommended_stack[0].startswith("SQLite")


def test_acceptance_criterion_has_no_testable_gate():
    from app.agents.schemas import AcceptanceCriterion

    assert "testable" not in AcceptanceCriterion.model_fields


# ---- plan category coverage ----


def test_coverage_required_rejects_plumbing_only_plan():
    plan = TaskPlan.model_validate(PLAN_TWO_TASKS)
    with pytest.raises(PlanCoverageError, match="design/polish"):
        validate_plan(plan, 12, require_categories=True)


def test_coverage_required_accepts_full_plan():
    plan = TaskPlan.model_validate(PLAN_USER_FACING)
    assert len(validate_plan(plan, 12, require_categories=True)) == 4


def test_coverage_not_required_by_default_or_for_deltas():
    plan = TaskPlan.model_validate(PLAN_TWO_TASKS)
    assert len(validate_plan(plan, 12)) == 2  # iteration/delta plans exempt
    assert len(validate_plan(plan, 12, preexisting={9}, require_categories=False)) == 2


# ---- end-to-end: user-facing artifacts, echo, categories, handoff ----


def test_user_facing_pipeline(env):
    pid = _drive_to_approval(env)

    slugs = {a["slug"] for a in env.client.get(f"/api/projects/{pid}/artifacts").json()}
    assert {"design-spec.md", "brand-and-copy.md", "quality-rubric.md"} <= slugs

    # Terse answer ("yes") gets an explicit interpretation at the approval gate.
    open_q = env.client.get(f"/api/projects/{pid}/artifacts/open-questions.md").json()
    assert "Interpreted answers" in open_q[0]["content"]

    env.llm.replies.extend([PLAN_USER_FACING])
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert {t["category"] for t in tasks} == {"feature", "design", "content", "test"}

    handoff = env.client.get(f"/api/projects/{pid}/handoff").text
    assert "Task 2: Design implementation _[design]_" in handoff
    assert "PASSING TESTS IS THE FLOOR" in handoff
    assert "Built To Win" in handoff           # copy deck inlined
    assert "Hero feels premium" in handoff     # rubric inlined
    assert "Dark, high-energy" in handoff      # design spec inlined


def test_coverage_reject_then_reask_recovers(env):
    pid = _drive_to_approval(env)
    # First plan is plumbing-only; the Dev Manager is re-asked once and recovers.
    env.llm.replies.extend([PLAN_TWO_TASKS, PLAN_USER_FACING])
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    events = [e["type"] for e in env.client.get(f"/api/projects/{pid}/events").json()]
    assert "plan_coverage_rejected" in events
    assert len(env.client.get(f"/api/projects/{pid}/tasks").json()) == 4


def test_coverage_exhausted_fails_project(env):
    pid = _drive_to_approval(env)
    env.llm.replies.extend([PLAN_TWO_TASKS])  # only one attempt scripted; re-ask exhausts mocks
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    project = wait_for_state(env, pid, {ProjectState.FAILED}, timeout=30)
    assert project["error"]


def test_user_facing_flag_persisted(env):
    pid = _drive_to_approval(env)
    with env.sessions() as s:
        project = s.get(Project, pid)
        assert project.user_facing is True
