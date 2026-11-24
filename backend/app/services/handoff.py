"""Handoff: assemble a single, self-contained build prompt the operator can
hand to a more capable coding agent to do the build + test phases.

Deterministic (no extra LLM call): the approved artifact versions plus the Dev
Manager's task plan are composed into one markdown document. That document is
the deliverable — CodingFactory no longer executes the build itself.

D-023: the specification inlined here is the exact set of artifact versions
frozen at approval — never a later edit. D-024: change-request handoffs get an
existing-repository brief and label every task NEW / delivered / SUPERSEDED.
D-025: tasks are exported in topological order, task number as tie-breaker.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.prompts import HANDOFF_BRIEF, ITERATION_BRIEF
from ..models import Approval, ApprovalDecision, Artifact, ArtifactVersion, Project, Task
from .discovery import DESIGN_ARTIFACT_DEFS, save_artifact

HANDOFF_SLUG = "handoff-prompt.md"
HANDOFF_TITLE = "Build Handoff Prompt"

_DESIGN_SLUGS = {slug for slug, _ in DESIGN_ARTIFACT_DEFS}


def _content_at(session: Session, artifact: Artifact, version: int) -> str | None:
    """The text of one specific artifact version, or None if it has none."""
    if version <= 0:
        return None
    row = session.execute(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact.id,
            ArtifactVersion.version == version,
        )
    ).scalar_one_or_none()
    return row.content if row is not None else None


def current_content(session: Session, artifact: Artifact) -> str | None:
    """The text of an artifact's current version, or None if it has none."""
    return _content_at(session, artifact, artifact.current_version)


def spec_snapshot(project: Project) -> dict[str, int]:
    """The versions an approval must freeze (D-023): every non-empty spec
    artifact's current version at that moment, never the handoff itself."""
    return {
        a.slug: a.current_version
        for a in project.artifacts
        if a.slug != HANDOFF_SLUG and a.current_version > 0
    }


def approval_snapshot(session: Session, project: Project) -> dict[str, int] | None:
    """The artifact versions frozen when the user approved this iteration
    (D-023), or None when no approval captured a snapshot (pre-migration rows,
    service-level callers before the gate) — then current versions are the
    baseline, which is exactly how those flows behaved before."""
    approval = session.execute(
        select(Approval)
        .where(
            Approval.project_id == project.id,
            Approval.iteration == project.iteration,
            Approval.decision == ApprovalDecision.APPROVED,
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if approval is None or not approval.artifact_versions:
        return None
    return {str(slug): int(version) for slug, version in approval.artifact_versions.items()}


def approved_specs(session: Session, project: Project) -> list[tuple[str, str]]:
    """(slug, approved content) for every spec artifact, in slug order. Shared
    by the planner and the handoff so both see the same specification: with an
    approval snapshot (D-023) the pinned versions — never a later edit, and
    never artifacts that did not exist at approval. Without one, current
    versions. Excludes the handoff itself and design docs left over from an
    iteration in which the product was still user-facing."""
    snapshot = approval_snapshot(session, project)
    out: list[tuple[str, str]] = []
    for artifact in sorted(project.artifacts, key=lambda a: a.slug):
        if artifact.slug == HANDOFF_SLUG:
            continue
        if artifact.slug in _DESIGN_SLUGS and not project.user_facing:
            continue
        if snapshot is None:
            version = artifact.current_version
        else:
            if artifact.slug not in snapshot:
                continue  # absent (or still empty) at approval time
            version = snapshot[artifact.slug]
        content = _content_at(session, artifact, version)
        if content is not None and content.strip():
            out.append((artifact.slug, content))
    return out


def superseded_map(tasks: Iterable[Task]) -> dict[int, list[int]]:
    """D-024: task number -> the newer task numbers that replace it. A delta
    plan may only supersede pre-existing tasks, so a number here was always
    planned in an earlier cycle than the task that supersedes it."""
    out: dict[int, list[int]] = {}
    for t in tasks:
        for old in t.supersedes or []:
            out.setdefault(old, []).append(t.number)
    return {num: sorted(replacements) for num, replacements in out.items()}


def topological_order(tasks: list[Task]) -> list[Task]:
    """D-025: dependencies strictly before dependents; the task number only
    breaks ties. Validation forbids cycles, but the export must not depend on
    that: if a cycle somehow survived, the remaining tasks are appended in
    number order rather than silently dropped."""
    by_number = {t.number: t for t in tasks}
    pending = {n: {d for d in t.dependencies if d in by_number} - {n} for n, t in by_number.items()}
    ordered: list[Task] = []
    while pending:
        ready = sorted(n for n, deps in pending.items() if not deps)
        pick = ready[0] if ready else min(pending)
        ordered.append(by_number[pick])
        del pending[pick]
        for deps in pending.values():
            deps.discard(pick)
    return ordered


def _task_label(t: Task, project: Project, superseded: dict[int, list[int]]) -> str:
    """D-024: what a builder should do with this task, for change-request
    iterations (empty for the first whole-plan iteration)."""
    if not project.iteration:
        return ""
    if t.number in superseded:
        repl = ", ".join(str(n) for n in superseded[t.number])
        return f"SUPERSEDED by task {repl} — its functionality is replaced; retire it, do not rebuild it"
    if t.iteration == project.iteration:
        return "NEW in this change request"
    return "delivered earlier — verify it still holds, do not rebuild it"


def write_handoff(session: Session, project: Project) -> None:
    """Render and save a new handoff-prompt.md version (caller commits)."""
    save_artifact(session, project.id, HANDOFF_SLUG, HANDOFF_TITLE, generate_handoff(session, project), "dev-manager")


def generate_handoff(session: Session, project: Project) -> str:
    """Render the full build prompt for this project's approved snapshot + plan."""
    tasks = session.execute(select(Task).where(Task.project_id == project.id)).scalars().all()
    ordered = topological_order(list(tasks))
    superseded = superseded_map(tasks)
    is_delta = project.iteration > 0

    brief = ITERATION_BRIEF if is_delta else HANDOFF_BRIEF
    lines: list[str] = [brief.rstrip(), "", "---", ""]
    lines += [f"# Project: {project.name}", ""]
    lines += [f"**Original idea:** {project.idea}", ""]
    if is_delta:
        lines += [
            f"_Iteration {project.iteration + 1}: the specification below is the whole "
            f"current specification after change-request cycle {project.iteration} — not a "
            "diff. The task plan labels each task NEW (the only work to implement), "
            "delivered, or SUPERSEDED. The codebase already exists; work in it._",
            "",
        ]

    specs = approved_specs(session, project)
    lines += ["## Approved specification", ""]
    if specs:
        for slug, content in specs:
            lines += [f"### {slug}", "", content.strip(), ""]
    else:
        lines += ["(no specification artifacts)", ""]

    if is_delta:
        lines += ["## Task plan (existing repository — labels decide what to do)", ""]
    else:
        lines += ["## Task plan (implement in this order)", ""]
    if not tasks:
        lines += ["(no tasks)", ""]
    for t in ordered:
        deps = ", ".join(f"#{d}" for d in t.dependencies) if t.dependencies else "none"
        label = _task_label(t, project, superseded)
        brackets = f"[{t.category}]" + (f" · {label}" if label else "")
        lines += [f"### Task {t.number}: {t.title} _{brackets}_", ""]
        lines += [f"**Depends on:** {deps}", ""]
        if t.description.strip():
            lines += [t.description.strip(), ""]
        if t.acceptance_criteria.strip():
            lines += ["**Acceptance criteria:**", t.acceptance_criteria.strip(), ""]

    return "\n".join(lines).rstrip() + "\n"
