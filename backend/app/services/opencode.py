"""OpenCode execution adapter.

Verified against OpenCode v2.3.2:
    opencode run --format json --auto [--model provider/model] [message...]
JSON format emits NDJSON events; text parts carry assistant output,
step_finish carries token usage. cwd (the workspace) scopes file access.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from ..config import Settings
from .workspace import Workspace

logger = logging.getLogger("cf.opencode")

MAX_LOG_CHARS = 40_000

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return text


@dataclass
class ExecResult:
    ok: bool
    exit_code: int
    final_text: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    notes: str = ""


class Executor(Protocol):
    async def execute(self, workspace: Workspace, instructions: str) -> ExecResult: ...


def parse_opencode_events(stdout: str) -> tuple[str, str]:
    """Return (final_text, error_note) from NDJSON event output."""
    texts: list[str] = []
    errors: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        etype = event.get("type")
        part = event.get("part") or {}
        if etype == "text" and part.get("type") == "text":
            texts.append(part.get("text", ""))
        elif etype in ("error",):
            errors.append(json.dumps(event)[:500])
        elif part.get("type") == "tool-error":
            errors.append(part.get("error") or part.get("message") or "tool error")
    return ("\n".join(texts), "; ".join(errors))


class OpenCodeExecutor:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, workspace: Workspace, instructions: str) -> ExecResult:
        cmd = [self.settings.opencode_bin, "run", "--format", "json", "--auto"]
        if self.settings.opencode_model:
            cmd += ["--model", self.settings.opencode_model]
        cmd.append(instructions)

        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return ExecResult(
                ok=False,
                exit_code=-1,
                notes=f"OpenCode binary not found: {self.settings.opencode_bin}",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.settings.opencode_timeout
            )
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            stdout_b, stderr_b = await proc.communicate()
            timed_out = True

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = redact(stdout_b.decode(errors="replace"))
        stderr = redact(stderr_b.decode(errors="replace"))
        final_text, error_note = parse_opencode_events(stdout)

        return ExecResult(
            ok=(proc.returncode == 0 and not timed_out),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            final_text=final_text[-MAX_LOG_CHARS:],
            duration_ms=duration_ms,
            timed_out=timed_out,
            stdout_tail=stdout[-MAX_LOG_CHARS:],
            stderr_tail=stderr[-4000:],
            notes=error_note,
        )


@dataclass
class MockExecutor:
    """Test double: scripted results, records instructions it was given."""

    results: list[ExecResult] = field(default_factory=list)
    instructions_seen: list[str] = field(default_factory=list)

    async def execute(self, workspace: Workspace, instructions: str) -> ExecResult:
        self.instructions_seen.append(instructions)
        if self.results:
            return self.results.pop(0)
        return ExecResult(ok=True, exit_code=0, final_text="mock: nothing scripted")
