"""Task plan validation and creation."""

import pytest

from app.agents.schemas import TaskPlan
from app.db import make_engine, make_session_factory, Base
from app.llm.mock import MockLLM
from app.models import Task
from app.services.events import EventBus
from app.config import Settings
from app.services.planning import PlanValidationError, plan_project, validate_plan

from conftest import PLAN_TWO_TASKS

GOOD = {
    "tasks": [
        {"number": 1, "title": "a", "description": "d", "acceptance_criteria": "x", "dependencies": []},
        {"number": 2, "title": "b", "description": "d", "acceptance_criteria": "y", "dependencies": [1]},
        {"number": 3, "title": "c", "description": "d", "acceptance_criteria": "z", "dependencies": [1, 2]},
    ]
}


def test_valid_plan_passes():
    tasks = validate_plan(TaskPlan.model_validate(GOOD), max_tasks=12)
    assert len(tasks) == 3


def test_duplicate_numbers_rejected():
    plan = TaskPlan.model_validate(
        {"tasks": [GOOD["tasks"][0], dict(GOOD["tasks"][1], number=1)]}
    )
    with pytest.raises(PlanValidationError, match="duplicate"):
        validate_plan(plan, 12)


def test_unknown_dependency_rejected():
    plan = TaskPlan.model_validate({"tasks": [dict(GOOD["tasks"][0], dependencies=[99])]})
    with pytest.raises(PlanValidationError, match="unknown"):
        validate_plan(plan, 12)


def test_self_dependency_rejected():
    plan = TaskPlan.model_validate({"tasks": [dict(GOOD["tasks"][0], dependencies=[1])]})
    with pytest.raises(PlanValidationError, match="itself"):
        validate_plan(plan, 12)


def test_cycle_rejected():
    plan = TaskPlan.model_validate(
        {
            "tasks": [
                {"number": 1, "title": "a", "description": "", "acceptance_criteria": "", "dependencies": [2]},
                {"number": 2, "title": "b", "description": "", "acceptance_criteria": "", "dependencies": [1]},
            ]
        }
    )
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(plan, 12)


def test_plan_truncated_to_max():
    plan = TaskPlan.model_validate(GOOD)
    assert len(validate_plan(plan, max_tasks=2)) == 2


async def test_plan_project_persists(tmp_path):
    from app.models import Project, ProjectState

    engine = make_engine(f"sqlite:///{tmp_path}/p.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    settings = Settings(_env_file=None)
    bus = EventBus()
    with sessions() as s:
        project = Project(name="p", idea="i", state=ProjectState.PLANNING, user_facing=False)
        s.add(project)
        s.commit()
        pid = project.id

    llm = MockLLM(replies=[PLAN_TWO_TASKS])
    tasks = await plan_project(sessions, llm, bus, settings, pid)
    assert len(tasks) == 2
    with sessions() as s:
        t1 = s.query(Task).filter_by(project_id=pid, number=1).one()
        t2 = s.query(Task).filter_by(project_id=pid, number=2).one()
        assert t1.dependencies == []
        assert t2.dependencies == [1]


async def test_plan_project_rejects_bad_plan_without_persisting(tmp_path):
    from app.models import Project, ProjectState

    engine = make_engine(f"sqlite:///{tmp_path}/p.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    settings = Settings(_env_file=None)
    bus = EventBus()
    with sessions() as s:
        project = Project(name="p", idea="i", state=ProjectState.PLANNING, user_facing=False)
        s.add(project)
        s.commit()
        pid = project.id

    llm = MockLLM(replies=[{"tasks": [{"number": 1, "title": "a", "description": "", "acceptance_criteria": "", "dependencies": [7]}]}])
    with pytest.raises(PlanValidationError):
        await plan_project(sessions, llm, bus, settings, pid)
    with sessions() as s:
        assert s.query(Task).filter_by(project_id=pid).count() == 0
