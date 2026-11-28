"""D-047: a conditional follow-up Q&A round.

A short/vague idea got ONE batch of at most three questions and nothing else —
second-order unknowns (opened BY the operator's answers) could never be asked,
so they degraded into assumption bullets nobody verifies. Now the PM ⇄ Architect
pair re-runs once after answers (budget: settings.max_discovery_rounds) with a
narrow mandate: ask ONLY what the answers themselves opened, at most two more
questions, and an empty batch falls straight through to artifact generation.
Round detection is derived from persisted agent turns, so crash-resume stays
idempotent; the follow-up Architect sees only its own round's candidates.
"""

import pytest

from app.models import ProjectState

from conftest import (
    ARCH_OK,
    CRITERIA,
    DOC_ARCH,
    DOC_REQ,
    PM_WITH_QUESTIONS,
    STORIES,
    create_project,
    wait_for_state,
)

ARTIFACT_REPLIES = [DOC_REQ, STORIES, CRITERIA, DOC_ARCH]

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

ARCH_FOLLOWUP_ONE = dict(
    ARCH_OK,
    open_questions=[
        {
            "id": "fq1",
            "question": "With many devices, must edits sync across them?",
            "why_it_matters": "'many devices' opened a sync/conflict decision that "
            "changes the data model and the API surface",
            "options": ["Manual re-entry per device", "Cloud sync with conflict merge"],
            "recommended_option": "Manual re-entry per device",
        }
    ],
)

ARCH_FOUR_QUESTIONS = dict(
    ARCH_OK,
    open_questions=[
        {"id": f"fq{i}", "question": f"Follow-up question {i}?", "why_it_matters": "w"}
        for i in range(1, 5)
    ],
)

PM_NO_CANDIDATES = dict(PM_WITH_QUESTIONS, open_questions=[])


def _rounds2(fn):
    return pytest.mark.parametrize("settings", [2], indirect=True)(fn)


def _messages(env, pid, kind=None):
    msgs = env.client.get(f"/api/projects/{pid}/messages").json()
    return [m for m in msgs if kind is None or m["kind"] == kind]


def _answer(env, pid, answers):
    resp = env.client.post(
        f"/api/projects/{pid}/answers",
        json={"answers": [{"question_id": qid, "answer": a} for qid, a in answers]},
    )
    assert resp.status_code == 200, resp.text


def _pm_calls(env):
    return [c for c in env.llm.calls if any("Produce your analysis now" in m["content"] for m in c)]


@_rounds2
def test_followup_round_asks_only_what_the_answers_opened(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    round1 = _messages(env, pid, "question")
    assert len(round1) == 2

    # Answering opens a new material unknown ("many devices" -> sync?).
    env.llm.replies.extend([PM_NO_CANDIDATES, ARCH_FOLLOWUP_ONE])
    _answer(env, pid, [(q["id"], "Many devices") for q in round1])
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    pending = [m for m in _messages(env, pid, "question") if not m["answered"]]
    assert [m["content"] for m in pending] == ["With many devices, must edits sync across them?"]
    assert pending[0]["meta"]["recommended_option"] == "Manual re-entry per device"

    # The round-2 PM/Architect calls carry the FOLLOW-UP ROUND mandate.
    pm2 = _pm_calls(env)[-1][1]["content"]
    assert "FOLLOW-UP ROUND" in pm2
    arch2 = next(
        c for c in reversed(env.llm.calls)
        if any(m["content"].startswith("You are the Software Architect") for m in c)
    )[1]["content"]
    assert "FOLLOW-UP ROUND" in arch2
    # ...and the round-2 Architect sees ONLY this round's candidates (PM filed
    # none) — round 1's candidate must not resurface for re-asking.
    cands_block = arch2.split("PM CANDIDATE QUESTIONS", 1)[1].split("OPERATOR DECISIONS", 1)[0]
    assert "(none)" in cands_block
    assert "email reminders be included in MVP" not in cands_block
    assert "at most 2" in cands_block  # follow-up consolidation cap reaches the prompt

    # Answering the follow-up exhausts the round budget -> artifacts, no third pass.
    env.llm.replies.extend(ARTIFACT_REPLIES)
    _answer(env, pid, [(pending[0]["id"], "Manual re-entry per device")])
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    assert len(_pm_calls(env)) == 2
    assert len(_messages(env, pid, "question")) == 3  # 2 + 1, no third batch
    assert len(_messages(env, pid, "message")) == 4  # PM+Arch twice


@_rounds2
def test_followup_round_with_no_new_questions_goes_straight_to_artifacts(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    round1 = _messages(env, pid, "question")

    env.llm.replies.extend([PM_NO_CANDIDATES, ARCH_OK, *ARTIFACT_REPLIES])
    _answer(env, pid, [(q["id"], "In-app only (no email)") for q in round1])
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    assert len(_pm_calls(env)) == 2  # follow-up pass ran but asked nothing
    assert len(_messages(env, pid, "question")) == 2  # no new batch was asked


@_rounds2
def test_followup_batch_trimmed_to_two(env):
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    round1 = _messages(env, pid, "question")

    env.llm.replies.extend([PM_NO_CANDIDATES, ARCH_FOUR_QUESTIONS])
    _answer(env, pid, [(q["id"], "Many devices") for q in round1])
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    pending = [m for m in _messages(env, pid, "question") if not m["answered"]]
    assert [m["content"] for m in pending] == ["Follow-up question 1?", "Follow-up question 2?"]

    env.llm.replies.extend(ARTIFACT_REPLIES)
    _answer(env, pid, [(m["id"], "x") for m in pending])
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})


@_rounds2
def test_round_budget_exhausted_no_third_pass(env):
    # Queue the whole run up front: the follow-up pass starts the moment
    # /answers returns, so its replies must be in the script before answering.
    env.llm.replies.extend(
        [PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS, PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS, *ARTIFACT_REPLIES]
    )
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    _answer(env, pid, [(q["id"], "Many devices") for q in _messages(env, pid, "question")])

    # Round 2 asks two more; after they are answered the budget (2) is spent.
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    pending = [m for m in _messages(env, pid, "question") if not m["answered"]]
    assert len(pending) == 2
    _answer(env, pid, [(m["id"], "ok") for m in pending])
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    assert len(_pm_calls(env)) == 2  # never a third PM pass


@_rounds2
def test_partial_answers_do_not_start_the_followup_pass(env):
    """/answers bumps discovery_round even on PARTIAL submissions — the round
    budget must not be spent (and a second batch must not be asked) while an
    earlier question of the iteration is still unanswered."""
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    round1 = _messages(env, pid, "question")

    _answer(env, pid, [(round1[0]["id"], "Many devices")])  # one of two
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})

    assert len(_pm_calls(env)) == 1  # no follow-up pass on a partial submission
    pending = [m for m in _messages(env, pid, "question") if not m["answered"]]
    assert [m["id"] for m in pending] == [round1[1]["id"]]  # same batch, no new one

    env.llm.replies.extend([PM_NO_CANDIDATES, ARCH_OK, *ARTIFACT_REPLIES])
    _answer(env, pid, [(round1[1]["id"], "In-app only (no email)")])
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})
    assert len(_pm_calls(env)) == 2  # follow-up ran only once the set was complete


def test_default_single_round_skips_the_followup_pass(env):
    """settings.max_discovery_rounds=1 reproduces pre-D-047 behavior: after
    answers, discovery goes straight to artifacts with no second PM call
    (the scripted reply list has no round-2 entries — a stray call would
    exhaust the mock and fail the wait)."""
    env.llm.replies.extend([PM_WITH_QUESTIONS, ARCH_TWO_QUESTIONS])
    pid = create_project(env)
    wait_for_state(env, pid, {ProjectState.AWAITING_INPUT})
    round1 = _messages(env, pid, "question")

    env.llm.replies.extend(ARTIFACT_REPLIES)
    _answer(env, pid, [(q["id"], "In-app only (no email)") for q in round1])
    wait_for_state(env, pid, {ProjectState.AWAITING_APPROVAL})

    assert len(_pm_calls(env)) == 1
