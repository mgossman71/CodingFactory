"""OpenCode adapter: event parsing, fake binary execution, timeout, redaction."""

import json
import stat
import textwrap

import pytest

from app.config import Settings
from app.services.opencode import OpenCodeExecutor, parse_opencode_events, redact
from app.services.workspace import Workspace


def test_parse_text_and_tokens():
    lines = [
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": "SMOKE_OK"}}),
        json.dumps({"type": "step_finish", "part": {"type": "step-finish", "tokens": {"input": 1, "output": 1}}}),
    ]
    final, err = parse_opencode_events("\n".join(lines))
    assert final == "SMOKE_OK"
    assert err == ""


def test_parse_ignores_non_json_and_captures_errors():
    out = "welcome noise\n" + json.dumps({"type": "error", "error": {"name": "Boom"}})
    final, err = parse_opencode_events(out)
    assert final == ""
    assert "Boom" in err


def test_redact_secrets():
    text = "export API_KEY=abc123 and token: hunter2"
    out = redact(text)
    assert "abc123" not in out
    assert "hunter2" not in out


@pytest.fixture
def fake_opencode(tmp_path):
    """Shell script that emits NDJSON like OpenCode and exits with the requested code."""
    script = tmp_path / "fake-opencode.sh"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            if [ "$SLEEP_MODE" = "1" ]; then sleep 30; fi
            echo '{"type":"text","part":{"type":"text","text":"did the task"}}'
            echo '{"type":"step_finish","part":{"type":"step-finish"}}'
            exit ${EXIT_CODE:-0}
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


async def test_execute_success(tmp_path, fake_opencode):
    settings = Settings(_env_file=None, opencode_bin=fake_opencode)
    ws = Workspace(tmp_path / "ws", "proj")
    ws.ensure()
    result = await OpenCodeExecutor(settings).execute(ws, "implement task 1")
    assert result.ok
    assert result.exit_code == 0
    assert result.final_text == "did the task"
    assert result.duration_ms > 0


async def test_execute_detects_failure(tmp_path, fake_opencode):
    import os

    os.environ["EXIT_CODE"] = "3"
    try:
        settings = Settings(_env_file=None, opencode_bin=fake_opencode)
        ws = Workspace(tmp_path / "ws", "proj")
        ws.ensure()
        result = await OpenCodeExecutor(settings).execute(ws, "task")
        assert not result.ok
        assert result.exit_code == 3
    finally:
        del os.environ["EXIT_CODE"]


async def test_execute_timeout(tmp_path, fake_opencode):
    import os

    os.environ["SLEEP_MODE"] = "1"
    try:
        settings = Settings(_env_file=None, opencode_bin=fake_opencode, opencode_timeout=30)
        ws = Workspace(tmp_path / "ws", "proj")
        ws.ensure()
        # patch the timeout to 1s for a fast test
        settings.opencode_timeout = 1
        result = await OpenCodeExecutor(settings).execute(ws, "task")
        assert result.timed_out
        assert not result.ok
    finally:
        del os.environ["SLEEP_MODE"]


async def test_execute_missing_binary(tmp_path):
    settings = Settings(_env_file=None, opencode_bin="/nonexistent/opencode")
    ws = Workspace(tmp_path / "ws", "proj")
    ws.ensure()
    result = await OpenCodeExecutor(settings).execute(ws, "task")
    assert not result.ok
    assert "not found" in result.notes
