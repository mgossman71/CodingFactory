"""Project rename: PATCH /api/projects/{id} (name is always editable)."""

from app.models import Project, WorkflowEvent

from conftest import create_project


def test_rename_returns_updated_name(env):
    pid = create_project(env)
    resp = env.client.patch(f"/api/projects/{pid}", json={"name": "My Renamed Project"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "My Renamed Project"
    # The list and detail both reflect the change.
    assert any(p["name"] == "My Renamed Project" for p in env.client.get("/api/projects").json())
    assert env.client.get(f"/api/projects/{pid}").json()["name"] == "My Renamed Project"


def test_rename_records_event(env):
    pid = create_project(env)
    env.client.patch(f"/api/projects/{pid}", json={"name": "Renamed"})
    events = env.client.get(f"/api/projects/{pid}/events").json()
    rename = [e for e in events if e["type"] == "project_renamed"]
    assert len(rename) == 1
    assert '"Renamed"' in rename[0]["payload"]


def test_rename_strips_whitespace(env):
    pid = create_project(env)
    resp = env.client.patch(f"/api/projects/{pid}", json={"name": "  trimmed  "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "trimmed"


def test_rename_blank_name_rejected(env):
    pid = create_project(env)
    # min_length=1 passes for a space, but the endpoint strips and re-checks.
    resp = env.client.patch(f"/api/projects/{pid}", json={"name": "   "})
    assert resp.status_code == 422


def test_rename_empty_name_rejected(env):
    pid = create_project(env)
    resp = env.client.patch(f"/api/projects/{pid}", json={"name": ""})
    assert resp.status_code == 422  # min_length=1 on the schema


def test_rename_too_long_rejected(env):
    pid = create_project(env)
    resp = env.client.patch(f"/api/projects/{pid}", json={"name": "x" * 201})
    assert resp.status_code == 422


def test_rename_missing_project_404(env):
    assert env.client.patch("/api/projects/nope", json={"name": "x"}).status_code == 404


def test_rename_allowed_in_any_state(env):
    """Renaming works during discovery, not just idle/terminal states."""
    env.llm.replies.extend([
        {"summary": "x", "confirmed_requirements": [], "assumptions": [], "open_questions": [],
         "user_stories": [], "mvp_scope": [], "out_of_scope": [], "user_facing": False,
         "genre": "tool", "genre_expectations": []},
    ])
    pid = create_project(env)
    resp = env.client.patch(f"/api/projects/{pid}", json={"name": "Mid-discovery rename"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Mid-discovery rename"


def test_create_with_name(env):
    resp = env.client.post("/api/projects", json={
        "idea": "Build a vehicle maintenance tracker for cars and mowers.", "name": "MyCustomName"
    })
    assert resp.status_code == 201
    assert resp.json()["name"] == "MyCustomName"


def test_create_without_name_derives_from_idea(env):
    resp = env.client.post("/api/projects", json={
        "idea": "Build a vehicle maintenance tracker for cars and mowers."
    })
    assert resp.status_code == 201
    name = resp.json()["name"]
    assert name  # non-empty
    assert "Build a vehicle" in name  # derived from first 8 words
