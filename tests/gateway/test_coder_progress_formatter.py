"""Tests for CoderProgressFormatter — subagent_progress events → emoji-prefixed strings.

Includes CJK (Korean) chunk regression cases per
project_acp_codex_orchestrator memo (OpenClaw bug #2).
"""
from gateway.coder_progress_formatter import format_event, MAX_CHUNK_CHARS


def test_read_file_event():
    out = format_event({"event": "tool_call", "tool": "read_file", "path": "src/foo.py"})
    assert out == "🔧 reading src/foo.py"


def test_edit_file_event_with_diff_stats():
    out = format_event({
        "event": "tool_call", "tool": "edit_file",
        "path": "src/foo.py", "added": 12, "removed": 3,
    })
    assert out == "✏️ editing src/foo.py (+12 -3)"


def test_terminal_command_event():
    out = format_event({"event": "tool_call", "tool": "terminal", "command": "pytest tests/"})
    assert out == "▶️ $ pytest tests/"


def test_success_completion():
    out = format_event({"event": "turn_complete", "summary": "함수 X 추가, 테스트 5개 통과"})
    assert out == "✅ 완료 — 함수 X 추가, 테스트 5개 통과"


def test_error_event():
    out = format_event({"event": "error", "message": "pytest failed: assert 1 == 2"})
    assert "❌" in out and "pytest failed" in out


def test_korean_text_preserves_spacing():
    """CJK text must not lose spacing (regression for OpenClaw chunk-trim bug #2)."""
    out = format_event({"event": "text_delta", "text": "현재 작업 디렉토리에서 파일을 읽는 중"})
    assert "현재 작업 디렉토리에서" in out
    assert "현재작업디렉토리" not in out


def test_long_chunks_are_capped():
    big = "x" * (MAX_CHUNK_CHARS + 500)
    out = format_event({"event": "text_delta", "text": big})
    assert len(out) <= MAX_CHUNK_CHARS + len("…[truncated]") + 10
    assert "[truncated]" in out


def test_unknown_event_returns_none():
    """Events we don't render skip silently (do not garbage-spam the thread)."""
    assert format_event({"event": "internal_metric", "value": 42}) is None


# ---------------------------------------------------------------------------
# Codex CLI NDJSON shape — direct passthrough from CodexExecClient
# ---------------------------------------------------------------------------


def test_codex_thread_started_drops_silently():
    assert format_event({"event": "thread.started", "data": {"thread_id": "t1"}}) is None


def test_codex_turn_started_drops_silently():
    assert format_event({"event": "turn.started", "data": {}}) is None


def test_codex_agent_message_renders_text():
    out = format_event({
        "event": "item.completed",
        "data": {"item": {"type": "agent_message", "text": "patched config"}},
    })
    assert out == "patched config"


def test_codex_local_shell_call_renders_command():
    out = format_event({
        "event": "item.completed",
        "data": {"item": {"type": "local_shell_call", "command": "pytest"}},
    })
    assert out == "▶️ pytest"


def test_codex_command_execution_renders_command():
    """Codex 0.121.0 emits item.type=command_execution for shell runs."""
    out = format_event({
        "event": "item.completed",
        "data": {"item": {
            "type": "command_execution",
            "command": "ls /tmp",
            "exit_code": 0,
            "status": "completed",
        }},
    })
    assert out == "▶️ ls /tmp"


def test_codex_command_execution_strips_bash_wrapper():
    """``/bin/bash -lc "<actual>"`` wrapping must collapse to the inner command."""
    out = format_event({
        "event": "item.completed",
        "data": {"item": {
            "type": "command_execution",
            "command": '/bin/bash -lc "printf \'hi\' > /tmp/x"',
            "exit_code": 0,
        }},
    })
    assert out == "▶️ printf 'hi' > /tmp/x"


def test_codex_command_execution_marks_nonzero_exit():
    out = format_event({
        "event": "item.completed",
        "data": {"item": {
            "type": "command_execution",
            "command": "false",
            "exit_code": 1,
        }},
    })
    assert "▶️ false" in out
    assert "exit 1" in out


def test_codex_item_started_drops_silently():
    """item.started is followed immediately by item.completed — render only one."""
    out = format_event({
        "event": "item.started",
        "data": {"item": {"type": "command_execution", "command": "echo hi"}},
    })
    assert out is None


def test_codex_turn_completed_with_usage():
    out = format_event({
        "event": "turn.completed",
        "data": {"usage": {"input_tokens": 100, "output_tokens": 42}},
    })
    assert out == "✅ 완료 (42 out tokens)"


def test_codex_error_includes_stderr():
    out = format_event({
        "event": "error",
        "data": {"returncode": 1, "stderr": "boom"},
    })
    assert "❌" in out and "boom" in out
