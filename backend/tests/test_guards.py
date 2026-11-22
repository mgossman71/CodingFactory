"""Guards: approval gating, answers gating, failure paths, recovery."""

import pytest

from app.db import Base
from app.models import Project, ProjectState, Task, TaskAttempt, TaskStatus
from app.services.opencode import ExecResult
from app.services.planning import _promote_ready

from conftest import (
    ARCH_OK,
    DOC_ARCH,
    DOC_REQ,
    PM_WITH_QUESTIONS,
    REVIEW_OK,
    create_project,
    wait_for_state,
)

REVIEW_FIX = {
    "verdict": "request_changes",
    "summary": "missing criterion",
    "issues": ["criterion not met"],
    "fix_instructions": ["implement listing"],
}

PLAN_ONE = {"tasks": [{"number": 1, "title": "only", "description": "d", "acceptance_criteria": "a", "dependencies": []}]}


def test_cannot_approve_before_gate(env):
    pid = create_project(env)
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 409


def test_cannot_answer_when_not_awaiting(env):
    pid = create_project(env)
    resp = env.client.post(f"/api/projects/{pid}/answers", json={"answers": [{"answer": "x"}]})
    assert resp.status_code == 409


def test_idea_too_short_rejected(env):
    resp = env.client.post("/api/projects", json={"idea": "hi"})
    assert resp.status_code == 422


def test_llm_failure_marks_project_failed(env):
    pid = create_project(env)
    # No replies scripted -> MockLLM raises -> runner records failure.
    project = wait_for_state(env, pid, {ProjectState.FAILED})
    assert project["error"] and "MockLLM" in project["error"]


def test_cancel_active_project(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_OK])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    resp = env.client.post(f"/api/projects/{pid}/cancel")
    assert resp.json()["state"] == ProjectState.CANCELLED.value
    resp2 = env.client.post(f"/api/projects/{pid}/cancel")
    assert resp2.status_code == 409


def test_reviewer_fix_loop_bounded(env):
    """Reviewer requests changes 3x (max attempts) -> task and project FAIL."""
    env.llm.replies.extend([
        dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK,
        DOC_REQ, {"stories": []}, {"criteria": []}, DOC_ARCH,
        PLAN_ONE, REVIEW_FIX, REVIEW_FIX, REVIEW_FIX,
    ])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    project = wait_for_state(env, pid, {ProjectState.FAILED}, timeout=30)
    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert tasks[0]["attempts"] == 3  # never exceeds the cap
    assert tasks[0]["status"] == TaskStatus.FAILED.value


def test_recovery_requeues_stale_in_progress_task(env):
    """Simulated crash: IN_PROGRESS task with no live executor resumes via /resume."""
    pid = create_project(env)
    # Let the (reply-less) discovery dispatch fail fast, then impose the crashed state.
    wait_for_state(env, pid, {ProjectState.FAILED})
    sessions = env.sessions
    with sessions() as s:
        project = s.get(Project, pid)
        project.state = ProjectState.BUILDING
        t = Task(
            project_id=pid, number=1, title="stale", description="d",
            acceptance_criteria="a", status=TaskStatus.IN_PROGRESS, attempts=1, max_attempts=3,
        )
        s.add(t)
        s.flush()
        s.add(TaskAttempt(task_id=t.id, attempt_number=1, status="RUNNING"))
        s.commit()

    env.llm.replies.extend([REVIEW_OK])
    env.executor.results.append(ExecResult(ok=True, exit_code=0, final_text="recovered"))

    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200

    project = wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert tasks[0]["status"] == TaskStatus.COMPLETED.value
    assert tasks[0]["attempts"] == 2  # interrupted attempt still shows, but didn't consume budget
    assert tasks[0]["attempts_log"][0]["status"] == "INTERRUPTED"


def test_resume_resets_attempt_budget(env):
    """After hitting the failure cap, /resume clears old failures so the
    task can genuinely retry."""
    env.llm.replies.extend([
        dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK,
        DOC_REQ, {"stories": []}, {"criteria": []}, DOC_ARCH,
        PLAN_ONE, REVIEW_FIX, REVIEW_FIX, REVIEW_FIX,
    ])
    env.executor.results.extend([
        ExecResult(ok=True, exit_code=0, final_text="try") for _ in range(3)
    ])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.FAILED}, timeout=30)

    # Now let the retry succeed.
    env.llm.replies.extend([REVIEW_OK])
    env.executor.results.append(ExecResult(ok=True, exit_code=0, final_text="fixed"))
    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert tasks[0]["status"] == TaskStatus.COMPLETED.value
    assert tasks[0]["attempts"] == 4  # cumulative tries; resume reset only the budget
    assert {a["status"] for a in tasks[0]["attempts_log"][:3]} == {"SUPERSEDED"}


def test_signal_kill_requeues_without_consuming_attempt(env):
    """Exit 143 (SIGTERM) from the executor = interruption, not task failure."""
    env.llm.replies.extend([
        dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK,
        DOC_REQ, {"stories": []}, {"criteria": []}, DOC_ARCH,
        PLAN_ONE, REVIEW_OK,
    ])
    env.executor.results.extend(
        [
            ExecResult(ok=False, exit_code=143, notes="terminated"),
            ExecResult(ok=True, exit_code=0, final_text="second try"),
        ]
    )
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert tasks[0]["status"] == TaskStatus.COMPLETED.value
    assert tasks[0]["attempts"] == 2  # total tries incl. the interrupted one
    assert tasks[0]["attempts_log"][0]["status"] == "INTERRUPTED"


def test_resume_discovery_when_no_tasks(env):
    pid = create_project(env)
    with env.sessions() as s:
        project = s.get(Project, pid)
        project.state = ProjectState.FAILED
        project.error = "boom"
        s.commit()

    env.llm.replies.extend([
        dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK,
        DOC_REQ, {"stories": []}, {"criteria": []}, DOC_ARCH,
    ])
    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
