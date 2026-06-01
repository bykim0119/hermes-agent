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
    _register_external_process_defaults()
    # Task 6~8에서 auth resolver/overlay/slot wire를 차례로 추가.
    logger.info("subagent_coder: register(ctx) complete (provider + ext-process defaults)")


def _register_external_process_defaults() -> None:
    """Inject codex-exec into hermes_cli.auth._EXTERNAL_PROCESS_DEFAULTS.

    Externalized from auth.py so coder wiring lives in this plugin. The dict is
    module-level mutable (verified Step 0.5), so .update at register time is safe.
    """
    from hermes_cli.auth import _EXTERNAL_PROCESS_DEFAULTS

    _EXTERNAL_PROCESS_DEFAULTS["codex-exec"] = {
        "command_env_vars": ("HERMES_CODER_COMMAND",),
        "default_command": "codex",
        "args_env_var": "HERMES_CODER_ARGS",
        "default_args": [
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
        ],
        "missing_cli_hint": (
            "Install OpenAI Codex CLI or set HERMES_CODER_COMMAND."
        ),
        "missing_cli_code": "missing_codex_cli",
        "remote_base_url_prefix": None,
    }
