"""D-023: approval freezes the exact artifact versions it approved.

The finding (P1): approval recorded only the decision; the planner and the
handoff generator each read artifact heads independently. Approving a spec,
editing it while the planner was running, then downloading the handoff
produced a prompt containing the unapproved edit next to a plan built from
the earlier version. Planning, handoff generation, and retries must all read
the approval snapshot.
"""

from sqlalchemy import select

from app.db import Base, make_engine, make_session_factory
from app.models import (
    Approval,
    ApprovalDecision,
    Artifact,
    Project,
    ProjectState,
)
from app.services.discovery import save_artifact
from app.services.handoff import approval_snapshot, approved_specs, spec_snapshot

from conftest import (
    ARCH_OK,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    PLAN_TWO_TASKS,
    PM_WITH_QUESTIONS,
    STORIES,
    create_project,
    wait_for_state,
)

PM_NO_QS = dict(PM_WITH_QUESTIONS, open_questions=[])


def _drive_to_approval(env) -> str:
    env.llm.replies.extend([PM_NO_QS, ARCH_OK, DOC_REQ, STORIES, CRITERIA, DOC_ARCH])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    return pid


# ---- unit: the snapshot functions ----


def test_spec_snapshot_freezes_current_versions_excluding_the_handoff(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/snap.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = Project(name="W", idea="i", state=ProjectState.AWAITING_APPROVAL)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "PR", "REQ-V1", "product-manager")
        save_artifact(s, p.id, "architecture.md", "ARCH", "ARCH-V2", "architect")
        save_artifact(s, p.id, "architecture.md", "ARCH", "ARCH-V3", "architect")
        save_artifact(s, p.id, "handoff-prompt.md", "Handoff", "OLD-PROMPT", "dev-manager")
        s.commit()
        pid = p.id

    with sessions() as s:
        project = s.get(Project, pid)
        snap = spec_snapshot(project)
        assert snap == {"product-requirements.md": 1, "architecture.md": 2}
        assert "handoff-prompt.md" not in snap  # the prompt itself is never a spec


def test_approved_specs_pin_pinned_versions_and_exclude_later_docs(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/pin.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = Project(name="W", idea="i", state=ProjectState.PLANNING)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "PR", "APPROVED-TEXT", "product-manager")
        s.add(
            Approval(
                project_id=p.id, decision=ApprovalDecision.APPROVED, iteration=0,
                artifact_versions={"product-requirements.md": 1},
            )
        )
        s.commit()
        pid = p.id

    with sessions() as s:  # a later edit AND a brand-new artifact appear post-approval
        save_artifact(s, pid, "product-requirements.md", "PR", "LATER-EDIT", "user")
        save_artifact(s, pid, "brand-new.md", "New doc", "POST-APPROVAL-DOC", "product-manager")
        s.commit()

    with sessions() as s:
        project = s.get(Project, pid)
        assert approval_snapshot(s, project) == {"product-requirements.md": 1}
        assert approved_specs(s, project) == [("product-requirements.md", "APPROVED-TEXT")]


def test_no_snapshot_falls_back_to_current_versions(tmp_path):
    """Legacy rows and pre-gate callers behave exactly as before D-023."""
    engine = make_engine(f"sqlite:///{tmp_path}/legacy.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = Project(name="W", idea="i", state=ProjectState.PLANNING)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "PR", "CURRENT-TEXT", "product-manager")
        s.add(Approval(project_id=p.id, decision=ApprovalDecision.APPROVED, iteration=0))
        s.commit()
        pid = p.id
    with sessions() as s:
        assert approved_specs(s, s.get(Project, pid)) == [("product-requirements.md", "CURRENT-TEXT")]


# ---- end-to-end: the repro from the finding ----


def test_approval_records_the_snapshot(env):
    pid = _drive_to_approval(env)
    env.llm.replies.extend([PLAN_TWO_TASKS])
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    with env.sessions() as s:
        approval = s.execute(
            select(Approval).where(Approval.project_id == pid, Approval.decision == ApprovalDecision.APPROVED)
        ).scalar_one()
        assert approval.artifact_versions == {
            "product-requirements.md": 1,
            "architecture.md": 1,
            "user-stories.json": 1,
            "acceptance-criteria.json": 1,
            "open-questions.md": 1,
        }
        assert "handoff-prompt.md" not in approval.artifact_versions


def test_edit_during_planning_never_reaches_the_handoff(env):
    pid = _drive_to_approval(env)

    def edit_during_planning(_messages):
        """The finding's repro, made deterministic: the user edits the spec
        while the Dev Manager call is in flight."""
        with env.sessions() as s:
            save_artifact(s, pid, "product-requirements.md", "Product Requirements", "UNAPPROVED EDIT", "user")
            s.commit()
        return PLAN_TWO_TASKS

    env.llm.replies.append(edit_during_planning)
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    handoff = env.client.get(f"/api/projects/{pid}/handoff").text
    assert "UNAPPROVED EDIT" not in handoff
    assert "Track maintenance events and schedules" in handoff  # the approved text

    with env.sessions() as s:  # the artifact head moved anyway — the freeze is at use time
        artifact = s.execute(
            select(Artifact).where(Artifact.project_id == pid, Artifact.slug == "product-requirements.md")
        ).scalar_one()
        assert artifact.current_version == 2


def test_retry_after_failure_reuses_the_snapshot(env):
    """A plan failure + Resume (D-022) must replan from the approved snapshot,
    not from an edit that landed after approval."""
    pid = _drive_to_approval(env)
    # No plan reply queued -> planning fails.
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.FAILED}, timeout=30)

    with env.sessions() as s:
        save_artifact(s, pid, "product-requirements.md", "Product Requirements", "UNAPPROVED EDIT", "user")
        s.commit()

    env.llm.replies.extend([PLAN_TWO_TASKS])
    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    handoff = env.client.get(f"/api/projects/{pid}/handoff").text
    assert "UNAPPROVED EDIT" not in handoff
    assert "Track maintenance events and schedules" in handoff

    # The Dev Manager prompt itself carried only the approved text.
    plan_call = env.llm.calls[-1]
    assert "Track maintenance events and schedules" in plan_call[1]["content"]
    assert "UNAPPROVED EDIT" not in plan_call[1]["content"]
