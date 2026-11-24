"""Pre-pivot (build-era) databases must come up clean.

A row left in the removed BUILDING state used to make every read of Project
raise LookupError on the ProjectState enum, which surfaced as a 500 on
GET /api/projects.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from app.db import Base, make_engine, make_session_factory
from app.llm.mock import MockLLM
from app.main import create_app
from app.models import Project, ProjectState, Task

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


def _seed_legacy_shape(engine, project_id: str) -> None:
    """Put a DB that already has the current tables back into a build-era state."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE projects DROP COLUMN iteration"))  # column did not exist pre-pivot
        conn.execute(text("ALTER TABLE projects DROP COLUMN user_facing"))  # added in D-020
        conn.execute(text("ALTER TABLE tasks DROP COLUMN category"))  # added in D-020
        conn.execute(text("ALTER TABLE tasks DROP COLUMN iteration"))  # per-iteration plan idempotency
        conn.execute(text("ALTER TABLE tasks DROP COLUMN supersedes"))  # added in D-024
        conn.execute(text("ALTER TABLE approvals DROP COLUMN iteration"))  # resume routing
        conn.execute(text("ALTER TABLE approvals DROP COLUMN artifact_versions"))  # added in D-023
        conn.execute(text("ALTER TABLE tasks ADD COLUMN status VARCHAR(20)"))
        conn.execute(text("ALTER TABLE tasks ADD COLUMN attempts INTEGER"))
        conn.execute(text("CREATE INDEX ix_tasks_project_status ON tasks (project_id, status)"))
        conn.execute(text("CREATE TABLE task_attempts (id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36))"))
        conn.execute(text("CREATE TABLE execution_logs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))"))
        conn.execute(text("CREATE TABLE user_feedback (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))"))
        conn.execute(
            text(
                "INSERT INTO projects (id, name, idea, state, discovery_round, error, created_at) "
                "VALUES (:id, 'legacy build', 'an idea from the build era', 'BUILDING', 1, NULL, :now)"
            ),
            {"id": project_id, "now": _NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tasks (id, project_id, number, title, description, acceptance_criteria, "
                "dependencies, created_at, status, attempts) "
                "VALUES (:id, :pid, 1, 'Stand up the API skeleton', 'routes + models', 'server starts', "
                "'[]', :now, 'COMPLETED', 2)"
            ),
            {"id": _uid(), "pid": project_id, "now": _NOW},
        )
        artifact_id = _uid()
        conn.execute(
            text(
                "INSERT INTO artifacts (id, project_id, slug, title, current_version, created_at) "
                "VALUES (:id, :pid, 'architecture.md', 'Technical Architecture', 1, :now)"
            ),
            {"id": artifact_id, "pid": project_id, "now": _NOW},
        )
        conn.execute(
            text(
                "INSERT INTO artifact_versions (id, artifact_id, version, content, created_by, created_at) "
                "VALUES (:id, :aid, 1, '# Architecture\nFastAPI + SQLite.', 'architect', :now)"
            ),
            {"id": _uid(), "aid": artifact_id, "now": _NOW},
        )
        conn.execute(
            text(
                "INSERT INTO artifacts (id, project_id, slug, title, current_version, created_at) "
                "VALUES (:id, :pid, 'delivery-report.md', 'Delivery Report', 1, :now)"
            ),
            {"id": _uid(), "pid": project_id, "now": _NOW},
        )


def test_legacy_build_era_db_boots_and_serves(settings, tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/legacy.db")
    Base.metadata.create_all(engine)
    project_id = _uid()
    _seed_legacy_shape(engine, project_id)

    app = create_app(settings, engine=engine, llm=MockLLM(replies=[]))
    with TestClient(app) as client:
        listing = client.get("/api/projects")
        assert listing.status_code == 200, listing.text
        assert [p["state"] for p in listing.json()] == ["COMPLETED"]

        detail = client.get(f"/api/projects/{project_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["task_count"] == 1
        assert "delivery-report.md" not in detail.json()["artifacts"]

        handoff = client.get(f"/api/projects/{project_id}/handoff")
        assert handoff.status_code == 200, handoff.text
        assert "Stand up the API skeleton" in handoff.text

    sessions = make_session_factory(engine)
    with sessions() as session:
        project = session.get(Project, project_id)
        assert project is not None and project.state is ProjectState.COMPLETED
        assert {a.slug for a in project.artifacts} == {"architecture.md", "handoff-prompt.md"}
        assert session.execute(select(Task).where(Task.project_id == project_id)).scalars().all()

    names = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert not {"status", "attempts", "max_attempts"} & names
    with engine.connect() as conn:
        leftover = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('task_attempts', 'execution_logs', 'user_feedback')"
            )
        ).scalars().all()
        versions = conn.execute(
            text(
                "SELECT version FROM artifact_versions WHERE artifact_id = "
                "(SELECT id FROM artifacts WHERE slug = 'architecture.md')"
            )
        ).scalars().all()
    assert leftover == []
    assert versions == [1]
