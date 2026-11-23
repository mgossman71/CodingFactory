"""generate_handoff assembles a self-contained build prompt from the approved
specs + the task plan. Deterministic — no LLM call."""

from app.db import Base, make_engine, make_session_factory
from app.models import Project, ProjectState, Task
from app.services.discovery import save_artifact
from app.services.handoff import approved_specs, generate_handoff


def _project(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/h.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = Project(name="Widget", idea="a widget app", state=ProjectState.PLANNING)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "Product Requirements", "REQ: must do X", "product-manager")
        save_artifact(s, p.id, "architecture.md", "Technical Architecture", "ARCH: FastAPI + SQLite", "architect")
        s.add(Task(project_id=p.id, number=1, title="Scaffold", description="create app",
                   acceptance_criteria="app boots", dependencies=[]))
        s.add(Task(project_id=p.id, number=2, title="Feature X", description="implement X",
                   acceptance_criteria="X works", dependencies=[1]))
        s.commit()
        return sessions, p.id


def test_handoff_embeds_specs_tasks_and_brief(tmp_path):
    sessions, pid = _project(tmp_path)
    with sessions() as s:
        project = s.get(Project, pid)
        prompt = generate_handoff(s, project)

    assert "must do X" in prompt and "FastAPI + SQLite" in prompt
    assert "Task 1: Scaffold" in prompt and "Task 2: Feature X" in prompt
    assert "**Depends on:** #1" in prompt
    assert "X works" in prompt
    # The instruction brief and ordering are present.
    assert "run the project's test command" in prompt
    assert prompt.index("Task 1:") < prompt.index("Task 2:")


def test_handoff_excludes_the_prompt_itself(tmp_path):
    sessions, pid = _project(tmp_path)
    with sessions() as s:
        # A previously-generated handoff artifact must not be re-inlined.
        save_artifact(s, pid, "handoff-prompt.md", "Build Handoff Prompt", "PREVIOUS-PROMPT-LEAK", "dev-manager")
        project = s.get(Project, pid)
        prompt = generate_handoff(s, project)
    assert "PREVIOUS-PROMPT-LEAK" not in prompt


def test_approved_specs_shared_exclusions(tmp_path):
    """The planner and the handoff read the same spec: never the handoff, and no
    design docs left over once the product is no longer user-facing."""
    sessions, pid = _project(tmp_path)
    with sessions() as s:
        save_artifact(s, pid, "handoff-prompt.md", "Build Handoff Prompt", "OLD-PROMPT", "dev-manager")
        save_artifact(s, pid, "design-spec.md", "Design Spec", "STALE-DESIGN", "designer")
        project = s.get(Project, pid)
        project.user_facing = False
        slugs = [slug for slug, _ in approved_specs(s, project)]
        assert slugs == ["architecture.md", "product-requirements.md"]
        project.user_facing = True
        assert "design-spec.md" in [slug for slug, _ in approved_specs(s, project)]
