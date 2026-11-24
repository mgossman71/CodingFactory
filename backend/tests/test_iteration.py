"""Change requests: a COMPLETED project re-enters discovery, re-plans, and
regenerates the handoff prompt. No build execution."""

from sqlalchemy import select

import pytest

from app.models import AgentMessage, Artifact, Project, ProjectState, Task
from app.services.planning import PlanValidationError, validate_plan

from conftest import (
    ARCH_OK,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    PLAN_TWO_TASKS,
    PM_WITH_QUESTIONS,
    STORIES,
    create_project,
    wait_for_state,
)

PLAN_DELTA3 = {
    "tasks": [
        {
            "number": 3,
            "title": "Add per-build color themes",
            "description": "theme picker on build pages",
            "acceptance_criteria": "theme choice persists per build",
            "dependencies": [2],
        }
    ]
}

PM_NO_QS = dict(PM_WITH_QUESTIONS, open_questions=[])

# D-024: the finding's scenario — a change that REPLACES delivered work.
PLAN_EMAIL_TWO = {
    "tasks": [
        {"number": 1, "title": "Data layer", "description": "models + CRUD",
         "acceptance_criteria": "events can be created and listed", "dependencies": [], "category": "feature"},
        {"number": 2, "title": "Email reminders", "description": "SMTP send at due date",
         "acceptance_criteria": "email delivered at due date", "dependencies": [1], "category": "feature"},
    ]
}

PLAN_NOTIF_REPLACES_EMAIL = {
    "tasks": [
        {
            "number": 3,
            "title": "In-app notification center",
            "description": "notifications panel; remove the SMTP delivery path",
            "acceptance_criteria": "notifications appear in-app; no SMTP code path remains",
            "dependencies": [1],
            "category": "feature",
            "supersedes": [2],
        }
    ]
}


def _queue_discovery_no_questions(env):
    env.llm.replies.extend([PM_NO_QS, ARCH_OK, DOC_REQ, STORIES, CRITERIA, DOC_ARCH])


def drive_to_completed(env) -> str:
    """Iteration 0: idea -> approval -> plan (two tasks) -> handoff -> COMPLETED."""
    _queue_discovery_no_questions(env)
    env.llm.replies.extend([PLAN_TWO_TASKS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    return pid


# ---------- validate_plan: delta plans ----------


def _pt(number, deps):
    return {"number": number, "title": f"t{number}", "description": "d", "acceptance_criteria": "a", "dependencies": deps}


def test_validate_plan_allows_deps_on_existing_tasks():
    from app.agents.schemas import TaskPlan

    plan = TaskPlan.model_validate({"tasks": [_pt(3, [2])]})
    tasks = validate_plan(plan, 10, preexisting={1, 2})
    assert [t.number for t in tasks] == [3]


def test_validate_plan_rejects_replanning_existing_numbers():
    from app.agents.schemas import TaskPlan

    plan = TaskPlan.model_validate({"tasks": [_pt(2, [])]})
    with pytest.raises(PlanValidationError, match="re-plans existing"):
        validate_plan(plan, 10, preexisting={1, 2})


# ---------- validate_plan: supersedes (D-024) ----------


def test_validate_plan_allows_superseding_preexisting_tasks():
    from app.agents.schemas import TaskPlan

    plan = TaskPlan.model_validate({"tasks": [dict(_pt(3, [2]), supersedes=[2])]})
    tasks = validate_plan(plan, 10, preexisting={1, 2})
    assert tasks[0].supersedes == [2]


def test_validate_plan_rejects_superseding_unknown_tasks():
    from app.agents.schemas import TaskPlan

    plan = TaskPlan.model_validate({"tasks": [dict(_pt(3, []), supersedes=[9])]})
    with pytest.raises(PlanValidationError, match="supersedes unknown"):
        validate_plan(plan, 10, preexisting={1, 2})


def test_validate_plan_rejects_superseding_a_task_in_the_same_plan():
    """Silent re-plan: a new task cannot "replace" another brand-new task."""
    from app.agents.schemas import TaskPlan

    plan = TaskPlan.model_validate(
        {"tasks": [dict(_pt(3, []), supersedes=[4]), _pt(4, [])]}
    )
    with pytest.raises(PlanValidationError, match="supersedes unknown"):
        validate_plan(plan, 10, preexisting={1, 2})


# ---------- change request from COMPLETED (API end-to-end) ----------


def test_change_request_runs_second_iteration(env):
    pid = drive_to_completed(env)

    # Delta discovery -> approval gate -> delta plan -> new task -> handoff v2 -> COMPLETED.
    _queue_discovery_no_questions(env)
    env.llm.replies.extend([PLAN_DELTA3])
    resp = env.client.post(
        f"/api/projects/{pid}/feedback",
        json={"content": "Add per-build color themes to the site"},
    )
    assert resp.status_code == 200, resp.text
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert [t["number"] for t in tasks] == [1, 2, 3]

    with env.sessions() as s:
        project = s.get(Project, pid)
        assert project.iteration == 1
        report = s.execute(
            select(Artifact).where(Artifact.project_id == pid, Artifact.slug == "handoff-prompt.md")
        ).scalar_one()
        assert report.current_version == 2  # prompt regenerated, not duplicated
        change = s.execute(
            select(AgentMessage).where(
                AgentMessage.project_id == pid, AgentMessage.kind == "change_request"
            )
        ).scalar_one()
        assert change.iteration == 1

    # The regenerated prompt covers the whole current plan, including the new task.
    handoff = env.client.get(f"/api/projects/{pid}/handoff").text
    assert "Task 3: Add per-build color themes" in handoff
    assert "Iteration 2" in handoff

    prompts = [[m["content"] for m in call] for call in env.llm.calls]
    assert any("CHANGE-REQUEST CYCLE 1" in c for p in prompts for c in p)
    assert any("Add per-build color themes" in c for p in prompts for c in p)


# ---------- supersession: replaced work is called out, not re-ordered in (D-024) ----------


def test_change_request_handoff_targets_existing_repo_and_marks_supersession(env):
    """The finding (P1): a change replacing email delivery with in-app
    notifications produced a fresh-build prompt that still told the agent to
    implement the old email task."""
    _queue_discovery_no_questions(env)
    env.llm.replies.extend([PLAN_EMAIL_TWO])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    v1 = env.client.get(f"/api/projects/{pid}/handoff").text
    assert "fresh working directory" in v1  # iteration 0: whole-plan brief

    _queue_discovery_no_questions(env)
    env.llm.replies.extend([PLAN_NOTIF_REPLACES_EMAIL])
    resp = env.client.post(
        f"/api/projects/{pid}/feedback",
        json={"content": "Replace email reminders with in-app notifications"},
    )
    assert resp.status_code == 200, resp.text
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    v2 = env.client.get(f"/api/projects/{pid}/handoff").text
    # An existing-repository brief, not a fresh-build one.
    assert "fresh working directory" not in v2
    assert "already-delivered codebase" in v2
    assert "Iteration 2" in v2
    # Every prior task is labeled: the email task is explicitly replaced.
    assert "SUPERSEDED by task 3" in v2
    assert "NEW in this change request" in v2
    assert "delivered earlier" in v2

    with env.sessions() as s:
        t3 = s.query(Task).filter_by(project_id=pid, number=3).one()
        assert t3.supersedes == [2]
        # The old task row keeps its history; supersession is expressed, not destructive.
        t2 = s.query(Task).filter_by(project_id=pid, number=2).one()
        assert t2.title == "Email reminders"

    # The Dev Manager was instructed to express replacement via `supersedes`.
    prompts = [[m["content"] for m in call] for call in env.llm.calls]
    assert any("`supersedes`" in c for p in prompts for c in p)


# ---------- resume routing ----------


def test_resume_after_failed_change_request_reruns_discovery(env):
    pid = drive_to_completed(env)
    # No replies queued: the change request's PM turn fails.
    env.client.post(f"/api/projects/{pid}/feedback", json={"content": "Add per-build color themes to the site"})
    wait_for_state(env, pid, {ProjectState.FAILED})

    # Resume must go back through discovery + the approval gate, not plan
    # deltas against the un-revised iteration-0 spec.
    _queue_discovery_no_questions(env)
    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200, resp.text
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    assert [t["number"] for t in env.client.get(f"/api/projects/{pid}/tasks").json()] == [1, 2]


def test_resume_after_post_plan_failure_keeps_the_plan(env):
    pid = drive_to_completed(env)
    # Simulate a failure after the plan was committed (e.g. while writing the handoff).
    with env.sessions() as s:
        project = s.get(Project, pid)
        project.state = ProjectState.FAILED
        project.error = "boom"
        s.commit()
    calls_before = len(env.llm.calls)

    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200, resp.text
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    # Same iteration already planned: no Dev Manager call, no duplicate tasks.
    assert len(env.llm.calls) == calls_before
    assert [t["number"] for t in env.client.get(f"/api/projects/{pid}/tasks").json()] == [1, 2]
