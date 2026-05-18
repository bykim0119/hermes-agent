"""Format coder subagent progress events into emoji-prefixed Discord messages."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Optional

MAX_CHUNK_CHARS = 3500


def format_event(event: dict) -> Optional[str]:
    """Convert a subagent_progress event dict into a thread message string.

    Returns None for events that should not be rendered.
    Caller is responsible for debounce / batching.
    """
    et = event.get("event")
    if et == "tool_call":
        tool = event.get("tool", "")
        if tool == "read_file":
            return f"🔧 reading {event.get('path', '?')}"
        if tool == "edit_file":
            path = event.get("path", "?")
            added = event.get("added")
            removed = event.get("removed")
            if added is not None or removed is not None:
                return f"✏️ editing {path} (+{added or 0} -{removed or 0})"
            return f"✏️ editing {path}"
        if tool == "terminal":
            cmd = event.get("command", "")
            return f"▶️ $ {cmd}"
        return f"🔧 {tool}"
    if et == "text_delta":
        text = event.get("text", "")
        return _cap(text)
    if et == "turn_complete":
        summary = event.get("summary", "")
        return f"✅ 완료 — {summary}" if summary else "✅ 완료"
    if et == "error":
        msg = event.get("message", "(unknown error)")
        return f"❌ {_cap(msg)}"
    if et == "warning":
        return f"⚠️ {_cap(event.get('message', ''))}"
    if et == "plan":
        return f"📌 plan: {_cap(event.get('text', ''))}"
    return None


def _cap(text: str) -> str:
    if len(text) <= MAX_CHUNK_CHARS:
        return text
    return text[:MAX_CHUNK_CHARS] + "…[truncated]"


class DebouncedFlusher:
    """Collect short messages per thread and flush every ``interval_ms``.

    Caller schedules events via :meth:`add`. A background asyncio task
    flushes accumulated buffers by invoking the publish coroutine.
    """

    def __init__(
        self,
        interval_ms: int = 250,
        publish: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        self.interval = interval_ms / 1000.0
        self.publish = publish
        self._buffers: dict[str, list[str]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def add(self, thread_id: str, text: str) -> None:
        async with self._lock:
            self._buffers[thread_id].append(text)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            async with self._lock:
                snapshots = {
                    tid: "\n".join(parts)
                    for tid, parts in self._buffers.items()
                    if parts
                }
                self._buffers.clear()
            for tid, body in snapshots.items():
                if self.publish:
                    try:
                        await self.publish(tid, body)
                    except Exception:
                        pass
