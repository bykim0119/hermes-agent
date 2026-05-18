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
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


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
