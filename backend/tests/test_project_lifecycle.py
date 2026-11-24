"""Project lifecycle: start-over restart and permanent delete (D-021)."""

import asyncio

import pytest
from sqlalchemy import func, select

from app.models import (
    AgentMessage,
    Approval,
    Artifact,
    ArtifactVersion,
    Project,
    ProjectState,
    Task,
    WorkflowEvent,
)

from conftest import (
    ARCH_OK,
    ARCH_WITH_QUESTION,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    PLAN_TWO_TASKS,
    PM_WITH_QUESTIONS,
    STORIES,
    create_project,
    wait_for_state,
)


def drive_to_completed(env) -> str:
    """No questions: discovery -> artifacts -> approval -> plan -> COMPLETED."""
    env.llm.replies.extend(
        [dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK,
         DOC_REQ, STORIES, CRITERIA, DOC_ARCH, PLAN_TWO_TASKS]
    )
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    return pid


def _count(env, model, pid) -> int:
    with env.sessions() as s:
        return s.execute(
            select(func.count()).select_from(model).where(model.project_id == pid)
        ).scalar_one()


# ---------- restart ----------


def test_restart_completed_wipes_and_reprocesses(env):
    pid = drive_to_completed(env)
    assert _count(env, Task, pid) == 2

    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_WITH_QUESTION])  # fresh run stops at Q&A
    resp = env.client.post(f"/api/projects/{pid}/restart")
    assert resp.status_code == 200
    assert resp.json()["state"] in ("DISCOVERY", "AWAITING_INPUT")
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    # Everything derived is gone.
    assert _count(env, Task, pid) == 0
    assert _count(env, Artifact, pid) == 0
    assert env.client.get(f"/api/projects/{pid}/tasks").json() == []
    assert env.client.get(f"/api/projects/{pid}/artifacts").json() == []
    with env.sessions() as s:
        project = s.get(Project, pid)
        assert project.iteration == 0 and project.discovery_round == 0
        assert project.user_facing is False  # re-derived by the fresh PM turn
        assert project.error is None

    # Old discussion is gone: no stale answers; the new run's questions are live.
    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    assert not any(m["kind"] == "answer" for m in messages)
    assert sum(1 for m in messages if m["kind"] == "question") == 1

    # Event history restarted clean: the marker is event #1, old events are gone.
    types = [e["type"] for e in env.client.get(f"/api/projects/{pid}/events").json()]
    assert types[0] == "project_restarted"
    assert "approved" not in types

    # The stale approval gate is not reusable.
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 409


def test_restart_from_awaiting_input_reprocesses_from_scratch(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_WITH_QUESTION])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    old_ids = {
        m["id"] for m in env.client.get(f"/api/projects/{pid}/messages").json()
        if m["kind"] == "question"
    }
    assert len(old_ids) == 1

    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_WITH_QUESTION])
    resp = env.client.post(f"/api/projects/{pid}/restart")
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    new_ids = {
        m["id"] for m in env.client.get(f"/api/projects/{pid}/messages").json()
        if m["kind"] == "question"
    }
    assert len(new_ids) == 1 and new_ids.isdisjoint(old_ids)


def test_restart_missing_project_404(env):
    assert env.client.post("/api/projects/nope/restart").status_code == 404


# ---------- delete ----------


def test_delete_removes_project_and_all_children(env):
    pid = drive_to_completed(env)

    resp = env.client.delete(f"/api/projects/{pid}")
    assert resp.status_code == 204
    assert env.client.get(f"/api/projects/{pid}").status_code == 404
    assert all(p["id"] != pid for p in env.client.get("/api/projects").json())

    with env.sessions() as s:
        for model in (AgentMessage, Task, Approval, WorkflowEvent, Artifact):
            assert s.execute(
                select(func.count()).select_from(model).where(model.project_id == pid)
            ).scalar_one() == 0
        assert s.execute(select(func.count(ArtifactVersion.id))).scalar_one() == 0  # no orphans


def test_delete_missing_project_404(env):
    assert env.client.delete("/api/projects/nope").status_code == 404


# ---------- runner.abort ----------


async def test_runner_abort_cancels_live_tasks(env):
    r = env.client.app.state.runner
    task = asyncio.create_task(asyncio.sleep(60))
    r._live["discovery:p-x"] = task
    r.abort("p-x")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "discovery:p-x" not in r._live
    r.abort("no-such-project")  # no-op, must not raise


async def test_runner_late_callback_keeps_replacement_task(env):
    """abort() then an immediate re-ensure under the same key: the cancelled
    task's done-callback must not evict the replacement."""
    r = env.client.app.state.runner
    old = asyncio.create_task(asyncio.sleep(60))
    r._live["discovery:p-y"] = old
    old.add_done_callback(lambda t: r._forget("discovery:p-y", t))
    r.abort("p-y")
    new = asyncio.create_task(asyncio.sleep(60))
    r._live["discovery:p-y"] = new
    with pytest.raises(asyncio.CancelledError):
        await old
    await asyncio.sleep(0)  # let the done-callback run
    assert r._live.get("discovery:p-y") is new
    r.abort("p-y")  # don't leave a test-loop task for app shutdown to gather
    with pytest.raises(asyncio.CancelledError):
        await new


def test_restart_keeps_event_ids_monotonic(env):
    pid = drive_to_completed(env)
    with env.sessions() as s:
        before = s.execute(select(func.max(WorkflowEvent.id))).scalar_one()
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_WITH_QUESTION])
    env.client.post(f"/api/projects/{pid}/restart")
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    events = env.client.get(f"/api/projects/{pid}/events").json()
    assert events[0]["type"] == "project_restarted"
    assert all(e["id"] > before for e in events)
