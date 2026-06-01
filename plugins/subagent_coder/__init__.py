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
    _install_delegate_dispatch_wrap()
    # Task 6~8: auth resolver / Discord overlay / coder_spawn_callback slot.
    logger.info("subagent_coder: register(ctx) complete (provider + defaults + dispatch wrap)")


def _install_delegate_dispatch_wrap() -> None:
    """Runtime-wrap AIAgent._invoke_tool to inject parent_agent for the coder.

    Why a monkey-patch instead of editing run_agent.py: the registry dispatch
    path (model_tools.handle_function_call -> registry.dispatch) never forwards
    parent_agent to handlers — verified, and upstream's own delegate_task uses
    an inline _dispatch for exactly this reason. By wrapping here at register
    time, the coder works on a STOCK hermes (no run_agent.py edits), which is
    what makes subagent_coder installable as a standalone ~/.hermes/plugins/ unit.

    parent_agent is taken from ``self`` directly (not a ContextVar) so it
    survives the concurrent path's worker threads — ContextVars don't propagate
    across the ThreadPoolExecutor boundary (lesson from coder commit fd0d901a).

    NOTE: covers the concurrent path (_invoke_tool). The sequential path
    (_execute_tool_calls_sequential) dispatches inline and is wired separately.
    """
    import json

    from run_agent import AIAgent

    if getattr(AIAgent, "_subagent_coder_dispatch_wrapped", False):
        return

    _orig_invoke_tool = AIAgent._invoke_tool

    def _wrapped_invoke_tool(self, function_name, function_args, *args, **kwargs):
        if function_name == "delegate_task_background":
            from tools.delegate_tool import delegate_task_background
            return json.dumps(
                delegate_task_background(
                    parent_agent=self,
                    goal=function_args.get("goal"),
                    context=function_args.get("context") or "",
                ),
                ensure_ascii=False,
            )
        return _orig_invoke_tool(self, function_name, function_args, *args, **kwargs)

    AIAgent._invoke_tool = _wrapped_invoke_tool
    AIAgent._subagent_coder_dispatch_wrapped = True
    logger.info("subagent_coder: AIAgent._invoke_tool wrapped for coder dispatch")


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
