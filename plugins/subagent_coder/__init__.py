"""subagent_coder plugin — Codex CLI 기반 코더 서브에이전트.

Wires (Task 2~8에서 차례로 채움):
- delegate_task_background tool (registry.register with parent_agent kw)
- codex-exec model provider
- Discord platform overlay (factory wrap + connect wrap)
- AIAgent.coder_spawn_callback slot

자세한 설계: ``coder-fork-isolation/2026-05-23-coder-fork-isolation-design.md``
"""
from __future__ import annotations

import logging

from . import codex_provider

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Plugin entry point — Hermes plugin system이 로드 시 ``register(ctx)``로 호출.

    ``ctx``는 ``PluginContext(manifest, manager)`` (hermes_cli/plugins.py).
    """
    logger.info("subagent_coder: register(ctx) started")
    codex_provider.register_codex_provider(ctx)
    # Task 4~8에서 tool/overlay/slot wire를 차례로 추가.
    logger.info("subagent_coder: register(ctx) complete (codex-exec provider)")
