"""Product discovery: PM analysis ⇄ Architect critique, capped rounds,
user Q&A, then versioned artifacts and the approval gate.
"""

import json
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..agents.prompts import ARCHITECT_SYSTEM, PM_SYSTEM, PROMPT_VERSION
from ..agents.schemas import (
    AcceptanceCriteriaOut,
    ArchitectReview,
    DocumentOut,
    PMAnalysis,
    UserStoriesOut,
)
from ..config import Settings
from ..llm.gateway import LLMClient
from ..llm.structured import chat_json
from ..models import AgentMessage, Artifact, ArtifactVersion, Project, ProjectState
from .events import EventBus

logger = logging.getLogger("cf.discovery")


@dataclass
class DiscoveryContext:
    sessions: sessionmaker[Session]
    llm: LLMClient
    bus: EventBus
    settings: Settings


def add_message(
    session: Session, project_id: str, agent: str, content: str, kind: str = "message", round_: int = 0
) -> AgentMessage:
    next_seq = (
        session.execute(
            select(func.max(AgentMessage.seq)).where(AgentMessage.project_id == project_id)
        ).scalar()
        or 0
    )
    msg = AgentMessage(project_id=project_id, agent=agent, kind=kind, content=content, round=round_, seq=next_seq + 1)
    session.add(msg)
    session.flush()
    return msg


def _render_analysis(a: PMAnalysis) -> str:
    lines = [f"**Summary:** {a.summary}", ""]
    if a.confirmed_requirements:
        lines.append("Confirmed requirements:")
        lines += [f"- {r}" for r in a.confirmed_requirements]
    if a.assumptions:
        lines.append("Assumptions (unconfirmed):")
        lines += [f"- {r}" for r in a.assumptions]
    if a.mvp_scope:
        lines.append("MVP scope:")
        lines += [f"- {r}" for r in a.mvp_scope]
    if a.out_of_scope:
        lines.append("Out of scope:")
        lines += [f"- {r}" for r in a.out_of_scope]
    return "\n".join(lines)


def _render_review(r: ArchitectReview) -> str:
    lines = [
        f"**Feasibility: {r.feasibility}**",
        "",
        f"Stack: {', '.join(r.recommended_stack) or '—'}",
    ]
    if r.architecture_summary:
        lines += ["", r.architecture_summary]
    if r.risks:
        lines += ["", "Risks:"] + [f"- {x}" for x in r.risks]
    if r.challenges_for_pm:
        lines += ["", "Challenges for PM:"] + [f"- {x}" for x in r.challenges_for_pm]
    return "\n".join(lines)


def _conversation_context(session: Session, project: Project) -> list[dict[str, str]]:
    """Replay the persisted discussion as LLM context (bounded)."""
    msgs = (
        session.execute(
            select(AgentMessage)
            .where(AgentMessage.project_id == project.id, AgentMessage.kind != "system")
            .order_by(AgentMessage.seq)
        )
        .scalars()
        .all()
    )
    context = [{"role": "user", "content": f"PROJECT IDEA:\n{project.idea}"}]
    for m in msgs[-30:]:
        role = "user" if m.agent == "user" else "assistant"
        label = m.agent if m.agent != "user" else "USER"
        context.append({"role": role, "content": f"[{label}]\n{m.content}"})
    return context


def _get_or_create_artifact(session: Session, project_id: str, slug: str, title: str) -> Artifact:
    artifact = session.execute(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.slug == slug)
    ).scalar_one_or_none()
    if artifact is None:
        artifact = Artifact(project_id=project_id, slug=slug, title=title, current_version=0)
        session.add(artifact)
        session.flush()
    return artifact


def save_artifact(session: Session, project_id: str, slug: str, title: str, content: str, created_by: str) -> Artifact:
    artifact = _get_or_create_artifact(session, project_id, slug, title)
    artifact.current_version += 1
    session.add(
        ArtifactVersion(
            artifact_id=artifact.id, version=artifact.current_version, content=content, created_by=created_by
        )
    )
    session.flush()
    return artifact


ARTIFACT_DEFS = [
    ("product-requirements.md", "Product Requirements"),
    ("architecture.md", "Technical Architecture"),
    ("user-stories.json", "User Stories"),
    ("acceptance-criteria.json", "Acceptance Criteria"),
    ("open-questions.md", "Open Questions & Assumptions"),
]


async def advance_discovery(ctx: DiscoveryContext, project_id: str) -> None:
    """Run the discovery loop from wherever the project currently is.

    Idempotent per stage: safe to re-enter after a crash; the persisted
    message history determines what has already happened.
    """
    with ctx.sessions() as session:
        project = session.get(Project, project_id)
        if project is None or project.state not in (ProjectState.IDEA, ProjectState.DISCOVERY):
            return
        if project.state == ProjectState.IDEA:
            project.state = ProjectState.DISCOVERY
            session.commit()

        has_pm = _has_agent_turn(session, project.id, "product-manager")
        has_arch = _has_agent_turn(session, project.id, "architect")
        answered = _answered_count(session, project.id)

        if not has_pm:
            await _run_pm(ctx, session, project)
            await _run_architect(ctx, session, project)
        elif has_pm and not has_arch and answered > 0:
            await _run_architect(ctx, session, project)

        # Questions gate: if there are unanswered questions, wait for the user.
        questions = _pending_questions(session, project.id)
        if questions and answered == 0:
            project.state = ProjectState.AWAITING_INPUT
            ctx.bus.record(session, project.id, "awaiting_input", {"questions": questions})
            session.commit()
            return

        await _generate_artifacts(ctx, session, project)
        project.state = ProjectState.AWAITING_APPROVAL
        ctx.bus.record(session, project.id, "awaiting_approval", {})
        session.commit()


def _has_agent_turn(session: Session, project_id: str, agent: str) -> bool:
    return (
        session.execute(
            select(AgentMessage.id).where(
                AgentMessage.project_id == project_id, AgentMessage.agent == agent
            ).limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _answered_count(session: Session, project_id: str) -> int:
    return session.execute(
        select(func.count(AgentMessage.id)).where(
            AgentMessage.project_id == project_id, AgentMessage.kind == "answer"
        )
    ).scalar_one()


def _pending_questions(session: Session, project_id: str) -> list[dict[str, str]]:
    return [
        {"id": m.id, "agent": m.agent, "question": m.content}
        for m in session.execute(
            select(AgentMessage)
            .where(AgentMessage.project_id == project_id, AgentMessage.kind == "question")
            .order_by(AgentMessage.seq)
        )
        .scalars()
    ]


async def _run_pm(ctx: DiscoveryContext, session: Session, project: Project) -> None:
    analysis = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": PM_SYSTEM},
            {"role": "user", "content": _pm_task_prompt(session, project)},
        ],
        PMAnalysis,
        repair_attempts=ctx.settings.max_agent_output_repair_attempts,
    )
    add_message(session, project.id, "product-manager", _render_analysis(analysis), round_=project.discovery_round)
    for q in analysis.open_questions:
        add_message(session, project.id, "product-manager", q.question, kind="question", round_=project.discovery_round)
    ctx.bus.record(session, project.id, "pm_analysis", {"questions": len(analysis.open_questions)})
    session.commit()


def _pm_task_prompt(session: Session, project: Project) -> str:
    context = _conversation_context(session, project)
    prior = "\n\n".join(m["content"] for m in context[1:]) or "(none yet)"
    return (
        f"PROJECT IDEA:\n{project.idea}\n\n"
        f"PRIOR DISCUSSION (agent/user turns so far):\n{prior}\n\n"
        "Produce your analysis now: requirements vs assumptions, MVP scope, "
        "user stories, and ONLY materially-important open questions."
    )


async def _run_architect(ctx: DiscoveryContext, session: Session, project: Project) -> None:
    review = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": ARCHITECT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"PROJECT IDEA:\n{project.idea}\n\n"
                    f"FULL DISCUSSION:\n"
                    + "\n\n".join(m["content"] for m in _conversation_context(session, project)[1:])
                ),
            },
        ],
        ArchitectReview,
        repair_attempts=ctx.settings.max_agent_output_repair_attempts,
    )
    add_message(session, project.id, "architect", _render_review(review), round_=project.discovery_round)
    for q in review.open_questions:
        add_message(session, project.id, "architect", q.question, kind="question", round_=project.discovery_round)
    ctx.bus.record(session, project.id, "architect_review", {"feasibility": review.feasibility})
    session.commit()


async def _generate_artifacts(ctx: DiscoveryContext, session: Session, project: Project) -> None:
    discussion = "\n\n".join(m["content"] for m in _conversation_context(session, project)[1:])
    repair = ctx.settings.max_agent_output_repair_attempts

    req = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": PM_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\n"
                    "Write the final product requirements document in markdown: scope, "
                    "confirmed requirements, assumptions (labelled), user stories with "
                    "acceptance criteria, MVP, out-of-scope, success criteria."
                ),
            },
        ],
        DocumentOut,
        repair_attempts=repair,
    )
    save_artifact(session, project.id, "product-requirements.md", "Product Requirements", req.markdown, "product-manager")

    stories = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": PM_SYSTEM},
            {"role": "user", "content": f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\nEmit the final user stories."},
        ],
        UserStoriesOut,
        repair_attempts=repair,
    )
    save_artifact(
        session,
        project.id,
        "user-stories.json",
        "User Stories",
        json.dumps([s.model_dump() for s in stories.stories], indent=2),
        "product-manager",
    )

    criteria = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": PM_SYSTEM},
            {"role": "user", "content": f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\nEmit the final acceptance criteria, each independently testable."},
        ],
        AcceptanceCriteriaOut,
        repair_attempts=repair,
    )
    save_artifact(
        session,
        project.id,
        "acceptance-criteria.json",
        "Acceptance Criteria",
        json.dumps([c.model_dump() for c in criteria.criteria], indent=2),
        "product-manager",
    )

    arch = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": ARCHITECT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\n"
                    "Write the final technical architecture in markdown: stack decision "
                    "with rationale, components, data model, API surface, security, "
                    "deployment, risks."
                ),
            },
        ],
        DocumentOut,
        repair_attempts=repair,
    )
    save_artifact(session, project.id, "architecture.md", "Technical Architecture", arch.markdown, "architect")

    # Open questions doc assembled from the record, not the model (no hallucinated closure).
    leftover = _pending_questions(session, project.id)
    answered_pairs = session.execute(
        select(AgentMessage).where(
            AgentMessage.project_id == project.id, AgentMessage.kind == "answer"
        )
    ).scalars().all()
    lines = ["# Open Questions & Assumptions", ""]
    if leftover:
        lines += ["## Unresolved questions", ""] + [f"- **{q['agent']}**: {q['question']}" for q in leftover]
    if answered_pairs:
        lines += ["", "## Asked and answered", ""] + [f"- {m.content}" for m in answered_pairs]
    lines += ["", "_Prompt version:_", f"- PM/Architect prompts v{PROMPT_VERSION}"]
    save_artifact(
        session, project.id, "open-questions.md", "Open Questions & Assumptions", "\n".join(lines), "system"
    )
    ctx.bus.record(session, project.id, "artifacts_generated", {"artifacts": [s for s, _ in ARTIFACT_DEFS]})
    session.commit()
