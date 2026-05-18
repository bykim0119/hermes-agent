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
