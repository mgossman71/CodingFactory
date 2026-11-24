"""Product discovery: PM analysis ⇄ Architect critique, capped rounds,
user Q&A, then versioned artifacts and the approval gate.
"""

import json
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..agents.prompts import ARCHITECT_SYSTEM, DESIGNER_SYSTEM, PM_SYSTEM, PROMPT_VERSION
from ..agents.schemas import (
    AcceptanceCriteriaOut,
    ArchitectReview,
    DocumentOut,
    PMAnalysis,
    RequirementsDoc,
    UserStoriesOut,
)
from ..config import Settings
from ..llm.gateway import LLMClient
from ..llm.structured import chat_json
from ..models import AgentMessage, Artifact, ArtifactVersion, Project, ProjectState
from .events import EventBus

logger = logging.getLogger("cf.discovery")

# D-028: the Architect consolidates everyone's candidates into ONE operator-
# facing batch of at most this many questions per discovery iteration.
_MAX_OPERATOR_QUESTIONS = 3


def _question_meta(q) -> dict:
    """Structured detail carried on question messages (D-028)."""
    return {
        "why_it_matters": q.why_it_matters,
        "options": list(q.options),
        "recommended_option": q.recommended_option,
    }


@dataclass
class DiscoveryContext:
    sessions: sessionmaker[Session]
    llm: LLMClient
    bus: EventBus
    settings: Settings


def add_message(
    session: Session,
    project_id: str,
    agent: str,
    content: str,
    kind: str = "message",
    round_: int = 0,
    iteration_: int = 0,
    meta: dict | None = None,
) -> AgentMessage:
    next_seq = (
        session.execute(
            select(func.max(AgentMessage.seq)).where(AgentMessage.project_id == project_id)
        ).scalar()
        or 0
    )
    msg = AgentMessage(
        project_id=project_id, agent=agent, kind=kind, content=content,
        round=round_, seq=next_seq + 1, iteration=iteration_, meta=meta,
    )
    session.add(msg)
    session.flush()
    return msg


def _render_analysis(a: PMAnalysis) -> str:
    lines = [f"**Summary:** {a.summary}", ""]
    lines.append(
        f"**User-facing product:** {a.user_facing}"
        + (f" | genre: {a.genre}" if a.genre else "")
    )
    lines.append("")
    if a.genre_expectations:
        lines.append("What this genre typically includes:")
        lines += [f"- {e}" for e in a.genre_expectations]
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


# Design/brand/rubric artifacts exist only for user-facing products; they are
# what stops the build from being plumbing-only (D-020).
CORE_ARTIFACT_DEFS = [
    ("product-requirements.md", "Product Requirements"),
    ("architecture.md", "Technical Architecture"),
    ("user-stories.json", "User Stories"),
    ("acceptance-criteria.json", "Acceptance Criteria"),
    ("open-questions.md", "Open Questions & Assumptions"),
]
DESIGN_ARTIFACT_DEFS = [
    ("design-spec.md", "Design Spec"),
    ("brand-and-copy.md", "Brand & Copy"),
    ("quality-rubric.md", "Quality Rubric"),
]
ARTIFACT_DEFS = CORE_ARTIFACT_DEFS + DESIGN_ARTIFACT_DEFS


def _still_running(session: Session, project: Project) -> bool:
    """D-026: fresh DB read of the project's state (the ORM copy goes stale
    across a long model call). Discovery may only write while the project is
    still IDEA/DISCOVERY — a CANCELLED (or FAILED) project must not be revived
    by a pending model call returning late. The cancel endpoint aborts the live
    task, but the await may already have returned, so every post-await write
    guards here too."""
    state = session.execute(
        select(Project.state).where(Project.id == project.id)
    ).scalar_one_or_none()
    return state in (ProjectState.IDEA, ProjectState.DISCOVERY)


async def advance_discovery(ctx: DiscoveryContext, project_id: str) -> None:
    """Run the discovery loop from wherever the project currently is.

    Idempotent per stage: safe to re-enter after a crash; the persisted
    message history determines what has already happened. Agent turns are
    scoped to the project's current iteration so a change request starts a
    fresh PM/Architect pass while keeping the full discussion as context.
    Every state transition is guarded by _still_running (D-026).
    """
    with ctx.sessions() as session:
        project = session.get(Project, project_id)
        if project is None or project.state not in (ProjectState.IDEA, ProjectState.DISCOVERY):
            return
        if project.state == ProjectState.IDEA:
            project.state = ProjectState.DISCOVERY
            session.commit()

        iteration = project.iteration
        has_pm = _has_agent_turn(session, project.id, "product-manager", iteration)
        has_arch = _has_agent_turn(session, project.id, "architect", iteration)

        if not has_pm:
            await _run_pm(ctx, session, project)
            if not _still_running(session, project):
                return
            await _run_architect(ctx, session, project)
        elif has_pm and not has_arch:
            # D-028: resume between the two turns — the PM's candidates were
            # committed but the Architect's consolidated batch was not; the
            # architect re-runs on its own (candidates already persisted, so
            # no duplicate PM call and no duplicate questions).
            await _run_architect(ctx, session, project)

        if not _still_running(session, project):
            return

        # Questions gate (D-028): the Architect's consolidated batch for THIS
        # iteration; the project waits at AWAITING_INPUT until every one of
        # them is answered (partial submissions re-arm the gate).
        questions = _pending_questions(session, project.id, project.iteration)
        if questions:
            project.state = ProjectState.AWAITING_INPUT
            ctx.bus.record(session, project.id, "awaiting_input", {"questions": questions})
            session.commit()
            return

        await _generate_artifacts(ctx, session, project)
        if not _still_running(session, project):
            return
        project.state = ProjectState.AWAITING_APPROVAL
        ctx.bus.record(session, project.id, "awaiting_approval", {})
        session.commit()


def _has_agent_turn(session: Session, project_id: str, agent: str, iteration: int) -> bool:
    return (
        session.execute(
            select(AgentMessage.id).where(
                AgentMessage.project_id == project_id,
                AgentMessage.agent == agent,
                AgentMessage.iteration == iteration,
            ).limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _pending_questions(
    session: Session, project_id: str, iteration: int | None = None
) -> list[dict]:
    """Unanswered operator-facing questions. D-028: only the Architect's
    consolidated batch (kind="question") gates the flow — PM candidates are
    recorded as kind="question_candidate". Scoped to one iteration so a
    change request's questions don't inherit stale ones."""
    conds = [
        AgentMessage.project_id == project_id,
        AgentMessage.kind == "question",
        AgentMessage.answered.is_(False),
    ]
    if iteration is not None:
        conds.append(AgentMessage.iteration == iteration)
    return [
        {"id": m.id, "agent": m.agent, "question": m.content, "meta": m.meta or {}}
        for m in session.execute(
            select(AgentMessage).where(*conds).order_by(AgentMessage.seq)
        ).scalars()
    ]


def _operator_intents(session: Session, project: Project) -> list[AgentMessage]:
    """Explicit change requests and revision requests, in message order. These
    are the operator's stated intent (D-031/D-035) — both kinds live in the
    same seq ordering, so the LAST one is the most recent instruction."""
    return list(
        session.execute(
            select(AgentMessage)
            .where(
                AgentMessage.project_id == project.id,
                or_(
                    AgentMessage.kind == "change_request",
                    and_(
                        AgentMessage.kind == "message",
                        AgentMessage.content.startswith("REVISION REQUEST:"),
                    ),
                ),
            )
            .order_by(AgentMessage.seq)
        ).scalars()
    )


def _decision_split(session: Session, project: Project) -> tuple[list[AgentMessage], list[AgentMessage]]:
    """(binding, possibly-superseded) recorded answers. D-035: ONE precedence
    rule for change requests AND revision requests — message order decides.
    Every answer recorded BEFORE the most recent operator intent (change
    request or revision comment) is demoted to history: the intent may have
    reversed it, so it is never presented as binding again. Answers recorded
    after it (e.g. the Q&A round that intent triggered) stay binding."""
    answers = session.execute(
        select(AgentMessage)
        .where(AgentMessage.project_id == project.id, AgentMessage.kind == "answer")
        .order_by(AgentMessage.seq)
    ).scalars().all()
    intents = _operator_intents(session, project)
    if not intents:
        return list(answers), []
    boundary = max(m.seq for m in intents)
    return (
        [m for m in answers if m.seq > boundary],
        [m for m in answers if m.seq < boundary],
    )


def _decisions_block(session: Session, project: Project) -> str:
    """D-028/D-031: the operator's recorded decisions, outside the windowed
    discussion replay — so a decision survives into later artifact generation
    no matter how long the discussion gets. Explicit change/revision requests
    carry the most recent intent and take precedence; answers that predate a
    change request are marked as possibly-revised history, never as binding
    instructions."""
    binding, superseded = _decision_split(session, project)
    intents = _operator_intents(session, project)
    if not binding and not superseded and not intents:
        return ""
    parts = ["\n\nOPERATOR DECISIONS"]
    if intents:
        parts.append(
            " — MOST RECENT INTENT (explicit change/revision requests; where they "
            "conflict with an older answer, follow these):\n"
            + "\n".join(f"- {' '.join(m.content.split())}" for m in intents)
        )
    if binding:
        parts.append(
            "\nBINDING (recorded answers no change request has come after; never "
            "re-ask or contradict them):\n"
            + "\n".join(f"- {' '.join(m.content.split())}" for m in binding)
        )
    if superseded:
        parts.append(
            "\nHISTORY (recorded before a later change request — the operator may "
            "have revised these; the CURRENT INTENT above wins on conflict):\n"
            + "\n".join(f"- {' '.join(m.content.split())}" for m in superseded)
        )
    return "".join(parts)


def _pm_candidates_block(session: Session, project: Project) -> str:
    """D-028: the current iteration's PM candidate questions, with their
    structured detail, handed to the Architect for consolidation."""
    cands = session.execute(
        select(AgentMessage)
        .where(
            AgentMessage.project_id == project.id,
            AgentMessage.kind == "question_candidate",
            AgentMessage.iteration == project.iteration,
        )
        .order_by(AgentMessage.seq)
    ).scalars().all()
    lines = []
    for m in cands:
        meta = m.meta or {}
        line = f"- {m.content}"
        if meta.get("why_it_matters"):
            line += f" (why: {meta['why_it_matters']})"
        if meta.get("options"):
            line += f" | options: {'; '.join(meta['options'])}"
        if meta.get("recommended_option"):
            line += f" | PM recommends: {meta['recommended_option']}"
        lines.append(line)
    body = "\n".join(lines) or "(none)"
    return (
        f"\n\nPM CANDIDATE QUESTIONS (consolidate these with your own into ONE "
        f"ranked batch of at most {_MAX_OPERATOR_QUESTIONS}: merge overlapping "
        "candidates, drop anything the idea/change request/decisions already "
        f"answer):\n{body}"
    )


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
    if not _still_running(session, project):
        return  # cancelled/failed while the model call was pending: write nothing (D-026)
    project.user_facing = analysis.user_facing
    add_message(
        session, project.id, "product-manager", _render_analysis(analysis),
        round_=project.discovery_round, iteration_=project.iteration,
    )
    # D-028: candidates are recorded with the PM turn but are NOT operator-
    # facing — the Architect's consolidated batch (kind="question") is.
    for q in analysis.open_questions:
        add_message(
            session, project.id, "product-manager", q.question,
            kind="question_candidate", round_=project.discovery_round,
            iteration_=project.iteration, meta=_question_meta(q),
        )
    ctx.bus.record(session, project.id, "pm_analysis", {"candidates": len(analysis.open_questions)})
    session.commit()


def _delta_briefing(session: Session, project: Project) -> str:
    """Context block for change-request iterations: current specs + requested changes."""
    if project.iteration == 0:
        return ""
    specs: list[str] = []
    for artifact in project.artifacts:
        if not artifact.slug.endswith(".md") or artifact.current_version == 0:
            continue
        version = session.execute(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact.id,
                ArtifactVersion.version == artifact.current_version,
            )
        ).scalar_one_or_none()
        if version is not None:
            specs.append(f"## {artifact.slug}\n\n{version.content[:4000]}")
    changes = session.execute(
        select(AgentMessage)
        .where(
            AgentMessage.project_id == project.id,
            AgentMessage.kind == "change_request",
            AgentMessage.iteration == project.iteration,
        )
        .order_by(AgentMessage.seq)
    ).scalars().all()
    return (
        f"\n\nCHANGE-REQUEST CYCLE {project.iteration}: the product was already "
        "delivered once and the user is requesting changes. Focus ONLY on the "
        "requested changes; everything not affected by them stays as-is. Ask "
        "questions only if the changes themselves are ambiguous.\n\n"
        "CURRENT APPROVED SPECS:\n" + ("\n\n".join(specs) or "(none)") + "\n\n"
        "USER'S REQUESTED CHANGES:\n"
        + "\n\n".join(m.content for m in changes)
    )


def _pm_task_prompt(session: Session, project: Project) -> str:
    context = _conversation_context(session, project)
    prior = "\n\n".join(m["content"] for m in context[1:]) or "(none yet)"
    return (
        f"PROJECT IDEA:\n{project.idea}\n\n"
        f"PRIOR DISCUSSION (agent/user turns so far):\n{prior}\n\n"
        "Produce your analysis now: requirements vs assumptions, MVP scope, "
        "user stories, and ONLY materially-important candidate questions "
        "(the Architect will consolidate them — see your instructions)."
        + _decisions_block(session, project)
        + _delta_briefing(session, project)
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
                    + _pm_candidates_block(session, project)
                    + _decisions_block(session, project)
                    + _delta_briefing(session, project)
                ),
            },
        ],
        ArchitectReview,
        repair_attempts=ctx.settings.max_agent_output_repair_attempts,
    )
    if not _still_running(session, project):
        return  # D-026: no writes onto a cancelled/failed project
    add_message(
        session, project.id, "architect", _render_review(review),
        round_=project.discovery_round, iteration_=project.iteration,
    )
    # D-028: the open_questions list is the ranked batch; keep the top
    # `_MAX_OPERATOR_QUESTIONS` and record them as the ONLY operator-facing
    # questions for this iteration (structured detail travels with them).
    batch = review.open_questions[:_MAX_OPERATOR_QUESTIONS]
    if len(review.open_questions) > len(batch):
        logger.info(
            "architect batch of %d questions trimmed to %d for project %s",
            len(review.open_questions), len(batch), project.id,
        )
    for q in batch:
        add_message(
            session, project.id, "architect", q.question,
            kind="question", round_=project.discovery_round,
            iteration_=project.iteration, meta=_question_meta(q),
        )
    ctx.bus.record(
        session, project.id, "architect_review",
        {"feasibility": review.feasibility, "questions": len(batch)},
    )
    session.commit()


async def _generate_artifacts(ctx: DiscoveryContext, session: Session, project: Project) -> None:
    discussion = "\n\n".join(m["content"] for m in _conversation_context(session, project)[1:])
    # D-028: operator decisions ride along with every artifact prompt even when
    # the discussion replay window has scrolled past them.
    decisions = _decisions_block(session, project)
    repair = ctx.settings.max_agent_output_repair_attempts
    revise = (
        f"\n\nThis is CHANGE-REQUEST cycle {project.iteration}: revise the documents to "
        "incorporate the user's requested changes from the discussion; leave unaffected "
        "content unchanged."
        if project.iteration
        else ""
    )

    req = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": PM_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\n"
                    "Write the final product requirements document in markdown (under 900 words): scope, "
                    "confirmed requirements (label each CONSTRAINT as binding; put explanations of why in a "
                    "separate labelled Rationale note, never as scope-decay language), assumptions (labelled), "
                    "user stories with acceptance criteria, MVP, out-of-scope (keep any "
                    "'(intentionally excluded — confirm at approval)' tags), success criteria. "
                    "Use stable section headings the approval review can slice: exactly `## In scope`, "
                    "`## Out of scope`, and `## Assumptions` as H2 headings (plus any other H2 sections you "
                    "need). "
                    "Also return answer_interpretations: one line per user answer restating what it means — "
                    "where an answer was terse or ambiguous, state the interpretation explicitly." + revise
                    + decisions
                ),
            },
        ],
        RequirementsDoc,
        repair_attempts=repair,
    )
    save_artifact(session, project.id, "product-requirements.md", "Product Requirements", req.markdown, "product-manager")

    stories = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": PM_SYSTEM},
            {"role": "user", "content": f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\nEmit the final user stories." + revise + decisions},
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
            {"role": "user", "content": f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\nEmit the final acceptance criteria, each independently verifiable — an automated test for functional criteria; rubric/self-review checks belong in the quality rubric, not here. Do not omit experiential requirements from user stories just because they are not automatable." + revise + decisions},
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

    if project.user_facing:
        design_prompts = [
            (
                "design-spec.md",
                "Design Spec",
                "Write the final design spec in markdown (under 700 words): mood and aesthetic direction "
                "(name it, e.g. 'dark, high-energy, RGB accents, premium hardware brand'); color palette with "
                "hex values; typography (font stacks, weight/size rhythm); layout and spacing; component "
                "styling rules (hero, cards, buttons, nav); imagery policy INCLUDING the placeholder strategy "
                "for deferred assets (cohesive SVG renders/gradients/procedural imagery — never empty fields); "
                "2-3 named reference sites as the quality bar; and a 'never do' list (default-framework "
                "styling, unstyled native elements above the fold, empty visual fields).",
            ),
            (
                "brand-and-copy.md",
                "Brand & Copy",
                "Write the brand & copy document in markdown: business name; tone of voice; brand do's and "
                "don'ts; then the copy deck the implementer must use VERBATIM — hero headline, hero subhead, "
                "section headings, CTA labels, contact/pricing lines. If the user never gave brand inputs, "
                "invent a coherent set and mark it as a proposal for approval.",
            ),
            (
                "quality-rubric.md",
                "Quality Rubric",
                "Write the quality rubric in markdown: a self-review checklist of explicitly NON-automatable "
                "criteria a human judges from screenshots — premium feel per the design spec, genre "
                "expectations met (imagery-rich cards, strong hero), no default-framework-styled elements, "
                "WCAG AA contrast, fluid feel at 320/768/1280, no unstyled native elements above the fold, "
                "approved copy used verbatim. One checkbox item per criterion, concrete enough to judge.",
            ),
        ]
        for slug, title, instruction in design_prompts:
            doc = await chat_json(
                ctx.llm,
                [
                    {"role": "system", "content": DESIGNER_SYSTEM},
                    {
                        "role": "user",
                        "content": f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\n{instruction}" + revise + decisions,
                    },
                ],
                DocumentOut,
                repair_attempts=repair,
            )
            save_artifact(session, project.id, slug, title, doc.markdown, "designer")

    arch = await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": ARCHITECT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"PROJECT IDEA:\n{project.idea}\n\nDISCUSSION:\n{discussion}\n\n"
                    "Write the final technical architecture in markdown (under 900 words): ONE stack decision "
                    "(no alternatives) with rationale, components, data model, API surface, security, "
                    "deployment, risks, and — for user-facing products — a non-functional requirements block: "
                    "WCAG AA, fluid layout at 320/768/1280 breakpoints, image-richness per the design spec, "
                    "and no unstyled native browser elements above the fold." + revise + decisions
                ),
            },
        ],
        DocumentOut,
        repair_attempts=repair,
    )
    save_artifact(session, project.id, "architecture.md", "Technical Architecture", arch.markdown, "architect")

    # Open questions doc assembled from the record, not the model (no
    # hallucinated closure). D-031: answers that predate a change request go
    # under history — the change request may have reversed them — so the
    # approval review never lists an obsolete decision as current.
    leftover = _pending_questions(session, project.id, project.iteration)
    binding, superseded = _decision_split(session, project)
    lines = ["# Open Questions & Assumptions", ""]
    if leftover:
        lines += ["## Unresolved questions", ""] + [f"- **{q['agent']}**: {q['question']}" for q in leftover]
    if binding:
        # One decision per bullet (newlines folded) so the approval review
        # (services/review.py) can slice this document deterministically.
        lines += ["", "## Asked and answered", ""] + [f"- {' '.join(m.content.split())}" for m in binding]
    if superseded:
        lines += [
            "",
            "## Prior decisions (history — a later change request may have revised these)",
            "",
        ] + [f"- {' '.join(m.content.split())}" for m in superseded]
    if req.answer_interpretations:
        lines += [
            "",
            "## Interpreted answers (verify at approval)",
            "",
        ] + [f"- {i}" for i in req.answer_interpretations]
    if not leftover and not binding and not superseded:
        lines += ["No open questions remain."]
    lines += ["", "_Prompt version:_", f"- PM/Architect/Designer prompts v{PROMPT_VERSION}"]
    save_artifact(
        session, project.id, "open-questions.md", "Open Questions & Assumptions", "\n".join(lines), "system"
    )
    # D-026: a cancellation mid-generation discards this whole uncommitted
    # artifact batch (the session closes without committing) instead of writing
    # it onto a cancelled project.
    if not _still_running(session, project):
        return
    generated = [s for s, _ in CORE_ARTIFACT_DEFS]
    if project.user_facing:
        generated += [s for s, _ in DESIGN_ARTIFACT_DEFS]
    ctx.bus.record(session, project.id, "artifacts_generated", {"artifacts": generated})
    session.commit()
