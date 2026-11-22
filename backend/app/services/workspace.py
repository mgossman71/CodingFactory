"""Project workspaces: isolated folders under WORKSPACES_DIR."""

import re
import subprocess
from pathlib import Path


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


class Workspace:
    def __init__(self, root: Path, slug: str):
        self.path = root / slug

    def ensure(self) -> Path:
        """Create the dir and git-init it (idempotent)."""
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            subprocess.run(
                ["git", "init", "-b", "main"], cwd=self.path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "CodingFactory"],
                cwd=self.path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "codingfactory@local"],
                cwd=self.path,
                check=True,
                capture_output=True,
            )
        return self.path

    def commit_all(self, message: str) -> str | None:
        """Commit everything; returns short SHA or None if nothing to commit."""
        subprocess.run(["git", "add", "-A"], cwd=self.path, check=True, capture_output=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.path, check=True, capture_output=True, text=True
        )
        if not status.stdout.strip():
            return None
        subprocess.run(
            ["git", "commit", "-m", message], cwd=self.path, check=True, capture_output=True
        )
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=self.path, check=True, capture_output=True, text=True
        )
        return sha.stdout.strip()

    def diff_stat(self) -> str:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD~1", "HEAD"],
            cwd=self.path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # First commit: show its stat instead.
            result = subprocess.run(
                ["git", "show", "--stat", "--oneline", "HEAD"],
                cwd=self.path,
                capture_output=True,
                text=True,
            )
        return result.stdout[:4000]
