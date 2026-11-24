"""The compact approval review (D-029, hardened by D-030).

Built from the same versioned artifacts the coding agent will receive: the
requirements document (stable section headings), open-questions.md (asked &
answered decisions, superseded prior decisions, interpreted answers,
unresolved questions), and acceptance-criteria.json (shown verbatim).

Code review (P1): the first version read contents and versions in separate
queries, and an edit landing in between produced a review whose displayed
text (v1) disagreed with its own source map (v2) — an approval carrying that
map would freeze text the operator never saw. Contents AND versions now come
from ONE row set, and the review's source map is built from those same rows.

Code review (P2): bullets-only parsing hid requirements written as ordinary
paragraphs; and a missing section was indistinguishable from an empty one.
Paragraphs under recognized headings are now captured, and a section that
does not exist at all is `None` (the UI says so) versus `[]` (the document
explicitly lists nothing). Pure parsing — deterministic and unit-testable.
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Artifact, ArtifactVersion
from .handoff import HANDOFF_SLUG

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)$")

REQ_SLUG = "product-requirements.md"
OPEN_Q_SLUG = "open-questions.md"
CRITERIA_SLUG = "acceptance-criteria.json"


def split_sections(markdown: str) -> list[tuple[str, list[str]]]:
    """[(heading text lowercased, items)] — every item under a heading is a
    bullet (also numbered / checkbox items) OR a paragraph (blank-line
    separated, wrapped lines folded together); a line directly under a bullet
    or paragraph continues it."""
    sections: list[tuple[str, list[str]]] = []
    heading: str | None = None
    last: str | None = None  # "bullet" | "para" — what the previous line was
    for line in markdown.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            heading = h.group(2).strip().lower()
            if heading:
                sections.append((heading, []))
            last = None
            continue
        if not line.strip():
            last = None
            continue
        if heading is None or not sections:
            continue  # prose before any heading is not surfaced
        b = _BULLET_RE.match(line)
        if b:
            sections[-1][1].append(b.group(1).strip())
            last = "bullet"
        elif last is not None:
            sections[-1][1][-1] += " " + line.strip()  # wrapped continuation
            last = "para" if last == "para" else "bullet"
        else:
            sections[-1][1].append(line.strip())
            last = "para"
    return sections


def _gather(sections: list[tuple[str, list[str]]], aliases: tuple[str, ...]) -> list[str] | None:
    """Items of every section whose heading contains one of `aliases`
    (deduplicated, document order). None when NO such heading exists — the
    document has no such section at all — versus [] when a section exists but
    lists nothing. The distinction is surfaced in the UI."""
    if not any(any(alias in heading for alias in aliases) for heading, _ in sections):
        return None
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


def current_rows(session: Session, project_id: str) -> list[tuple[str, str, int, str]]:
    """(slug, title, version, content) for every CURRENT artifact version the
    approval would freeze — one query, so content and version can never come
    from different moments. Mirrors `handoff.spec_snapshot` eligibility: never
    the handoff prompt, never an empty (v0) artifact."""
    return [
        (r.slug, r.title, r.version, r.content)
        for r in session.execute(
            select(Artifact.slug, Artifact.title, ArtifactVersion.version, ArtifactVersion.content)
            .join(ArtifactVersion, ArtifactVersion.artifact_id == Artifact.id)
            .where(
                Artifact.project_id == project_id,
                ArtifactVersion.version == Artifact.current_version,
                Artifact.slug != HANDOFF_SLUG,
                Artifact.current_version > 0,
            )
        )
    ]


def build_review(session: Session, project) -> dict:
    """The whole review payload for one project. Sections sliced from the
    current versions are None when the heading is missing entirely and []
    when the document has the section but lists nothing — saved edits are
    reflected because text and source map come from the same rows."""
    rows = current_rows(session, project.id)
    contents = {slug: content for slug, _, _, content in rows}

    req_sections = split_sections(contents.get(REQ_SLUG, ""))
    oq_sections = split_sections(contents.get(OPEN_Q_SLUG, ""))

    return {
        "in_scope": _gather(req_sections, ("in scope", "mvp", "confirmed requirement")),
        "out_of_scope": _gather(req_sections, ("out of scope", "out-of-scope")),
        "assumptions": _gather(req_sections, ("assumption",)),
        "decisions": _gather(oq_sections, ("asked and answered", "confirmed decision")),
        "decision_history": _gather(oq_sections, ("prior decision", "decision history")),
        "interpretations": _gather(oq_sections, ("interpreted answers", "interpretation")),
        "unresolved": _gather(oq_sections, ("unresolved question",)),
        "acceptance_criteria": _criteria(contents.get(CRITERIA_SLUG, "")),
        "sources": [
            {"slug": slug, "title": title, "version": version}
            for slug, title, version, _ in sorted(rows)
        ],
    }
