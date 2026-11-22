"""Change requests, mid-build notes, and REPLAN steering."""

import asyncio

from sqlalchemy import select

import pytest

from app.models import AgentMessage, Artifact, Project, ProjectState, Task, TaskStatus, UserFeedback
from app.services.build import BuildContext, run_build
from app.services.events import EventBus
from app.services.planning import PlanValidationError, validate_plan

from conftest import (
    ARCH_OK,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    PLAN_TWO_TASKS,
    PM_WITH_QUESTIONS,
    REVIEW_OK,
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


def _queue_discovery_no_questions(env):
    env.llm.replies.extend([PM_NO_QS, ARCH_OK, DOC_REQ, STORIES, CRITERIA, DOC_ARCH])


def drive_to_completed(env) -> str:
    """Iteration 1: idea -> approval -> two tasks built -> COMPLETED."""
    # Queue everything up front: the runner starts consuming as soon as
    # the approval POST returns.
    _queue_discovery_no_questions(env)
    env.llm.replies.extend([PLAN_TWO_TASKS, REVIEW_OK, REVIEW_OK])
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


# ---------- change request from COMPLETED (API end-to-end) ----------


def test_change_request_runs_second_iteration(env):
    pid = drive_to_completed(env)

    # Delta discovery -> approval gate -> delta plan -> new task -> COMPLETED again.
    _queue_discovery_no_questions(env)
    env.llm.replies.extend([PLAN_DELTA3, REVIEW_OK])
    resp = env.client.post(
        f"/api/projects/{pid}/feedback",
        json={"content": "Add per-build color themes to the site", "mode": "REPLAN"},
    )
    assert resp.status_code == 200, resp.text
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert [t["number"] for t in tasks] == [1, 2, 3]
    assert tasks[2]["status"] == TaskStatus.COMPLETED.value

    with env.sessions() as s:
        project = s.get(Project, pid)
        assert project.iteration == 1
        report = s.execute(
            select(Artifact).where(
                Artifact.project_id == pid, Artifact.slug == "delivery-report.md"
            )
        ).scalar_one()
        assert report.current_version == 2  # report regenerated, not duplicated
        change = s.execute(
            select(AgentMessage).where(
                AgentMessage.project_id == pid, AgentMessage.kind == "change_request"
            )
        ).scalar_one()
        assert change.iteration == 1

    prompts = [[m["content"] for m in call] for call in env.llm.calls]
    assert any("CHANGE-REQUEST CYCLE 1" in c for p in prompts for c in p)
    assert any("Add per-build color themes" in c for p in prompts for c in p)
    # New dev task knows it's a later iteration on an existing codebase.
    assert "ITERATION 2" in env.executor.instructions_seen[-1]


def test_feedback_rejected_in_discovery(env):
    pid = create_project(env)
    resp = env.client.post(f"/api/projects/{pid}/feedback", json={"content": "dark mode", "mode": "NOTE"})
    assert resp.status_code == 409


# ---------- mid-build steering (service level, race-free) ----------


def _ctx(env) -> BuildContext:
    return BuildContext(
        sessions=env.sessions, llm=env.llm, executor=env.executor, bus=EventBus(), settings=env.settings
    )


def _impose_building_state(env, pid: str, feedback_mode: str, content: str):
    """Skip discovery/planning: put the project in BUILDING with one READY task."""
    wait_for_state(env, pid, {ProjectState.FAILED})  # reply-less discovery fails fast
    with env.sessions() as s:
        project = s.get(Project, pid)
        project.state = ProjectState.BUILDING
        s.add(
            Task(
                project_id=pid, number=1, title="build page", description="d",
                acceptance_criteria="a", status=TaskStatus.READY,
            )
        )
        s.add(UserFeedback(project_id=pid, content=content, mode=feedback_mode))
        s.commit()


def test_replan_feedback_pauses_build_into_delta_cycle(env):
    pid = create_project(env)
    _impose_building_state(env, pid, "REPLAN", "make it dark mode")

    outcome = asyncio.run(run_build(_ctx(env), pid))
    assert outcome == "steering"

    with env.sessions() as s:
        project = s.get(Project, pid)
        assert project.state == ProjectState.DISCOVERY
        assert project.iteration == 1
        fb = s.execute(
            select(UserFeedback).where(UserFeedback.project_id == pid)
        ).scalar_one()
        assert fb.consumed_at is not None
        msg = s.execute(
            select(AgentMessage).where(
                AgentMessage.project_id == pid, AgentMessage.kind == "change_request"
            )
        ).scalar_one()
        assert msg.content == "make it dark mode"
    # The READY task was left untouched for the re-planned cycle to decide.
    assert env.executor.instructions_seen == []


def test_note_feedback_injected_into_next_attempt(env):
    pid = create_project(env)
    _impose_building_state(env, pid, "NOTE", "make it dark mode")
    env.llm.replies.extend([REVIEW_OK])

    outcome = asyncio.run(run_build(_ctx(env), pid))
    assert outcome is None

    assert "USER FEEDBACK RECEIVED DURING THE BUILD" in env.executor.instructions_seen[0]
    assert "make it dark mode" in env.executor.instructions_seen[0]
    with env.sessions() as s:
        project = s.get(Project, pid)
        assert project.state == ProjectState.COMPLETED
        fb = s.execute(
            select(UserFeedback).where(UserFeedback.project_id == pid)
        ).scalar_one()
        assert fb.consumed_at is not None
