"""Guards: approval gating, answers gating, failure paths, recovery."""

from app.models import Project, ProjectState

from conftest import (
    ARCH_OK,
    DOC_ARCH,
    DOC_REQ,
    PM_WITH_QUESTIONS,
    create_project,
    wait_for_state,
)


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


def test_feedback_rejected_when_not_completed(env):
    pid = create_project(env)
    resp = env.client.post(f"/api/projects/{pid}/feedback", json={"content": "dark mode"})
    assert resp.status_code == 409


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
