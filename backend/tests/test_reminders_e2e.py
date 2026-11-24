"""D-028/D-029 end-to-end with a real decision to make: an underspecified
reminders app must be interrogated with ONE focused delivery-channel choice;
when the operator picks in-app only, the saved review and the exported build
prompt both carry that requirement, and the handoff never instructs the
coding agent to implement email delivery. A well-specified idea skips
questions entirely."""

from conftest import (
    ARCH_OK,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    STORIES,
    create_project,
    wait_for_state,
)
from app.models import ProjectState

REMINDER_IDEA = "Build a reminders app that reminds me of things I need to do."

PM_REMINDER_CANDIDATES = {
    "summary": "Personal reminders: capture tasks, surface them when due.",
    "confirmed_requirements": ["create reminders", "remind when due"],
    "assumptions": ["single user"],
    "open_questions": [
        {"id": "c1", "question": "How should due reminders reach the user?",
         "why_it_matters": "email means SMTP config + deliverability; in-app means the app must be open",
         "options": ["In-app only (no email)", "In-app + email delivery", "Email only"],
         "recommended_option": "In-app only (no email)"},
        {"id": "c2", "question": "Snooze behavior?", "why_it_matters": "small UX detail",
         "options": ["fixed 1 day", "custom"], "recommended_option": "fixed 1 day"},
    ],
    "user_stories": [{"id": "us1", "title": "see what is due", "as_a": "user",
                      "i_want": "my due reminders in the app", "so_that": "act on them",
                      "acceptance_criteria": ["due reminders listed in-app"], "mvp": True}],
    "mvp_scope": ["create/list reminders", "in-app due surfacing"],
    "out_of_scope": ["shared lists"],
    "user_facing": False,
    "genre": "productivity tool",
    "genre_expectations": ["fast capture", "clear due state"],
}

# The Architect consolidated two candidates into the one question that
# materially changes the build.
ARCH_REMINDER = {
    "feasibility": "feasible",
    "recommended_stack": ["Python", "FastAPI", "SQLite"],
    "architecture_summary": "CRUD + due-query; in-app surfacing only if chosen.",
    "risks": [],
    "challenges_for_pm": [],
    "open_questions": [
        {"id": "aq1", "question": "How should due reminders reach the user?",
         "why_it_matters": "email adds SMTP setup and deliverability risk; in-app needs no external service",
         "options": ["In-app only (no email)", "In-app + email delivery"],
         "recommended_option": "In-app only (no email)"},
    ],
}

DOC_REQ_REMINDERS = {
    "markdown": (
        "# Product Requirements\n\n"
        "## In scope\n"
        "- reminders can be created, listed, and completed\n"
        "- due reminders surface in-app (the app's due view)\n\n"
        "## Out of scope\n"
        "- email delivery — the operator chose in-app only; no delivery channel of any kind\n\n"
        "## Assumptions\n"
        "- single user, no auth\n"
    ),
    "answer_interpretations": [
        "'In-app only (no email)' -> no SMTP or delivery code path anywhere; reminders appear only in the app UI"
    ],
}

STORIES_REMINDERS = {"stories": PM_REMINDER_CANDIDATES["user_stories"]}
CRITERIA_REMINDERS = {"criteria": [
    {"id": "ac1", "story_id": "us1", "criterion": "a due reminder appears in the in-app due view at its due time"}
]}
DOC_ARCH_REMINDERS = {"markdown": "# Architecture\nFastAPI + SQLite; due-query endpoint; the app surfaces due reminders in its own UI."}
PLAN_REMINDERS = {
    "tasks": [
        {"number": 1, "title": "Reminders CRUD + in-app due view",
         "description": "create/list/complete endpoints and a due view query",
         "acceptance_criteria": "due reminder visible in the due view at due time",
         "dependencies": [], "category": "feature"},
        {"number": 2, "title": "Tests + README", "description": "pytest + run docs",
         "acceptance_criteria": "suite green", "dependencies": [1], "category": "test"},
    ]
}


def test_underspecified_reminders_get_one_structured_choice(env):
    env.llm.replies.extend([PM_REMINDER_CANDIDATES, ARCH_REMINDER])
    pid = create_project(env, REMINDER_IDEA)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    pending = [m for m in messages if m["kind"] == "question" and not m["answered"]]
    assert len(pending) == 1  # consolidated: at most one question here
    meta = pending[0]["meta"]
    assert meta["options"] == ["In-app only (no email)", "In-app + email delivery"]
    assert meta["recommended_option"] == "In-app only (no email)"
    assert meta["why_it_matters"]


def test_chosen_decision_reaches_review_and_the_build_prompt_carries_it(env):
    env.llm.replies.extend([PM_REMINDER_CANDIDATES, ARCH_REMINDER])
    pid = create_project(env, REMINDER_IDEA)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    question = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                if m["kind"] == "question"][0]

    env.llm.replies.extend(
        [DOC_REQ_REMINDERS, STORIES_REMINDERS, CRITERIA_REMINDERS, DOC_ARCH_REMINDERS, PLAN_REMINDERS]
    )
    resp = env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [{"question_id": question["id"], "answer": "In-app only (no email)"}]},
    )
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    # The review shows the decision, its interpretation, and the outcome in scope.
    review = env.client.get(f"/api/projects/{pid}/review").json()
    assert any("In-app only (no email)" in d for d in review["decisions"])
    assert any("no SMTP" in i for i in review["interpretations"])
    assert any("in-app" in s for s in review["in_scope"])
    assert any("email delivery" in s for s in review["out_of_scope"])
    assert review["acceptance_criteria"][0]["criterion"].startswith("a due reminder appears in the in-app")

    # Approve with the reviewed map; the exported prompt carries the decision.
    reviewed = {
        a["slug"]: a["current_version"]
        for a in env.client.get(f"/api/projects/{pid}/artifacts").json()
        if a["slug"] != "handoff-prompt.md"
    }
    resp = env.client.post(f"/api/projects/{pid}/approval",
                           json={"decision": "APPROVED", "artifact_versions": reviewed})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    handoff = env.client.get(f"/api/projects/{pid}/handoff").text
    assert "due reminders surface in-app" in handoff
    assert "no delivery channel of any kind" in handoff  # the out-of-scope decision, verbatim
    assert "In-app only (no email)" in handoff  # the recorded decision itself is inlined
    task_section = handoff.split("## Task plan", 1)[1]
    assert "email" not in task_section.lower()  # no task instructs building email delivery


def test_well_specified_idea_goes_straight_to_approval(env):
    env.llm.replies.extend(
        [dict(PM_REMINDER_CANDIDATES, open_questions=[]), ARCH_OK,
         DOC_REQ, STORIES, CRITERIA, DOC_ARCH]
    )
    pid = create_project(env,
                        "A reminders app: reminders are created in the app and surface only in the app's "
                        "due view, single user, no email, no auth.")
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    assert not [m for m in messages if m["kind"] == "question"]
    assert not [m for m in messages if m["kind"] == "question_candidate"]
