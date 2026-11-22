"""Test-env provisioning for generated projects."""

import asyncio
import os
import sys

from app.services.build import _venv_python, detect_test_command, run_tests
from app.services.workspace import Workspace


def _make_ws(tmp_path):
    ws = Workspace(tmp_path, "proj")
    ws.ensure()
    return ws


def test_detect_pytest_command(tmp_path):
    ws = _make_ws(tmp_path)
    (ws.path / "requirements.txt").write_text("fastapi\n")
    assert detect_test_command(ws) == ["python", "-m", "pytest", "-q"]


def test_run_tests_uses_existing_venv(tmp_path):
    """Pre-made .venv/bin/python must be used for the test run — no network.
    PYTHONPATH is exported so the symlinked interpreter can import pytest."""
    ws = _make_ws(tmp_path)
    (ws.path / "requirements.txt").write_text("")  # nothing to install
    venv_bin = ws.path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    os.symlink(sys.executable, venv_bin / "python")
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    (ws.path / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    code, out = asyncio.run(run_tests(ws))
    assert code == 0, out
    assert "1 passed" in out


def test_run_tests_reports_failing_tests(tmp_path):
    ws = _make_ws(tmp_path)
    (ws.path / "requirements.txt").write_text("")
    venv_bin = ws.path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    os.symlink(sys.executable, venv_bin / "python")
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    (ws.path / "test_bad.py").write_text("def test_bad():\n    assert False\n")

    code, out = asyncio.run(run_tests(ws))
    assert code != 0
    assert "1 failed" in out


def test_venv_python_resolution(tmp_path):
    ws = _make_ws(tmp_path)
    assert _venv_python(ws.path) is None
    (ws.path / ".venv" / "bin").mkdir(parents=True)
    vp = ws.path / ".venv" / "bin" / "python"
    vp.touch()
    assert _venv_python(ws.path) == vp
