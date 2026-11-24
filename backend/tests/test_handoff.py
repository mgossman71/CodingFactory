"""generate_handoff assembles a self-contained build prompt from the approved
specs + the task plan. Deterministic — no LLM call."""

from app.db import Base, make_engine, make_session_factory
from app.models import Project, ProjectState, Task
from app.services.discovery import save_artifact
from app.services.handoff import approved_specs, generate_handoff, topological_order


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


# ---------- export ordering (D-025) ----------


def test_topological_order_puts_dependencies_first_ignoring_numbers():
    """A valid plan can number things against the grain: task 1 depending on 2
    must still export 2 first."""
    t1 = Task(number=1, title="Dependent", dependencies=[2])
    t2 = Task(number=2, title="Foundation", dependencies=[])
    assert [t.number for t in topological_order([t1, t2])] == [2, 1]


def test_topological_order_breaks_ties_by_number():
    a = Task(number=3, title="c", dependencies=[1])
    b = Task(number=1, title="a", dependencies=[])
    c = Task(number=2, title="b", dependencies=[])
    assert [t.number for t in topological_order([a, b, c])] == [1, 2, 3]


def test_topological_order_keeps_cycle_stragglers():
    """Validation rejects cycles, but the export must never silently drop a
    task even if one slipped through."""
    a = Task(number=1, title="a", dependencies=[2])
    b = Task(number=2, title="b", dependencies=[1])
    assert [t.number for t in topological_order([a, b])] == [1, 2]


def test_handoff_lists_tasks_in_dependency_order(tmp_path):
    """The finding (P2): sorting the export by task number told the builder to
    start with task 1 even though task 1 depended on task 2."""
    engine = make_engine(f"sqlite:///{tmp_path}/order.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = Project(name="W", idea="i", state=ProjectState.PLANNING)
        s.add(p)
        s.flush()
        s.add(Task(project_id=p.id, number=1, title="Dependent feature", description="needs 2 first",
                   acceptance_criteria="works", dependencies=[2]))
        s.add(Task(project_id=p.id, number=2, title="Foundation", description="base layer",
                   acceptance_criteria="boots", dependencies=[]))
        s.commit()
        pid = p.id
    with sessions() as s:
        prompt = generate_handoff(s, s.get(Project, pid))
    assert "implement in this order" in prompt
    assert prompt.index("Task 2: Foundation") < prompt.index("Task 1: Dependent feature")
