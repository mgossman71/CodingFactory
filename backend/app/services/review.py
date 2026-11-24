"""The compact approval review (D-029).

Built from the same versioned artifacts the coding agent will receive: the
requirements document (stable section headings), open-questions.md (asked &
answered decisions, interpreted answers, unresolved questions), and
acceptance-criteria.json (shown verbatim). Pure parsing — deterministic and
unit-testable. Documents written before stable headings (or hand-edited into
different ones) simply yield empty sections; the review always lists its full
source artifacts so the operator can read them whole.
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Artifact, ArtifactVersion
from .handoff import HANDOFF_SLUG, spec_snapshot

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)$")

REQ_SLUG = "product-requirements.md"
OPEN_Q_SLUG = "open-questions.md"
CRITERIA_SLUG = "acceptance-criteria.json"


def split_sections(markdown: str) -> list[tuple[str, list[str]]]:
    """[(heading text lowercased, bullet items)] — bullets (and numbered /
    checkbox items) under each heading; continuation lines fold into the
    previous bullet. Paragraph prose outside bullets is not surfaced (the
    full source remains one click away)."""
    sections: list[tuple[str, list[str]]] = []
    heading: str | None = None
    for line in markdown.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            heading = h.group(2).strip().lower()
            if heading:
                sections.append((heading, []))
            continue
        b = _BULLET_RE.match(line)
        if b and heading is not None:
            sections[-1][1].append(b.group(1).strip())
        elif line.strip() and heading is not None and sections and sections[-1][1]:
            sections[-1][1][-1] += " " + line.strip()  # continuation of a bullet
    return sections


def _gather(sections: list[tuple[str, list[str]]], aliases: tuple[str, ...]) -> list[str]:
    """Items of every section whose heading contains one of `aliases` (first
    alias wins for ordering), deduplicated, preserving document order."""
    out: list[str] = []
    seen: set[str] = set()
    for heading, items in sections:
        if any(alias in heading for alias in aliases):
            for item in items:
                if item not in seen:
                    seen.add(item)
                    out.append(item)
    return out


def _criteria(content: str) -> list[dict[str, str]]:
    try:
        rows = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    return [
        {"id": str(r.get("id", f"ac{i + 1}")), "criterion": str(r.get("criterion", ""))}
        for i, r in enumerate(rows)
        if isinstance(r, dict) and str(r.get("criterion", "")).strip()
    ]


def _current_content(session: Session, project_id: str, slug: str) -> str:
    row = session.execute(
        select(ArtifactVersion.content)
        .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
        .where(Artifact.project_id == project_id, Artifact.slug == slug,
               ArtifactVersion.version == Artifact.current_version)
    ).first()
    return row[0] if row else ""


def build_review(session: Session, project, artifacts: list[Artifact] | None = None) -> dict:
    """The whole review payload for one project (current versions: exactly
    what approval is about to freeze, so saved edits are reflected)."""
    req_sections = split_sections(_current_content(session, project.id, REQ_SLUG))
    oq_sections = split_sections(_current_content(session, project.id, OPEN_Q_SLUG))

    if artifacts is None:
        artifacts = list(
            session.execute(select(Artifact).where(Artifact.project_id == project.id)).scalars()
        )
    titles = {a.slug: a.title for a in artifacts}
    snapshot = spec_snapshot(project)  # same eligibility rule the approval freeze uses
    sources = [
        {"slug": slug, "title": titles.get(slug, slug), "version": version}
        for slug, version in sorted(snapshot.items())
        if slug != HANDOFF_SLUG
    ]

    return {
        "in_scope": _gather(req_sections, ("in scope", "mvp", "confirmed requirement")),
        "out_of_scope": _gather(req_sections, ("out of scope", "out-of-scope")),
        "assumptions": _gather(req_sections, ("assumption",)),
        "decisions": _gather(oq_sections, ("asked and answered", "confirmed decision")),
        "interpretations": _gather(oq_sections, ("interpreted answers", "interpretation")),
        "unresolved": _gather(oq_sections, ("unresolved question", "open question")),
        "acceptance_criteria": _criteria(_current_content(session, project.id, CRITERIA_SLUG)),
        "sources": sources,
    }
