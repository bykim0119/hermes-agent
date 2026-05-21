"""Tests for delegate_task_background — async/detached coder spawn (Task 3)."""
from unittest.mock import MagicMock, patch

from tools.delegate_tool import delegate_task_background


def test_returns_immediately_with_handle():
    """Background variant returns a coder_run_id without waiting for the child."""
    parent = MagicMock()
    parent.task_id = "parent-task-1"

    with patch("tools.delegate_tool._spawn_detached_coder") as mock_spawn:
        result = delegate_task_background(
            parent_agent=parent,
            goal="add function X to foo.py",
            context="file at /tmp/foo.py",
        )

    assert isinstance(result, dict)
    assert "coder_run_id" in result
    assert result["coder_run_id"].startswith("coder-")
    assert result["status"] == "spawned"
    assert result["goal"] == "add function X to foo.py"
    mock_spawn.assert_called_once()


def test_records_coder_run_for_thread_routing():
    """The spawned run is registered so gateway can map it to a Discord thread."""
    parent = MagicMock()
    parent.task_id = "parent-task-2"

    with patch("tools.delegate_tool._spawn_detached_coder"), \
         patch("tools.delegate_tool._register_coder_run") as mock_register:
        result = delegate_task_background(
            parent_agent=parent,
            goal="rename Y",
            context="",
        )

    mock_register.assert_called_once()
    call_args_str = str(mock_register.call_args)
    # The registration must link the generated coder_run_id with the parent task
    assert result["coder_run_id"] in call_args_str
    assert "parent-task-2" in call_args_str


# ---------------------------------------------------------------------------
# Task 10 — follow-up via codex exec resume
# ---------------------------------------------------------------------------


def test_followup_argv_inserts_resume_with_session_id(monkeypatch):
    """``_spawn_followup_coder`` builds ``codex exec resume <UUID> ...``.

    Verifies the resume subcommand + session UUID land right after ``exec`` so
    the conversation context carries over. Spawn itself is mocked so the test
    doesn't actually fork codex.
    """
    from tools.delegate_tool import _spawn_followup_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["command"] = command
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            captured["goal"] = goal
            captured["workspace"] = workspace
            if False:  # async generator that yields nothing
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: ("codex", ["exec", "--json"]),
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_followup_coder(
        coder_run_id="coder-abc",
        codex_session_id="uuid-zzz",
        text="and update the README too",
    )

    # Give the daemon thread a moment to construct the stub client.
    import time
    time.sleep(0.2)

    assert captured.get("command") == "codex"
    extras = captured.get("extra_args") or []
    # exec must come first, then resume + UUID, then the rest.
    assert extras[:4] == ["exec", "resume", "uuid-zzz", "--json"]
    # The prompt is passed as ``goal`` to client.run, not in extra_args.
    assert captured.get("goal") == "and update the README too"


def test_followup_argv_translates_sandbox_danger(monkeypatch):
    """``--sandbox danger-full-access`` → ``--dangerously-bypass-approvals-and-sandbox``.

    Regression for two live-smoke failures:
      1. ``error: unexpected argument '--sandbox' found`` — resume rejects the flag.
      2. ``bwrap: loopback: Failed RTM_NEWADDR`` — naïvely dropping the pair
         falls back to default ``workspace-write`` which crashes on this VM.

    The fix translates each ``--sandbox <mode>`` to the resume-compatible
    equivalent so the parent's effective sandbox mode is preserved.
    """
    from tools.delegate_tool import _spawn_followup_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            if False:
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: (
            "codex",
            [
                "exec", "--json", "--skip-git-repo-check",
                "--sandbox", "danger-full-access",
            ],
        ),
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_followup_coder(
        coder_run_id="coder-sandbox",
        codex_session_id="uuid-sandbox",
        text="follow",
    )
    import time
    time.sleep(0.2)

    extras = captured.get("extra_args") or []
    assert extras[:3] == ["exec", "resume", "uuid-sandbox"]
    assert "--sandbox" not in extras
    assert "danger-full-access" not in extras
    assert "--dangerously-bypass-approvals-and-sandbox" in extras
    assert "--json" in extras
    assert "--skip-git-repo-check" in extras


def test_followup_argv_translates_sandbox_workspace_write(monkeypatch):
    """``--sandbox workspace-write`` → ``--full-auto``."""
    from tools.delegate_tool import _spawn_followup_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            if False:
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: (
            "codex",
            ["exec", "--json", "--sandbox", "workspace-write"],
        ),
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_followup_coder(
        coder_run_id="coder-ws",
        codex_session_id="uuid-ws",
        text="follow",
    )
    import time
    time.sleep(0.2)

    extras = captured.get("extra_args") or []
    assert "--sandbox" not in extras
    assert "--full-auto" in extras


def test_fresh_spawn_keeps_sandbox(monkeypatch):
    """``_spawn_codex_coder`` (no resume_session_id) is a fresh ``codex exec``
    invocation — ``exec`` accepts ``--sandbox`` so it must be preserved verbatim.

    Used by ``/code`` slash command: brand-new coder thread starting from a
    cold codex session. The whole point of fresh-mode vs follow-up-mode is
    that resume sanitization (sandbox translation, profile dropping) only
    applies to resume.
    """
    from tools.delegate_tool import _spawn_codex_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            captured["goal"] = goal
            if False:
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: (
            "codex",
            [
                "exec", "--json", "--skip-git-repo-check",
                "--sandbox", "danger-full-access",
            ],
        ),
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_codex_coder(
        coder_run_id="coder-fresh",
        text="build calc",
        # no resume_session_id → fresh
    )
    import time
    time.sleep(0.2)

    extras = captured.get("extra_args") or []
    assert extras[0] == "exec"
    assert "resume" not in extras
    # Sandbox flag + value preserved (fresh exec accepts them).
    assert "--sandbox" in extras
    assert "danger-full-access" in extras
    # Goal is passed as prompt to client.run, not argv.
    assert captured.get("goal") == "build calc"


def test_fresh_spawn_prepends_exec_if_missing(monkeypatch):
    """Defensive: fresh spawn always starts with ``exec`` even if base args
    don't include it (config edge case)."""
    from tools.delegate_tool import _spawn_codex_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            if False:
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: ("codex", ["--json"]),
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_codex_coder(coder_run_id="coder-fp", text="x")
    import time
    time.sleep(0.2)

    extras = captured.get("extra_args") or []
    assert extras[0] == "exec"


def test_followup_argv_drops_profile_pair(monkeypatch):
    """``--profile NAME`` is rejected by resume; drop the pair (parent already
    used it to seed config)."""
    from tools.delegate_tool import _spawn_followup_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            if False:
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: ("codex", ["exec", "--json", "--profile", "myprof"]),
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_followup_coder(
        coder_run_id="coder-p",
        codex_session_id="uuid-p",
        text="hi",
    )
    import time
    time.sleep(0.2)

    extras = captured.get("extra_args") or []
    assert "--profile" not in extras
    assert "myprof" not in extras
    assert "--json" in extras


def test_followup_argv_handles_args_without_exec(monkeypatch):
    """Defensive: if base args don't start with 'exec', prepend it."""
    from tools.delegate_tool import _spawn_followup_coder

    captured = {}

    class _StubClient:
        def __init__(self, command, extra_args):
            captured["extra_args"] = list(extra_args)

        async def run(self, *, goal, workspace):
            if False:
                yield

    monkeypatch.setattr(
        "tools.delegate_tool._resolve_codex_command_and_args",
        lambda: ("codex", ["--json"]),  # missing the leading "exec"
    )
    monkeypatch.setattr(
        "agent.codex_exec_client.CodexExecClient", _StubClient
    )

    _spawn_followup_coder(
        coder_run_id="coder-def",
        codex_session_id="uuid-qq",
        text="ping",
    )
    import time
    time.sleep(0.2)

    extras = captured.get("extra_args") or []
    assert extras[:3] == ["exec", "resume", "uuid-qq"]
    assert "--json" in extras
