"""WebSocket activity stream: replay of persisted events + live delivery."""

from app.models import ProjectState
from conftest import create_project, wait_for_state


def test_ws_replays_events(env):
    # No scripted LLM replies -> discovery fails -> exactly two events exist.
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.FAILED})

    with env.client.websocket_connect(f"/ws/projects/{pid}") as ws:
        seen = [ws.receive_json() for _ in range(2)]
        for data in seen:
            assert data["project_id"] == pid
        types = [d["type"] for d in seen]
        assert "project_created" in types
        assert "workflow_failed" in types
