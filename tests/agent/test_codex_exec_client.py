"""Tests for codex_exec_client — A1 path (codex exec --json) per Task 2 of
the hermes-coder-subagent plan."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from agent.codex_exec_client import CodexExecClient, CodexEvent


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
