"""Build pipeline: sequential dependency-aware execution.

Per task: OpenCode run -> test command -> Reviewer verdict -> bounded fix
loop. All state transitions are persisted; a restart resumes at the current
task with stale attempts requeued.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..agents.prompts import DEV_TASK_PREAMBLE, REVIEWER_SYSTEM
from ..agents.schemas import ReviewVerdict
from ..config import Settings
from ..llm.gateway import LLMClient
from ..llm.structured import chat_json
from ..models import Artifact, ArtifactVersion, ExecutionLog, Project, ProjectState, Task, TaskAttempt, TaskStatus
from .events import EventBus
from .opencode import ExecResult, Executor, redact
from .planning import _promote_ready
from .workspace import Workspace, slugify

logger = logging.getLogger("cf.build")

MAX_TEST_OUTPUT = 8000
TEST_TIMEOUT = 600


class BuildContext:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        llm: LLMClient,
        executor: Executor,
        bus: EventBus,
        settings: Settings,
    ):
        self.sessions = sessions
        self.llm = llm
        self.executor = executor
        self.bus = bus
        self.settings = settings


def prepare_workspace(settings: Settings, project: Project) -> Workspace:
    ws = Workspace(Path(settings.workspaces_dir), slugify(project.name))
    ws.ensure()
    return ws


def detect_test_command(workspace: Workspace) -> list[str] | None:
    p = workspace.path
    if (p / "package.json").exists():
        try:
            pkg = json.loads((p / "package.json").read_text())
        except ValueError:
            pkg = {}
        if "test" in (pkg.get("scripts") or {}):
            return ["npm", "test"]
    if (
        any((p / f).exists() for f in ("pyproject.toml", "pytest.ini", "setup.py", "requirements.txt"))
        or (p / "tests").is_dir()
    ):
        return ["python", "-m", "pytest", "-q"]
    return None


def _venv_python(path: Path) -> Path | None:
    for cand in (".venv/bin/python", ".venv/Scripts/python.exe", ".venv/Scripts/python"):
        p = path / cand
        if p.exists():
            return p
    return None


VENV_TIMEOUT = 180
DEPS_TIMEOUT = 900


async def provision_test_env(workspace: Workspace) -> str:
    """Install the generated project's dependencies where the test command
    can see them: a project-local .venv for Python, node_modules for Node.
    Idempotent; safe to call before every attempt."""
    p = workspace.path
    notes: list[str] = []

    if (p / "requirements.txt").exists() and _venv_python(p) is None:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "venv", ".venv", cwd=str(p),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=VENV_TIMEOUT)
            notes.append("created .venv" if proc.returncode == 0 else f".venv creation failed: {(err or out).decode(errors='replace')[:300]}")
        except asyncio.TimeoutError:
            proc.kill()
            notes.append(".venv creation timed out")

    vp = _venv_python(p)
    if vp is not None:
        # Does the venv already run tests? Skip the pytest install if so.
        probe = await asyncio.create_subprocess_exec(
            str(vp), "-m", "pytest", "--version",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
        )
        await probe.communicate()
        need_pytest = probe.returncode != 0
        req = p / "requirements.txt"
        if req.exists() and req.read_text().strip():
            proc = await asyncio.create_subprocess_exec(
                str(vp), "-m", "pip", "install", "-q", "-r", "requirements.txt",
                cwd=str(p), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=DEPS_TIMEOUT)
                if proc.returncode == 0:
                    notes.append("installed requirements.txt into .venv")
                else:
                    notes.append(f"pip install failed: {(err or out).decode(errors='replace')[-500:]}")
            except asyncio.TimeoutError:
                proc.kill()
                notes.append(f"pip install timed out after {DEPS_TIMEOUT}s")
        if need_pytest:
            proc = await asyncio.create_subprocess_exec(
                str(vp), "-m", "pip", "install", "-q", "pytest",
                cwd=str(p), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=DEPS_TIMEOUT)
                notes.append("installed pytest" if proc.returncode == 0 else f"pytest install failed: {(err or out).decode(errors='replace')[-300:]}")
            except asyncio.TimeoutError:
                proc.kill()
                notes.append("pytest install timed out")

    if (p / "package.json").exists() and not (p / "node_modules").exists():
        use_ci = (p / "package-lock.json").exists()
        cmd = ["npm", "ci" if use_ci else "install", "--no-audit", "--no-fund"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(p),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=DEPS_TIMEOUT)
            notes.append("npm deps ok" if proc.returncode == 0 else f"npm deps failed: {(err or out).decode(errors='replace')[-500:]}")
        except asyncio.TimeoutError:
            proc.kill()
            notes.append(f"npm deps timed out after {DEPS_TIMEOUT}s")
    return "\n".join(notes)


async def run_tests(workspace: Workspace) -> tuple[int, str]:
    env_notes = await provision_test_env(workspace)
    cmd = detect_test_command(workspace)
    if cmd is None:
        return 0, "(no test command detected)"
    vp = _venv_python(workspace.path)
    if vp is not None and cmd[:2] == ["python", "-m"]:
        cmd = [str(vp)] + cmd[1:]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, f"TIMEOUT after {TEST_TIMEOUT}s running: {' '.join(cmd)}"
    except FileNotFoundError:
        return 127, f"test command not found: {' '.join(cmd)}"
    out = redact((stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace")).strip())
    if env_notes:
        out = f"[env] {env_notes}\n{out}"
    return proc.returncode, out[-MAX_TEST_OUTPUT:]


async def run_build(ctx: BuildContext, project_id: str) -> None:
    """Main sequential scheduler loop. Resumable: re-queries state each pass."""
    with ctx.sessions() as session:
        project = session.get(Project, project_id)
        if project is None:
            return

        # Crash recovery: tasks left IN_PROGRESS by a dead run are requeued,
        # and their dangling RUNNING attempts are closed as INTERRUPTED. An
        # interrupted attempt does not count against the retry budget.
        for t in session.execute(
            select(Task).where(Task.project_id == project_id, Task.status == TaskStatus.IN_PROGRESS)
        ).scalars():
            t.status = TaskStatus.READY
            for a in session.execute(
                select(TaskAttempt).where(TaskAttempt.task_id == t.id, TaskAttempt.status == "RUNNING")
            ).scalars():
                a.status = "INTERRUPTED"
                a.result_summary = (a.result_summary or "") + "interrupted by restart"
            ctx.bus.record(session, project_id, "task_requeued", {"task": t.number, "reason": "stale after restart"})
        if project.state in (ProjectState.PLANNING, ProjectState.BUILDING):
            project.state = ProjectState.BUILDING
        session.commit()

        ws = prepare_workspace(ctx.settings, project)
        _write_spec_files(session, project, ws)
        session.commit()

        interrupted_counts: dict[str, int] = {}

        while True:
            _promote_ready(session, project_id)
            session.commit()
            task = session.execute(
                select(Task)
                .where(Task.project_id == project_id, Task.status == TaskStatus.READY)
                .order_by(Task.number)
                .limit(1)
            ).scalar_one_or_none()
            if task is None:
                break
            await _execute_attempt(ctx, session, project, task, ws, interrupted_counts)

        tasks = session.execute(select(Task).where(Task.project_id == project_id)).scalars().all()
        failed = [t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.BLOCKED)]
        if failed:
            project.state = ProjectState.FAILED
            project.error = f"{len(failed)} task(s) failed: {', '.join(str(t.number) for t in failed)}"
            ctx.bus.record(session, project_id, "build_failed", {"failed": [t.number for t in failed]})
        else:
            await _finish(ctx, session, project, ws)
        session.commit()


def _write_spec_files(session: Session, project: Project, ws: Workspace) -> None:
    """Copy current artifact versions into the workspace for OpenCode to read."""
    spec_dir = ws.path / ".codingfactory"
    spec_dir.mkdir(exist_ok=True)
    for artifact in project.artifacts:
        version = _current_version(session, artifact)
        if version is not None:
            (spec_dir / artifact.slug).write_text(version.content)
    gitignore = ws.path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("node_modules/\n__pycache__/\n.codingfactory/\n")


def _current_version(session: Session, artifact: Artifact) -> ArtifactVersion | None:
    if artifact.current_version == 0:
        return None
    return session.execute(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.version == artifact.current_version
        )
    ).scalar_one_or_none()


async def _execute_attempt(
    ctx: BuildContext, session: Session, project: Project, task: Task, ws: Workspace, interrupted_counts: dict[str, int]
) -> None:
    task.attempts += 1
    task.status = TaskStatus.IN_PROGRESS
    attempt = TaskAttempt(task_id=task.id, attempt_number=task.attempts, status="RUNNING")
    session.add(attempt)
    session.flush()
    ctx.bus.record(session, project.id, "task_started", {"task": task.number, "attempt": attempt.attempt_number})
    session.commit()

    instructions = _task_instructions(project, task)
    started = time.monotonic()
    result = await ctx.executor.execute(ws, instructions)
    _log(session, attempt.id, "command", f"opencode run (task {task.number}, attempt {attempt.attempt_number})")
    _log(session, attempt.id, "stdout", (result.final_text or result.stdout_tail)[:MAX_TEST_OUTPUT])
    if result.stderr_tail:
        _log(session, attempt.id, "stderr", result.stderr_tail)

    if not result.ok:
        if result.exit_code in (143, 137):
            # Killed by a signal (SIGTERM/SIGKILL — e.g. container restart).
            # Not the task's fault: requeue without consuming an attempt, but
            # bound consecutive interruptions so we can't loop forever.
            _close_attempt(session, attempt, "INTERRUPTED", result.exit_code, "executor terminated by signal; requeued", started)
            interrupted_counts[task.id] = interrupted_counts.get(task.id, 0) + 1
            if interrupted_counts[task.id] > 3 or _consumed_attempts(session, task) >= task.max_attempts:
                task.status = TaskStatus.FAILED
                ctx.bus.record(session, project.id, "task_failed", {"task": task.number, "reason": "repeatedly interrupted"})
            else:
                task.status = TaskStatus.READY
                ctx.bus.record(session, project.id, "task_interrupted", {"task": task.number, "exit": result.exit_code})
            session.commit()
            return
        _close_attempt(session, attempt, "FAILED", result.exit_code, f"executor failure: {result.notes or 'non-zero exit'}", started)
        task.status = TaskStatus.FAILED if _consumed_attempts(session, task) >= task.max_attempts else TaskStatus.READY
        ctx.bus.record(session, project.id, "task_attempt_failed", {"task": task.number, "attempt": attempt.attempt_number})
        session.commit()
        return

    test_code, test_out = await run_tests(ws)
    _log(session, attempt.id, "command", " ".join(detect_test_command(ws) or ["(none)"]))
    _log(session, attempt.id, "stdout", test_out)

    review = await _reviewer(ctx, task, result, test_code, test_out)
    _log(session, attempt.id, "review", f"{review.verdict}: {review.summary}")
    commit = ws.commit_all(f"task {task.number}: {task.title[:80]} (attempt {attempt.attempt_number})")

    if review.verdict == "approve" and test_code == 0:
        task.status = TaskStatus.COMPLETED
        _close_attempt(session, attempt, "PASSED", 0, f"approved; commit={commit}", started)
        ctx.bus.record(session, project.id, "task_completed", {"task": task.number, "commit": commit})
    elif review.verdict == "approve":
        # Tests failing overrides reviewer approval — never report unverified success.
        _close_attempt(session, attempt, "FAILED", test_code, f"reviewer approved but tests failed (exit {test_code})", started)
        task.status = TaskStatus.FAILED if _consumed_attempts(session, task) >= task.max_attempts else TaskStatus.READY
        ctx.bus.record(session, project.id, "task_tests_failed", {"task": task.number, "exit": test_code})
    else:
        _close_attempt(session, attempt, "FAILED", None, f"changes requested: {'; '.join(review.issues)[:500]}", started)
        if _consumed_attempts(session, task) >= task.max_attempts:
            task.status = TaskStatus.FAILED
            ctx.bus.record(session, project.id, "task_failed", {"task": task.number})
        else:
            task.status = TaskStatus.READY
            task.description += "\n\nFIX REQUESTED:\n" + "\n".join(
                f"- {i}" for i in (review.fix_instructions or review.issues)
            )
            ctx.bus.record(session, project.id, "task_changes_requested", {"task": task.number, "issues": review.issues[:5]})
    session.commit()


def _task_instructions(project: Project, task: Task) -> str:
    spec = [f"- .codingfactory/{a.slug}" for a in project.artifacts if a.slug.endswith(".md")]
    return (
        f"{DEV_TASK_PREAMBLE}\n\n"
        f"PROJECT: {project.name}\n"
        f"Approved specs: {', '.join(spec)} — read what you need.\n\n"
        f"TASK {task.number}: {task.title}\n\n{task.description}\n\n"
        f"ACCEPTANCE CRITERIA:\n{task.acceptance_criteria}\n\n"
        "When done, summarize what you changed and how each acceptance criterion is met."
    )


async def _reviewer(
    ctx: BuildContext, task: Task, result: ExecResult, test_code: int, test_out: str
) -> ReviewVerdict:
    return await chat_json(
        ctx.llm,
        [
            {"role": "system", "content": REVIEWER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"TASK {task.number}: {task.title}\n\nDESCRIPTION:\n{task.description}\n\n"
                    f"ACCEPTANCE CRITERIA:\n{task.acceptance_criteria}\n\n"
                    f"DEVELOPER SUMMARY:\n{result.final_text[:4000] or '(none)'}\n\n"
                    f"TEST RUN (exit {test_code}):\n{test_out}\n\nVerdict?"
                ),
            },
        ],
        ReviewVerdict,
        repair_attempts=ctx.settings.max_agent_output_repair_attempts,
    )


def _log(session: Session, attempt_id: str, kind: str, content: str) -> None:
    session.add(ExecutionLog(attempt_id=attempt_id, kind=kind, content=redact(content)[:100_000]))


def _consumed_attempts(session: Session, task: Task) -> int:
    """Attempts that count against the retry budget (interruptions don't)."""
    from sqlalchemy import func

    return session.execute(
        select(func.count())
        .select_from(TaskAttempt)
        .where(TaskAttempt.task_id == task.id, TaskAttempt.status.in_(("PASSED", "FAILED")))
    ).scalar_one()


def _close_attempt(
    session: Session, attempt: TaskAttempt, status: str, exit_code: int | None, summary: str, started: float
) -> None:
    attempt.status = status
    attempt.exit_code = exit_code
    attempt.result_summary = summary
    attempt.finished_at = datetime.now(timezone.utc)
    attempt.duration_ms = int((time.monotonic() - started) * 1000)


async def _finish(ctx: BuildContext, session: Session, project: Project, ws: Workspace) -> None:
    tasks = session.execute(
        select(Task).where(Task.project_id == project.id).order_by(Task.number)
    ).scalars().all()
    test_code, test_out = await run_tests(ws)
    lines = ["# Delivery Report", "", f"Project: {project.name}", ""]
    for t in tasks:
        lines.append(f"- Task {t.number}: {t.title} — {t.status.value} ({t.attempts} attempt(s))")
    lines += ["", f"Final test run (exit {test_code}):", "```", test_out[-3000:], "```"]
    commit = ws.commit_all("codingfactory: delivery baseline")
    lines += ["", f"Workspace: workspaces/{ws.path.name}", f"Head commit: {commit or 'n/a'}"]
    report = Artifact(project_id=project.id, slug="delivery-report.md", title="Delivery Report", current_version=1)
    session.add(report)
    session.flush()
    session.add(
        ArtifactVersion(artifact_id=report.id, version=1, content="\n".join(lines), created_by="release-manager")
    )
    project.state = ProjectState.COMPLETED
    ctx.bus.record(session, project.id, "completed", {"workspace": ws.path.name})
