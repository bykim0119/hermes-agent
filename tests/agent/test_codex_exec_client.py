"""Tests for codex_exec_client — A1 path (codex exec --json) per Task 2 of
the hermes-coder-subagent plan."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from agent.codex_exec_client import CodexExecClient, CodexEvent, CodexExecFacade


@pytest.mark.asyncio
async def test_run_emits_text_delta_events():
    """codex exec --json output lines are parsed into CodexEvent objects."""
    fake_stdout = b'{"event":"text_delta","text":"hello"}\n{"event":"turn_complete"}\n'

    class FakeProc:
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()
        returncode = 0

        async def wait(self):
            return 0

    proc = FakeProc()
    proc.stdout.feed_data(fake_stdout)
    proc.stdout.feed_eof()
    proc.stderr.feed_eof()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        client = CodexExecClient(command="codex")
        events = [e async for e in client.run(goal="say hi", workspace="/tmp")]

    assert any(e.event == "text_delta" and e.data["text"] == "hello" for e in events)
    assert any(e.event == "turn_complete" for e in events)


@pytest.mark.asyncio
async def test_malformed_json_lines_yield_raw_event():
    """Malformed JSON lines surface as raw events instead of crashing."""
    fake_stdout = b'not json at all\n{"event":"turn_complete"}\n'

    class FakeProc:
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()
        returncode = 0

        async def wait(self):
            return 0

    proc = FakeProc()
    proc.stdout.feed_data(fake_stdout)
    proc.stdout.feed_eof()
    proc.stderr.feed_eof()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        client = CodexExecClient(command="codex")
        events = [e async for e in client.run(goal="x", workspace="/tmp")]

    assert any(e.event == "raw" for e in events)


# ---------------------------------------------------------------------------
# CodexExecFacade — OpenAI-shape wrapper used by auxiliary_client provider
# ---------------------------------------------------------------------------


class _FakeClient:
    """Test double for CodexExecClient — yields a fixed event sequence."""

    def __init__(self, events):
        self._events = events
        self.captured_goal = None
        self.captured_workspace = None

    async def run(self, *, goal, workspace, env=None):
        self.captured_goal = goal
        self.captured_workspace = workspace
        for e in self._events:
            yield e


def test_facade_collects_agent_message_into_content():
    """agent_message item text is exposed via choices[0].message.content."""
    fake = _FakeClient([
        CodexEvent("thread.started", {"thread_id": "t1"}),
        CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "done"}}),
        CodexEvent("turn.completed", {"usage": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 0}}),
    ])
    facade = CodexExecFacade(workspace="/tmp", _client=fake)
    resp = facade.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "done"


def test_facade_finish_reason_is_stop():
    """Codex internally handles tools, so facade always reports finish_reason=stop."""
    fake = _FakeClient([
        CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "ok"}}),
        CodexEvent("turn.completed", {"usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}}),
    ])
    facade = CodexExecFacade(workspace="/tmp", _client=fake)
    resp = facade.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.tool_calls == []


def test_facade_extracts_goal_from_last_user_message():
    """The last user message becomes the goal arg passed to CodexExecClient.run."""
    fake = _FakeClient([
        CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "ok"}}),
        CodexEvent("turn.completed", {"usage": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}}),
    ])
    facade = CodexExecFacade(workspace="/tmp", _client=fake)
    facade.chat.completions.create(messages=[
        {"role": "system", "content": "you are codex"},
        {"role": "user", "content": "rename foo to bar"},
    ])
    assert fake.captured_goal == "rename foo to bar"
    assert fake.captured_workspace == "/tmp"


def test_facade_populates_usage_from_turn_completed():
    """turn.completed.usage flows into the OpenAI-shape usage object."""
    fake = _FakeClient([
        CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "ok"}}),
        CodexEvent("turn.completed", {"usage": {"input_tokens": 100, "output_tokens": 42, "cached_input_tokens": 8}}),
    ])
    facade = CodexExecFacade(workspace="/tmp", _client=fake)
    resp = facade.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 42
    assert resp.usage.total_tokens == 142
    assert resp.usage.prompt_tokens_details.cached_tokens == 8


# ---------------------------------------------------------------------------
# auxiliary_client.resolve_provider_client wiring for codex-exec
# ---------------------------------------------------------------------------


def test_facade_stream_yields_content_delta_and_terminator():
    """stream=True returns a 2-chunk OpenAI-shaped iterable: content + finish."""
    fake = _FakeClient([
        CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "result"}}),
        CodexEvent("turn.completed", {"usage": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 0}}),
    ])
    facade = CodexExecFacade(workspace="/tmp", _client=fake)
    stream = facade.chat.completions.create(
        messages=[{"role": "user", "content": "go"}],
        stream=True,
    )
    chunks = list(stream)
    assert len(chunks) == 2
    assert chunks[0].choices[0].delta.content == "result"
    assert chunks[0].choices[0].finish_reason is None
    assert chunks[1].choices[0].delta.content is None
    assert chunks[1].choices[0].finish_reason == "stop"
    assert chunks[1].usage.prompt_tokens == 10
    assert chunks[1].usage.completion_tokens == 5


def test_facade_inherits_progress_sink_from_contextvar():
    """If no explicit progress_callback is passed, the facade picks up the
    sink installed in the module-level ContextVar — this is how
    _spawn_detached_coder bridges Codex events back into the parent agent."""
    from agent.codex_exec_client import _FACADE_PROGRESS_SINK

    events = [
        CodexEvent("thread.started", {"thread_id": "t"}),
        CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "hi"}}),
        CodexEvent("turn.completed", {"usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}}),
    ]
    captured: list = []
    sink = lambda ev: captured.append((ev.event, ev.data))

    token = _FACADE_PROGRESS_SINK.set(sink)
    try:
        facade = CodexExecFacade(workspace="/tmp", _client=_FakeClient(list(events)))
        facade.chat.completions.create(messages=[{"role": "user", "content": "go"}])
    finally:
        _FACADE_PROGRESS_SINK.reset(token)

    assert [e for e, _ in captured] == ["thread.started", "item.completed", "turn.completed"]


def test_facade_explicit_callback_overrides_contextvar():
    """Explicit progress_callback constructor arg wins over the ContextVar."""
    from agent.codex_exec_client import _FACADE_PROGRESS_SINK

    sentinel_sink = lambda ev: None
    explicit = []

    token = _FACADE_PROGRESS_SINK.set(sentinel_sink)
    try:
        facade = CodexExecFacade(
            workspace="/tmp",
            progress_callback=lambda ev: explicit.append(ev.event),
            _client=_FakeClient([
                CodexEvent("item.completed", {"item": {"type": "agent_message", "text": "ok"}}),
                CodexEvent("turn.completed", {"usage": {}}),
            ]),
        )
    finally:
        _FACADE_PROGRESS_SINK.reset(token)

    facade.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert "item.completed" in explicit


def test_auxiliary_client_resolves_codex_exec_to_facade(monkeypatch):
    """resolve_provider_client(provider='codex-exec') returns CodexExecFacade."""
    from agent import auxiliary_client

    fake_creds = {
        "provider": "codex-exec",
        "api_key": "codex-exec",
        "base_url": "codex-exec://local",
        "command": "/usr/local/bin/codex",
        "args": ["exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write"],
        "source": "process",
    }
    import hermes_cli.auth

    monkeypatch.setattr(
        hermes_cli.auth,
        "resolve_external_process_provider_credentials",
        lambda provider: fake_creds,
    )

    client, model = auxiliary_client.resolve_provider_client(
        "codex-exec", model="gpt-5.4", async_mode=False
    )

    assert isinstance(client, CodexExecFacade)
    assert model == "gpt-5.4"
