"""D-029: the compact approval review + the reviewed-version map handshake.

The review is sliced from the same versioned artifacts approval is about to
freeze (saved edits reflected, decisions/interpretations surfaced, acceptance
criteria shown verbatim, full sources always available). The approval
endpoint accepts the version map the UI reviewed: a stale map is a 409 that
records nothing; the map is optional for older callers.
"""

import pytest
from sqlalchemy import select

from app.models import Approval, ApprovalDecision, Project, ProjectState
from app.services.review import build_review, split_sections

from conftest import (
    ARCH_OK,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    PLAN_TWO_TASKS,
    PM_WITH_QUESTIONS,
    ARCH_WITH_QUESTION,
    STORIES,
    create_project,
    wait_for_state,
)

LEGACY_REQ = """# Everything about the tool

## Background
- prose era document, no standard headings

## Notes for whoever builds it
- it should work
"""

MODERN_REQ = """# Product Requirements

## In scope
- record maintenance events with date and mileage
- due-date listing surfaces in-app

## Out of scope
- multi-user accounts
- email delivery (operator chose in-app only)

## Assumptions
- single user, no auth (labelled: assumption)

## Success criteria
- a week of real entries without friction
"""


# ---------- the parser ----------


def test_split_sections_slices_bullets_and_folds_continuations():
    sections = dict(split_sections(MODERN_REQ))
    assert "record maintenance events with date and mileage" in sections["in scope"]
    assert sections["out of scope"] == ["multi-user accounts",
                                        "email delivery (operator chose in-app only)"]
    assert any("single user" in a for a in sections["assumptions"])
    folded = split_sections("## Decisions\n- Q: something?\nA: yes\n- next")
    assert dict(folded)["decisions"] == ["Q: something? A: yes", "next"]


def test_split_sections_ignores_prose_outside_bullets():
    assert dict(split_sections(LEGACY_REQ)).get("in scope") is None


def test_split_sections_captures_paragraphs_and_distinguishes_missing():
    doc = (
        "## Out of scope\n"
        "Email delivery is excluded.\n"
        "Wrapped sentence continues here.\n"
        "\n"
        "Second paragraph under the same heading.\n"
        "\n"
        "## In scope\n"
    )
    sections = dict(split_sections(doc))
    assert sections["out of scope"] == [
        "Email delivery is excluded. Wrapped sentence continues here.",
        "Second paragraph under the same heading.",
    ]
    assert sections["in scope"] == []  # present but empty — not the same as missing
    assert "assumptions" not in sections  # absent heading entirely


# ---------- the review payload ----------


def test_build_review_surfaces_every_section(tmp_path):
    from app.db import Base, make_engine, make_session_factory
    from app.models import Project as P
    from app.services.discovery import save_artifact

    engine = make_engine(f"sqlite:///{tmp_path}/review.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = P(name="W", idea="i", state=ProjectState.AWAITING_APPROVAL)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "PR", MODERN_REQ, "product-manager")
        save_artifact(s, p.id, "open-questions.md", "OQ",
                      "# Open Questions & Assumptions\n\n## Asked and answered\n\n"
                      "- Q: Delivery channel? A: In-app only (no email)\n\n"
                      "## Interpreted answers (verify at approval)\n\n"
                      "- 'in-app only' -> no SMTP code path at all\n", "system")
        save_artifact(s, p.id, "acceptance-criteria.json", "AC",
                      '[{"id": "ac1", "criterion": "reminder appears in-app; no email sent"}]', "product-manager")
        save_artifact(s, p.id, "handoff-prompt.md", "H", "should not be a source", "dev-manager")
        s.commit()
        review = build_review(s, p)

    assert any("maintenance events" in x for x in review["in_scope"])
    assert any("multi-user" in x for x in review["out_of_scope"])
    assert any("single user" in x for x in review["assumptions"])
    assert review["decisions"] == ["Q: Delivery channel? A: In-app only (no email)"]
    assert review["decision_history"] is None  # no history section in this doc
    assert review["interpretations"] == ["'in-app only' -> no SMTP code path at all"]
    assert review["acceptance_criteria"] == [
        {"id": "ac1", "criterion": "reminder appears in-app; no email sent"}
    ]
    slugs = {src["slug"] for src in review["sources"]}
    assert {"product-requirements.md", "open-questions.md", "acceptance-criteria.json"} <= slugs
    assert "handoff-prompt.md" not in slugs


def test_build_review_legacy_document_falls_back_to_full_source(tmp_path):
    from app.db import Base, make_engine, make_session_factory
    from app.models import Project as P
    from app.services.discovery import save_artifact

    engine = make_engine(f"sqlite:///{tmp_path}/legacy.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = P(name="W", idea="i", state=ProjectState.AWAITING_APPROVAL)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "PR", LEGACY_REQ, "product-manager")
        s.commit()
        review = build_review(s, p)

    # Unrecognized headings: the sections are simply absent (None), but the
    # whole source is listed so the operator can read it in full.
    assert review["in_scope"] is None
    assert review["out_of_scope"] is None
    assert review["assumptions"] is None
    assert [src["slug"] for src in review["sources"]] == ["product-requirements.md"]


def test_build_review_separates_current_and_superseded_decisions(tmp_path):
    from app.db import Base, make_engine, make_session_factory
    from app.models import Project as P
    from app.services.discovery import save_artifact

    engine = make_engine(f"sqlite:///{tmp_path}/hist.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        p = P(name="W", idea="i", state=ProjectState.AWAITING_APPROVAL)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "open-questions.md", "OQ",
                      "# Open Questions & Assumptions\n\n"
                      "## Asked and answered\n\n"
                      "- Q: Delivery? A: Email delivery\n\n"
                      "## Prior decisions (history — a later change request may have revised these)\n\n"
                      "- Q: Delivery? A: In-app only (no email)\n", "system")
        s.commit()
        review = build_review(s, p)

    assert review["decisions"] == ["Q: Delivery? A: Email delivery"]
    assert review["decision_history"] == ["Q: Delivery? A: In-app only (no email)"]


def test_review_reads_content_and_versions_in_one_query(tmp_path):
    """P1 regression: an edit landing BETWEEN the content read and the version
    read used to produce a review showing v1 text while its source map said v2
    — and approval would accept that map, freezing unseen text. Content and
    versions now come from the SAME rows, so the review's artifact query runs
    exactly once; if separate reads creep back in, this fails."""
    from sqlalchemy import event

    from app.db import Base, make_engine, make_session_factory
    from app.models import Project as P
    from app.services.discovery import save_artifact

    engine = make_engine(f"sqlite:///{tmp_path}/atomic.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    artifact_queries: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        if "artifact_versions" in statement.lower() and "select" in statement.lower():
            artifact_queries.append(statement)

    with sessions() as s:
        p = P(name="W", idea="i", state=ProjectState.AWAITING_APPROVAL)
        s.add(p)
        s.flush()
        save_artifact(s, p.id, "product-requirements.md", "PR", MODERN_REQ, "product-manager")
        save_artifact(s, p.id, "acceptance-criteria.json", "AC",
                      '[{"id": "ac1", "criterion": "x"}]', "product-manager")
        s.commit()
        build_review(s, p)

    assert len(artifact_queries) == 1, (
        f"review must read versions+content from one row set, got {len(artifact_queries)} queries"
    )


# ---------- end-to-end: the gate, edits, and the version map ----------


def _drive_to_approval_with_answers(env) -> str:
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_WITH_QUESTION])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    q = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
         if m["kind"] == "question"][0]
    env.llm.replies.extend([DOC_REQ, STORIES, CRITERIA, DOC_ARCH])
    resp = env.client.post(f"/api/projects/{pid}/answers",
                           json={"answers": [{"question_id": q["id"], "answer": "In-app only (no email)"}]})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    return pid


def test_review_reflects_saved_edits_and_surfaces_decisions(env):
    pid = _drive_to_approval_with_answers(env)

    review = env.client.get(f"/api/projects/{pid}/review").json()
    assert any("In-app only (no email)" in d for d in review["decisions"])
    assert review["interpretations"]  # DOC_REQ carries one
    assert [c["criterion"] for c in review["acceptance_criteria"]] == ["event saved with date"]

    resp = env.client.put(
        f"/api/projects/{pid}/artifacts/product-requirements.md",
        json={"content": MODERN_REQ},
    )
    assert resp.status_code == 200
    review = env.client.get(f"/api/projects/{pid}/review").json()
    assert any("mileage" in x for x in review["in_scope"])
    assert any("email delivery" in x for x in review["out_of_scope"])
    versions = {src["slug"]: src["version"] for src in review["sources"]}
    assert versions["product-requirements.md"] == 2


def test_stale_version_map_is_rejected_and_records_nothing(env):
    pid = _drive_to_approval_with_answers(env)
    reviewed = {
        a["slug"]: a["current_version"]
        for a in env.client.get(f"/api/projects/{pid}/artifacts").json()
        if a["slug"] != "handoff-prompt.md"
    }
    # An edit lands between rendering the review and clicking Approve.
    env.client.put(f"/api/projects/{pid}/artifacts/architecture.md",
                   json={"content": "# Architecture\nQuietly different in here."})

    env.llm.replies.extend([PLAN_TWO_TASKS])
    resp = env.client.post(f"/api/projects/{pid}/approval",
                           json={"decision": "APPROVED", "artifact_versions": reviewed})
    assert resp.status_code == 409
    assert "changed since you reviewed" in resp.json()["detail"]

    with env.sessions() as s:
        approvals = s.execute(select(Approval).where(Approval.project_id == pid)).scalars().all()
        assert approvals == []  # nothing recorded
        assert s.get(Project, pid).state is ProjectState.AWAITING_APPROVAL

    # A fresh click on the reloaded review approves the CURRENT versions.
    current = {
        a["slug"]: a["current_version"]
        for a in env.client.get(f"/api/projects/{pid}/artifacts").json()
        if a["slug"] != "handoff-prompt.md"
    }
    resp = env.client.post(f"/api/projects/{pid}/approval",
                           json={"decision": "APPROVED", "artifact_versions": current})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)
    with env.sessions() as s:
        approval = s.execute(
            select(Approval).where(Approval.project_id == pid,
                                   Approval.decision == ApprovalDecision.APPROVED)
        ).scalar_one()
        assert approval.artifact_versions == current


def test_version_map_optional_for_existing_callers(env):
    pid = _drive_to_approval_with_answers(env)
    env.llm.replies.extend([PLAN_TWO_TASKS])
    resp = env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)


def test_revision_request_needs_no_map_and_gives_a_fresh_review(env):
    pid = _drive_to_approval_with_answers(env)
    env.llm.replies.extend([DOC_REQ, STORIES, CRITERIA, DOC_ARCH])
    resp = env.client.post(f"/api/projects/{pid}/approval",
                           json={"decision": "REVISION_REQUESTED", "comment": "Add mileage tracking."})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    review = env.client.get(f"/api/projects/{pid}/review").json()
    assert review["sources"]  # review regenerated over the revised artifacts
