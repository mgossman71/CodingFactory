"""D-028: fewer, better questions.

The PM proposes CANDIDATE questions (recorded, never operator-facing); the
Architect consolidates them into ONE ranked batch of at most three, with
structured detail (why it matters, options, recommendation). The gate is
iteration-scoped; partial submissions re-arm it; stale / cross-iteration /
cross-project question ids are rejected; resume between the two agent turns
adds no duplicate calls or questions; operator decisions survive into later
artifact generation.
"""

from sqlalchemy import select

from app.models import AgentMessage, Project, ProjectState

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

PM_FOUR_CANDIDATES = dict(
    PM_WITH_QUESTIONS,
    open_questions=[
        {"id": f"c{i}", "question": f"Candidate {i}?", "why_it_matters": f"because {i}",
         "options": ["x", "y"], "recommended_option": "x"}
        for i in range(4)
    ],
)

ARCH_TWO_QUESTIONS = dict(
    ARCH_OK,
    open_questions=[
        {
            "id": "aq1",
            "question": "Delivery channel for due-date reminders?",
            "why_it_matters": "email adds SMTP setup to the MVP",
            "options": ["In-app only (no email)", "In-app + email"],
            "recommended_option": "In-app only (no email)",
        },
        {"id": "aq2", "question": "One device or many?", "why_it_matters": "data model"},
    ],
)

ARCH_FOUR_QUESTIONS = dict(
    ARCH_OK,
    open_questions=[
        {"id": f"aq{i}", "question": f"Consolidated question {i}?", "why_it_matters": "w"}
        for i in range(1, 5)
    ],
)

ARTIFACT_REPLIES = [DOC_REQ, STORIES, CRITERIA, DOC_ARCH]


# ---------- consolidation + persistence ----------


def test_pm_candidates_recorded_but_not_operator_facing(env):
    env.llm.replies.extend([PM_FOUR_CANDIDATES, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    candidates = [m for m in messages if m["kind"] == "question_candidate"]
    questions = [m for m in messages if m["kind"] == "question"]
    assert len(candidates) == 4 and all(m["agent"] == "product-manager" for m in candidates)
    assert len(questions) == 2 and all(m["agent"] == "architect" for m in questions)

    # Structured detail travels with the final batch (ranked = persisted order).
    assert questions[0]["content"] == "Delivery channel for due-date reminders?"
    meta = questions[0]["meta"]
    assert meta["why_it_matters"] == "email adds SMTP setup to the MVP"
    assert meta["options"] == ["In-app only (no email)", "In-app + email"]
    assert meta["recommended_option"] == "In-app only (no email)"
    assert questions[1]["meta"]["options"] == []  # free-text question

    # Candidates carry structured detail too, and the gate only counts the batch.
    assert candidates[0]["meta"]["recommended_option"] == "x"
    pending = [m for m in messages if m["kind"] == "question" and not m["answered"]]
    assert len(pending) == 2

    # The architect call actually SAW the candidates (consolidation input).
    arch_call = next(
        c for c in env.llm.calls if any("PM CANDIDATE QUESTIONS" in m["content"] for m in c)
    )
    assert "Candidate 0?" in arch_call[1]["content"]  # [0]=system, [1]=task, [2]=schema hint


def test_architect_batch_trimmed_to_three_ranked(env):
    """More than three consolidated questions: keep the top three of the
    ranked list, drop the tail — never silently ask all four."""
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_FOUR_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    assert [q["content"] for q in questions] == [f"Consolidated question {i}?" for i in (1, 2, 3)]


def test_architect_sees_prior_decisions(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    env.llm.replies.extend(ARTIFACT_REPLIES + [PLAN_TWO_TASKS])
    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    env.client.post(f"/api/projects/{pid}/answers", json={"answers": [
        {"question_id": questions[0]["id"], "answer": "In-app only (no email)"},
        {"question_id": questions[1]["id"], "answer": "One device"},
    ]})
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    env.client.post(f"/api/projects/{pid}/approval", json={"decision": "APPROVED"})
    wait_for_state(env, pid, {ProjectState.COMPLETED}, timeout=30)

    # A change request's architect call carries the earlier decision explicitly.
    env.llm.replies.extend([dict(PM_WITH_QUESTIONS, open_questions=[]), ARCH_OK, *ARTIFACT_REPLIES])
    env.client.post(f"/api/projects/{pid}/feedback", json={"content": "Add overdue reporting."})
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL}, timeout=30)
    arch_calls = [c for c in env.llm.calls if any(m["content"].startswith("You are the Software Architect") for m in c)]
    assert any("OPERATOR DECISIONS" in c[1]["content"] and "In-app only (no email)" in c[1]["content"]
               for c in arch_calls)


# ---------- answering ----------


def test_partial_answers_keep_the_gate_until_all_answered(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    assert len(questions) == 2

    resp = env.client.post(f"/api/projects/{pid}/answers",
                           json={"answers": [{"question_id": questions[0]["id"], "answer": "In-app only (no email)"}]})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})  # gate re-armed
    still = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
             if m["kind"] == "question" and not m["answered"]]
    assert [m["id"] for m in still] == [questions[1]["id"]]

    env.llm.replies.extend(ARTIFACT_REPLIES)
    resp = env.client.post(f"/api/projects/{pid}/answers",
                           json={"answers": [{"question_id": questions[1]["id"], "answer": "One device"}]})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    answers = [m for m in env.client.get(f"/api/projects/{pid}/messages").json() if m["kind"] == "answer"]
    # Persisted question text is what gets recorded, joined to the answer.
    assert answers[0]["content"].startswith("Q: Delivery channel for due-date reminders?")
    assert "A: In-app only (no email)" in answers[0]["content"]


def test_freeform_note_still_allowed(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    env.llm.replies.extend(ARTIFACT_REPLIES)
    resp = env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [
            {"question_id": questions[0]["id"], "answer": "In-app only (no email)"},
            {"question_id": questions[1]["id"], "answer": "One device"},
            {"question": "additional requirement", "answer": "export to CSV"},
        ]},
    )
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    answers = [m for m in env.client.get(f"/api/projects/{pid}/messages").json() if m["kind"] == "answer"]
    assert any(m["content"] == "Q: additional requirement\nA: export to CSV" for m in answers)
    # The note reaches artifact generation through the discussion.
    assert any("export to CSV" in m["content"] for c in env.llm.calls for m in c)


def test_answer_rejects_stale_question_id(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    resp = env.client.post(f"/api/projects/{pid}/answers",
                           json={"answers": [{"question_id": questions[0]["id"], "answer": "x"}]})
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    resp = env.client.post(f"/api/projects/{pid}/answers",
                           json={"answers": [{"question_id": questions[0]["id"], "answer": "changed my mind"}]})
    assert resp.status_code == 422
    assert "not an unanswered question" in resp.json()["detail"]


def test_answer_rejects_question_from_another_project(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid_a = create_project(env)
    wait_for_state(env, pid_a, {ProjectState.AWAITING_INPUT})
    q_a = [m for m in env.client.get(f"/api/projects/{pid_a}/messages").json() if m["kind"] == "question"][0]

    pid_b = create_project(env, idea="Some other tool entirely, please.")
    wait_for_state(env, pid_b, {ProjectState.FAILED})  # no replies queued
    with env.sessions() as s:
        s.get(Project, pid_b).state = ProjectState.AWAITING_INPUT
        s.commit()

    resp = env.client.post(f"/api/projects/{pid_b}/answers",
                           json={"answers": [{"question_id": q_a["id"], "answer": "nope"}]})
    assert resp.status_code == 422
    # A's gate is untouched.
    assert not [m for m in env.client.get(f"/api/projects/{pid_a}/messages").json() if m["kind"] == "answer"]


def test_answer_rejects_question_from_a_previous_iteration(env):
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.FAILED})
    with env.sessions() as s:  # stale question from iteration 0, project now at iteration 1
        stale = AgentMessage(project_id=pid, agent="architect", kind="question",
                             content="stale question", seq=1, iteration=0)
        s.add(stale)
        s.flush()
        stale_id = stale.id
        project = s.get(Project, pid)
        project.state = ProjectState.AWAITING_INPUT
        project.iteration = 1
        s.commit()

    resp = env.client.post(f"/api/projects/{pid}/answers",
                           json={"answers": [{"question_id": stale_id, "answer": "late answer"}]})
    assert resp.status_code == 422


def test_legacy_plain_text_question_is_answerable(env):
    """Pre-migration question rows have NULL meta: the gate, the answer flow,
    and the recorded Q/A all work on them (the UI renders them as plain text)."""
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    with env.sessions() as s:
        for m in s.execute(
            select(AgentMessage).where(AgentMessage.project_id == pid, AgentMessage.kind == "question")
        ).scalars():
            m.meta = None
        s.commit()

    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    assert all(m["meta"] is None for m in questions)
    env.llm.replies.extend(ARTIFACT_REPLIES)
    resp = env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [
            {"question_id": questions[0]["id"], "answer": "a"},
            {"question_id": questions[1]["id"], "answer": "b"},
        ]},
    )
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})


# ---------- resume between the two agent turns ----------


def test_resume_between_pm_and_architect_adds_no_duplicates(env):
    """Crash after the PM commit: resume re-runs ONLY the Architect — the PM
    call is not repeated and no question batch is duplicated."""
    env.llm.replies.extend([PM_WITH_QUESTIONS])  # Architect call then exhausts -> FAILED
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.FAILED})

    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    assert any(m["agent"] == "product-manager" and m["kind"] == "message" for m in messages)
    assert [m for m in messages if m["kind"] == "question"] == []
    pm_calls = [c for c in env.llm.calls if any("Produce your analysis now" in m["content"] for m in c)]
    assert len(pm_calls) == 1

    env.llm.replies.extend([ARCH_TWO_QUESTIONS])
    resp = env.client.post(f"/api/projects/{pid}/resume")
    assert resp.status_code == 200
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    messages = env.client.get(f"/api/projects/{pid}/messages").json()
    questions = [m for m in messages if m["kind"] == "question"]
    assert len(questions) == 2  # exactly one consolidated batch
    pm_calls = [c for c in env.llm.calls if any("Produce your analysis now" in m["content"] for m in c)]
    assert len(pm_calls) == 1  # still exactly one PM call


def test_decisions_carry_into_artifact_prompts_beyond_the_window(env):
    """Every artifact-generation prompt receives the full decision record,
    outside the 30-message discussion window."""
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    questions = [m for m in env.client.get(f"/api/projects/{pid}/messages").json()
                 if m["kind"] == "question"]
    env.llm.replies.extend(ARTIFACT_REPLIES)
    env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [
            {"question_id": questions[0]["id"], "answer": "In-app only (no email)"},
            {"question_id": questions[1]["id"], "answer": "One device"},
        ]},
    )
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    artifact_calls = [
        c for c in env.llm.calls
        if any(s in c[1]["content"] for s in ("final product requirements", "final user stories",
                                              "final acceptance criteria", "final technical architecture"))
    ]
    assert artifact_calls
    for call in artifact_calls:
        assert "OPERATOR DECISIONS" in call[1]["content"]
        assert "In-app only (no email)" in call[1]["content"]
