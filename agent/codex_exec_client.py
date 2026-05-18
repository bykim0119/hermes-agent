"""Codex CLI client using `codex exec --json` (A1 path).

Spawns ``codex exec --json <goal>`` as a subprocess, parses NDJSON events
from stdout, and yields them as :class:`CodexEvent` instances. Used by the
hermes coder subagent when delegating coding tasks to Codex CLI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

FACADE_MARKER_BASE_URL = "codex-exec://local"


@dataclass
class CodexEvent:
    event: str
    data: dict


class CodexExecClient:
    def __init__(
        self,
        command: str = "codex",
        extra_args: Optional[list[str]] = None,
    ):
        self.command = command
        self.extra_args = list(extra_args or [])

    async def run(
        self,
        *,
        goal: str,
        workspace: str,
        env: Optional[dict] = None,
    ) -> AsyncIterator[CodexEvent]:
        """Run codex exec, yielding parsed events as they arrive."""
        argv = [self.command, "exec", "--json", *self.extra_args, goal]
        proc_env = {**os.environ, **(env or {})}
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if not text:
                continue
            try:
                obj = json.loads(text)
                ev_type = obj.get("event") or obj.get("type") or "unknown"
                yield CodexEvent(event=ev_type, data=obj)
            except json.JSONDecodeError:
                yield CodexEvent(event="raw", data={"text": text})
        await proc.wait()
        if proc.returncode != 0:
            stderr = (
                (await proc.stderr.read()).decode("utf-8", errors="replace")
                if proc.stderr
                else ""
            )
            yield CodexEvent(
                event="error",
                data={"returncode": proc.returncode, "stderr": stderr},
            )


# ---------------------------------------------------------------------------
# OpenAI-shape facade — lets auxiliary_client route to Codex like any provider
# ---------------------------------------------------------------------------


def _extract_goal(messages: list[dict[str, Any]]) -> str:
    """The last user message becomes the prompt arg for `codex exec`."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") in (None, "text")
                ]
                return "\n".join(t for t in parts if t)
    return ""


class _FacadeChatCompletions:
    def __init__(self, facade: "CodexExecFacade"):
        self._facade = facade

    def create(self, **kwargs: Any) -> Any:
        return self._facade._create_chat_completion(**kwargs)


class _FacadeChatNamespace:
    def __init__(self, facade: "CodexExecFacade"):
        self.completions = _FacadeChatCompletions(facade)


class CodexExecFacade:
    """OpenAI-client-compatible facade backed by `codex exec --json`.

    The whole Codex turn (multiple internal steps) is collapsed into one
    chat-completion call. ``finish_reason`` is always ``"stop"`` because Codex
    runs its own tool/agent loop internally; hermes' outer AIAgent treats the
    delegation as a single LLM call that happens to take minutes.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        workspace: str | None = None,
        progress_callback: Optional[Callable[[CodexEvent], None]] = None,
        _client: Any | None = None,
        **_: Any,
    ):
        self.api_key = api_key or "codex-exec"
        self.base_url = base_url or FACADE_MARKER_BASE_URL
        self._workspace = str(workspace or os.getcwd())
        self._progress_callback = progress_callback
        self._client = _client or CodexExecClient(
            command=command or "codex",
            extra_args=list(args or []),
        )
        self.chat = _FacadeChatNamespace(self)
        self.is_closed = False

    def close(self) -> None:
        self.is_closed = True

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Any:
        goal = _extract_goal(messages or [])
        content_parts: list[str] = []
        usage_in = 0
        usage_out = 0
        usage_cached = 0

        async def _consume() -> None:
            nonlocal usage_in, usage_out, usage_cached
            async for event in self._client.run(goal=goal, workspace=self._workspace):
                if self._progress_callback is not None:
                    try:
                        self._progress_callback(event)
                    except Exception:
                        logger.exception("codex-exec progress callback raised")
                if event.event == "item.completed":
                    item = event.data.get("item") or {}
                    if item.get("type") == "agent_message":
                        text = item.get("text") or ""
                        if text:
                            content_parts.append(text)
                elif event.event == "turn.completed":
                    usage = event.data.get("usage") or {}
                    usage_in += int(usage.get("input_tokens") or 0)
                    usage_out += int(usage.get("output_tokens") or 0)
                    usage_cached += int(usage.get("cached_input_tokens") or 0)

        asyncio.run(_consume())

        content = "\n".join(content_parts)
        usage = SimpleNamespace(
            prompt_tokens=usage_in,
            completion_tokens=usage_out,
            total_tokens=usage_in + usage_out,
            prompt_tokens_details=SimpleNamespace(cached_tokens=usage_cached),
        )
        assistant_message = SimpleNamespace(
            content=content,
            tool_calls=[],
            reasoning=None,
            reasoning_content=None,
            reasoning_details=None,
        )
        choice = SimpleNamespace(message=assistant_message, finish_reason="stop")
        return SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=model or "codex-exec",
        )
