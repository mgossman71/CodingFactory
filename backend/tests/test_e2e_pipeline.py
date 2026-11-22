"""End-to-end: idea → PM/Architect refinement → Q&A → artifacts → approval →
planning → mock development → review → delivery. All with MockLLM + MockExecutor.
"""

from app.models import Artifact, ProjectState, Task, TaskStatus

from conftest import (
    ARCH_OK,
    DOC_ARCH,
    DOC_REQ,
    PLAN_TWO_TASKS,
    PM_WITH_QUESTIONS,
    REVIEW_OK,
    create_project,
    wait_for_state,
)


def test_full_pipeline(env):
    # Queue replies BEFORE creation: discovery starts dispatching immediately.
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_OK])
    pid = create_project(env)

    # Round 0: PM + Architect
    project = wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    assert project["discovery_round"] == 0

    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    agents = {m["agent"] for m in messages}
    assert {"product-manager", "architect"} <= agents
    questions = [m for m in messages if m["kind"] == "question"]
    assert len(questions) == 1

    # User answers -> artifacts -> approval gate
    env.llm.replies.extend([DOC_REQ, {"stories": PM_WITH_QUESTIONS["user_stories"]},
                            {"criteria": [{"id": "ac1", "criterion": "event saved with date", "testable": True}]},
                            DOC_ARCH])
    resp = env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [{"question": questions[0]["content"], "answer": "No email in MVP."}]},
    )
    assert resp.status_code == 200
    project = wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    arts = env.client.get(f"/api/projects/{pid}/artifacts").json()
    slugs = {a["slug"] for a in arts}
    assert {
        "product-requirements.md", "architecture.md", "user-stories.json",
        "acceptance-criteria.json", "open-questions.md",
    } <= slugs

    # User edits an artifact -> version 2, recorded against the record.
    resp = env.client.put(
        f"/api/projects/{pid}/artifacts/product-requirements.md",
        json={"content": "# Edited by user\nExtra constraint added."},
    )
    assert resp.status_code == 200 and resp.json()["current_version"] == 2
    history = env.client.get(f"/api/projects/{pid}/artifacts/product-requirements.md").json()
    assert len(history) == 2 and history[0]["created_by"] == "user"

    # Approve -> plan -> build (mock executor) -> review -> complete
    env.llm.replies.extend([PLAN_TWO_TASKS, REVIEW_OK, REVIEW_OK])
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 200

    project = wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    tasks = env.client.get(f"/api/projects/{pid}/tasks").json()
    assert len(tasks) == 2
    assert all(t["status"] == TaskStatus.COMPLETED.value for t in tasks)
    assert tasks[0]["attempts"] >= 1
    for t in tasks:
        logs = env.client.get(f"/api/projects/{pid}/attempts/{t['attempts_log'][0]['id']}/logs").json()
        assert any(l["kind"] == "command" for l in logs)  # audit trail exists

    arts = {a["slug"] for a in env.client.get(f"/api/projects/{pid}/artifacts").json()}
    assert "delivery-report.md" in arts

    # The approved (edited) spec was made available to the executor via the workspace.
    spec_file = env.settings.workspaces_dir + "/vehicle-maintenance/.codingfactory/product-requirements.md"
    from pathlib import Path

    assert "Edited by user" in Path(spec_file).read_text()

    events = env.client.get(f"/api/projects/{pid}/events").json()
    types = [e["type"] for e in events]
    assert "project_created" in types and "approved" in types and "completed" in types


def test_revision_request_reenters_discovery(env):
    env.llm.replies.extend([
        dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK,
        DOC_REQ, {"stories": []}, {"criteria": []}, DOC_ARCH,
    ])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    # Regenerate artifacts on revision.
    env.llm.replies.extend([DOC_REQ, {"stories": []}, {"criteria": []}, DOC_ARCH])
    resp = env.client.post(
        f"/api/projects/{pid}/approval",
        json={"decision": "REVISION_REQUESTED", "comment": "Add multi-vehicle support."},
    )
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    arts = env.client.get(f"/api/projects/{pid}/artifacts").json()
    req = next(a for a in arts if a["slug"] == "product-requirements.md")
    assert req["current_version"] == 2

    # Rejection ends the project.
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "REJECTED"})
    assert resp.status_code == 200
    assert resp.json()["state"] == ProjectState.FAILED.value
