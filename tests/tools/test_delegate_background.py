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


# ---------------------------------------------------------------------------
# Task 11 — cancellation (TDD)
# ---------------------------------------------------------------------------


def test_cancel_unknown_run_returns_false():
    """Calling ``cancel_coder_run`` on an id that isn't registered must be a
    no-op that returns False — never raises, never mutates the registry.

    This is the cheapest behavior to lock in first: it guarantees the
    cancel surface is safe to call from message handlers without prior
    existence checks, which keeps the Discord adapter code dumber.
    """
    from tools.delegate_tool import _CODER_RUN_REGISTRY, cancel_coder_run

    assert "coder-does-not-exist" not in _CODER_RUN_REGISTRY
    assert cancel_coder_run("coder-does-not-exist") is False
    assert "coder-does-not-exist" not in _CODER_RUN_REGISTRY


def test_cancel_terminates_attached_client_and_marks_status():
    """When a client has been attached for this coder_run_id, cancel calls
    ``client.terminate()`` and flips registry status to ``cancelled``.

    The attach happens in the spawn paths (covered by a separate test) —
    here we install the client by hand to isolate the cancel behavior.
    Status change is what surfaces "this run is dead" to other gateway
    code (e.g. the event bus, which could otherwise keep flushing
    debounced events to a closed thread)."""
    from tools.delegate_tool import (
        _CODER_RUN_REGISTRY,
        _attach_coder_client,
        _register_coder_run,
        cancel_coder_run,
    )

    coder_run_id = "coder-cancel-with-client"
    _register_coder_run(coder_run_id, "parent-task-A", "do thing")

    fake_client = MagicMock()
    fake_client.terminate.return_value = True
    _attach_coder_client(coder_run_id, fake_client)

    try:
        with patch("tools.delegate_tool.interrupt_subagent", return_value=False):
            ok = cancel_coder_run(coder_run_id)

        assert ok is True
        fake_client.terminate.assert_called_once()
        assert _CODER_RUN_REGISTRY[coder_run_id]["status"] == "cancelled"
    finally:
        _CODER_RUN_REGISTRY.pop(coder_run_id, None)


def test_cancel_falls_back_to_interrupt_subagent_when_no_client():
    """The natural-language path's facade may not have attached its client
    yet if cancellation lands during the boot window (registry rec
    exists but ``client`` not set). In that case we still want cancel to
    take effect via ``interrupt_subagent`` — its parent AIAgent shell
    will tear the in-flight chat completion down."""
    from tools.delegate_tool import (
        _CODER_RUN_REGISTRY,
        _register_coder_run,
        cancel_coder_run,
    )

    coder_run_id = "coder-cancel-no-client"
    _register_coder_run(coder_run_id, "parent-task-B", "another")
    assert _CODER_RUN_REGISTRY[coder_run_id].get("client") is None

    try:
        with patch("tools.delegate_tool.interrupt_subagent", return_value=True):
            ok = cancel_coder_run(coder_run_id)
        assert ok is True
        assert _CODER_RUN_REGISTRY[coder_run_id]["status"] == "cancelled"
    finally:
        _CODER_RUN_REGISTRY.pop(coder_run_id, None)


def test_spawn_codex_coder_attaches_client_to_registry():
    """The slash and follow-up spawn path must register its client into the
    coder run record. Without this, ``cancel_coder_run`` finds the rec
    but ``rec.get("client")`` is None and the kill never happens.

    Mocks CodexExecClient so the test doesn't actually fork codex; we
    just need to observe that the client instance becomes reachable."""
    from tools.delegate_tool import (
        _CODER_RUN_REGISTRY,
        _register_coder_run,
        _spawn_codex_coder,
    )

    captured_client = {}

    class _FakeClient:
        def __init__(self, command=None, extra_args=None):
            captured_client["instance"] = self
            self.command = command
            self.extra_args = list(extra_args or [])

        async def run(self, *, goal, workspace, env=None):
            if False:  # pragma: no cover - generator that yields nothing
                yield None

    coder_run_id = "coder-attach-spawn"
    _register_coder_run(coder_run_id, "parent-task-S", "spawn-test")

    try:
        with patch("agent.codex_exec_client.CodexExecClient", _FakeClient), \
             patch(
                 "tools.delegate_tool._resolve_codex_command_and_args",
                 return_value=("codex", ["exec", "--json"]),
             ):
            _spawn_codex_coder(coder_run_id, "hello")

        import time
        time.sleep(0.2)
        assert _CODER_RUN_REGISTRY[coder_run_id].get("client") is captured_client.get("instance")
    finally:
        _CODER_RUN_REGISTRY.pop(coder_run_id, None)


def test_is_cancel_command_recognizes_bang_prefixed_tokens():
    """``!cancel`` and ``!stop`` (case-insensitive, surrounding whitespace
    tolerated) are the only triggers. The ``!`` prefix is required so
    natural follow-up messages that happen to contain the word ``cancel``
    (e.g. "cancel that approach and try again") still flow to codex as
    real instructions."""
    from tools.delegate_tool import is_cancel_command

    assert is_cancel_command("!cancel") is True
    assert is_cancel_command("!stop") is True
    assert is_cancel_command("  !CANCEL  ") is True
    assert is_cancel_command("!Stop") is True


def test_delegate_task_background_short_circuits_on_bad_auth():
    """A missing/expired codex auth surfaces as a structured error before
    we spawn anything. Otherwise codex would fail mid-NDJSON-stream and
    the user would see an opaque ``returncode=N`` inside their thread
    instead of "your token is expired, run `codex login`".
    """
    from tools.delegate_tool import delegate_task_background

    parent = MagicMock()
    parent.task_id = "parent-auth"

    with patch(
        "gateway.coder_config.check_codex_auth",
        return_value="Codex OAuth 만료 — `codex login` 재실행 필요",
    ), patch("tools.delegate_tool._spawn_detached_coder") as mock_spawn:
        result = delegate_task_background(
            parent_agent=parent,
            goal="rename Y",
            context="",
        )

    mock_spawn.assert_not_called()
    assert result.get("status") == "auth_error"
    assert "만료" in result.get("error", "")
    assert result.get("coder_run_id") is None


def test_resolve_codex_command_falls_back_to_config_when_env_unset(monkeypatch):
    """When the auth resolver returns no creds and no env vars are set,
    ``_resolve_codex_command_and_args`` must consult ``delegation.coder``
    in config.yaml before falling to the hardcoded default.

    Without this, operators who set ``args:`` in config.yaml would still
    see codex spawn with ``workspace-write`` (the default) because the
    old behavior dropped straight from "no env" to "default", skipping
    config entirely."""
    from tools.delegate_tool import _resolve_codex_command_and_args

    monkeypatch.delenv("HERMES_CODER_COMMAND", raising=False)
    monkeypatch.delenv("HERMES_CODER_ARGS", raising=False)
    with patch(
        "hermes_cli.auth.resolve_external_process_provider_credentials",
        return_value={"command": None, "args": []},
    ), patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {
            "command": "codex-from-config",
            "args": "exec --json --skip-git-repo-check --sandbox danger-full-access",
        }}},
    ):
        cmd, args = _resolve_codex_command_and_args()

    assert cmd == "codex-from-config"
    assert "--sandbox" in args
    assert "danger-full-access" in args


def test_resolve_codex_command_config_overrides_auth_resolver_args(monkeypatch):
    """When config has explicit ``args`` set, it wins over whatever the
    credential auth resolver returns. Without this, config.yaml as the
    durable home for codex args is meaningless — auth would always
    return ``workspace-write`` defaults and silently shadow operator
    intent expressed in config.

    Priority: env > delegation.coder.args (config) > auth resolver creds > hardcoded default.
    """
    from tools.delegate_tool import _resolve_codex_command_and_args

    monkeypatch.delenv("HERMES_CODER_COMMAND", raising=False)
    monkeypatch.delenv("HERMES_CODER_ARGS", raising=False)
    with patch(
        "hermes_cli.auth.resolve_external_process_provider_credentials",
        return_value={
            "command": "/usr/bin/codex",
            "args": ["exec", "--json", "--skip-git-repo-check",
                     "--sandbox", "workspace-write"],
        },
    ), patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {
            "args": "exec --json --skip-git-repo-check --sandbox danger-full-access",
        }}},
    ):
        cmd, args = _resolve_codex_command_and_args()

    # config's --sandbox value wins over auth's workspace-write
    assert "danger-full-access" in args
    assert "workspace-write" not in args
    # command falls through to auth since config didn't set one
    assert cmd == "/usr/bin/codex"


def test_is_cancel_command_rejects_plain_words_and_followups():
    """Words without the bang prefix must NOT trigger cancellation —
    those are valid follow-up instructions to codex."""
    from tools.delegate_tool import is_cancel_command

    assert is_cancel_command("cancel") is False
    assert is_cancel_command("stop") is False
    assert is_cancel_command("cancel that approach") is False
    assert is_cancel_command("!cancel that") is False  # extra args, not a clean cancel
    assert is_cancel_command("") is False
    assert is_cancel_command(None) is False
