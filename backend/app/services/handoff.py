"""Handoff: assemble a single, self-contained build prompt the operator can
hand to a more capable coding agent to do the build + test phases.

Deterministic (no extra LLM call): the approved artifact versions plus the Dev
Manager's task plan are composed into one markdown document. That document is
the deliverable — CodingFactory no longer executes the build itself.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.prompts import HANDOFF_BRIEF
from ..models import Artifact, ArtifactVersion, Project, Task
from .discovery import DESIGN_ARTIFACT_DEFS, save_artifact

HANDOFF_SLUG = "handoff-prompt.md"
HANDOFF_TITLE = "Build Handoff Prompt"

_DESIGN_SLUGS = {slug for slug, _ in DESIGN_ARTIFACT_DEFS}


def current_content(session: Session, artifact: Artifact) -> str | None:
    """The text of an artifact's current version, or None if it has none."""
    if artifact.current_version == 0:
        return None
    version = session.execute(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact.id,
            ArtifactVersion.version == artifact.current_version,
        )
    ).scalar_one_or_none()
    return version.content if version is not None else None


def approved_specs(session: Session, project: Project) -> list[tuple[str, str]]:
    """(slug, current content) for every spec artifact, in slug order. Shared by
    the planner and the handoff so both see the same specification: never the
    handoff itself, and no design docs left over from an iteration in which the
    product was still user-facing."""
    out: list[tuple[str, str]] = []
    for artifact in sorted(project.artifacts, key=lambda a: a.slug):
        if artifact.slug == HANDOFF_SLUG:
            continue
        if artifact.slug in _DESIGN_SLUGS and not project.user_facing:
            continue
        content = current_content(session, artifact)
        if content is not None and content.strip():
            out.append((artifact.slug, content))
    return out


def write_handoff(session: Session, project: Project) -> None:
    """Render and save a new handoff-prompt.md version (caller commits)."""
    save_artifact(session, project.id, HANDOFF_SLUG, HANDOFF_TITLE, generate_handoff(session, project), "dev-manager")


def generate_handoff(session: Session, project: Project) -> str:
    """Render the full build prompt for this project's current plan + specs."""
    tasks = session.execute(
        select(Task).where(Task.project_id == project.id).order_by(Task.number)
    ).scalars().all()

    lines: list[str] = [HANDOFF_BRIEF.rstrip(), "", "---", ""]
    lines += [f"# Project: {project.name}", ""]
    lines += [f"**Original idea:** {project.idea}", ""]
    if project.iteration > 0:
        lines += [
            f"_Iteration {project.iteration + 1}: the specs and plan below are the "
            "latest after change-request cycles. Treat this as the whole current "
            "specification, not a diff._",
            "",
        ]

    specs = approved_specs(session, project)
    lines += ["## Approved specification", ""]
    if specs:
        for slug, content in specs:
            lines += [f"### {slug}", "", content.strip(), ""]
    else:
        lines += ["(no specification artifacts)", ""]

    lines += ["## Task plan (implement in this order)", ""]
    if not tasks:
        lines += ["(no tasks)", ""]
    for t in tasks:
        deps = ", ".join(f"#{d}" for d in t.dependencies) if t.dependencies else "none"
        lines += [f"### Task {t.number}: {t.title} _[{t.category}]_", ""]
        lines += [f"**Depends on:** {deps}", ""]
        if t.description.strip():
            lines += [t.description.strip(), ""]
        if t.acceptance_criteria.strip():
            lines += ["**Acceptance criteria:**", t.acceptance_criteria.strip(), ""]

    return "\n".join(lines).rstrip() + "\n"
