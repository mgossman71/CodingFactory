"""Guards: approval gating, answers gating, failure paths, recovery."""

import asyncio
import time

from app.models import Project, ProjectState

from conftest import (
    ARCH_OK,
    ARCH_WITH_QUESTION,
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
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_WITH_QUESTION])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    resp = env.client.post(f"/api/projects/{pid}/cancel")
    assert resp.json()["state"] == ProjectState.CANCELLED.value
    resp2 = env.client.post(f"/api/projects/{pid}/cancel")
    assert resp2.status_code == 409


# ---------- D-026: cancel is never undone by a returning model call ----------


def _hold_reply(payload, delay: float):
    """A reply that stays 'in flight' (awaiting) for `delay` seconds so the
    test can act while discovery is mid-model-call."""

    async def slow(_messages):
        await asyncio.sleep(delay)
        return payload

    return slow


def test_cancel_during_pending_model_call_is_not_undone(env):
    """The finding (P2): Cancel updated the DB but left the background task
    running; when the pending model call returned, the project flipped from
    CANCELLED back to AWAITING_INPUT."""
    env.llm.replies.append(_hold_reply(PM_WITH_QUESTIONS, 0.8))
    pid = create_project(env)
    time.sleep(0.3)  # the PM model call is in flight now

    resp = env.client.post(f"/api/projects/{pid}/cancel")
    assert resp.status_code == 200
    assert resp.json()["state"] == "CANCELLED"

    # The live task is gone...
    live = env.client.app.state.runner._live
    assert not [k for k in live if k.endswith(pid)]
    # ...and nothing revives the project while/after the scripted reply lands.
    deadline = time.time() + 1.5
    while time.time() < deadline:
        assert env.client.get(f"/api/projects/{pid}").json()["state"] == "CANCELLED"
        time.sleep(0.1)


def test_discovery_write_guard_refuses_to_revive_a_cancelled_project(env):
    """Defense in depth: even if the cancellation lands without aborting the
    task (another worker, or the await already returned), every post-await
    write re-checks the live project state (D-026)."""
    env.llm.replies.append(_hold_reply(PM_WITH_QUESTIONS, 0.8))
    pid = create_project(env)
    time.sleep(0.3)

    with env.sessions() as s:  # CANCELLED committed out-of-band, task NOT aborted
        project = s.get(Project, pid)
        project.state = ProjectState.CANCELLED
        s.commit()

    time.sleep(1.5)  # the PM call returns; discovery must notice and stop
    assert env.client.get(f"/api/projects/{pid}").json()["state"] == "CANCELLED"
    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    assert not any(m["agent"] == "product-manager" for m in messages)


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
