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
    # Import the coder delegation module — this registers the
    # delegate_task_background tool on the registry at import time.
    from . import delegate_background  # noqa: F401
    _install_delegate_dispatch_wrap()
    _install_coder_child_wraps()
    # Task 6~8: auth resolver / Discord overlay / coder_spawn_callback slot.
    logger.info(
        "subagent_coder: register(ctx) complete "
        "(provider + defaults + delegate_background + dispatch/child wraps)"
    )


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
            from plugins.subagent_coder.delegate_background import delegate_task_background
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


def _install_coder_child_wraps() -> None:
    """Runtime-wrap the stock child builders so the coder child gets its
    codex-exec provider, chat_completions api_mode, and a ``_subagent_id``
    pinned to ``coder_run_id`` — without editing tools/delegate_tool.py.

    Stock ``delegate_task`` has no ``override_provider``/``subagent_id_override``
    params, so the coder can't pass them. Instead ``_spawn_detached_coder`` sets
    the ``_coder_child_ctx`` ContextVar and these two wraps read it:

      * ``_build_child_agent``: PRE-inject ``override_provider``/``override_api_mode``
        (codex-exec is process-backed and only does chat.completions — inheriting
        the parent's codex_responses mode crashes the facade), then POST-pin
        ``child._subagent_id`` so ``_run_single_child``/``interrupt_subagent``/
        Discord routing all key off ``coder_run_id``.
      * ``_build_child_progress_callback``: replace the internally generated
        ``subagent_id`` with ``coder_run_id`` so every relayed event routes to
        the matching Discord thread.

    Both are module-level names in tools.delegate_tool, so the internal calls
    inside ``_build_child_agent`` resolve to the wrapped versions at call time.
    The coder spawn is single-task → ``_build_child_agent``, the callback, and
    ``_run_single_child`` all run on the ``_runner`` thread inline, so the
    ContextVar propagates (no ThreadPoolExecutor boundary).
    """
    import tools.delegate_tool as dt
    from plugins.subagent_coder.delegate_background import _coder_child_ctx

    if getattr(dt, "_subagent_coder_child_wrapped", False):
        return

    _orig_build_child_agent = dt._build_child_agent
    _orig_build_progress_cb = dt._build_child_progress_callback

    def _wrapped_build_child_agent(*args, **kwargs):
        ctx = _coder_child_ctx.get()
        if ctx is not None:
            kwargs["override_provider"] = ctx["provider"]
            kwargs["override_api_mode"] = ctx["api_mode"]
        child = _orig_build_child_agent(*args, **kwargs)
        if ctx is not None:
            child._subagent_id = ctx["subagent_id"]
        return child

    def _wrapped_build_progress_cb(*args, **kwargs):
        ctx = _coder_child_ctx.get()
        if ctx is not None and "subagent_id" in kwargs:
            kwargs["subagent_id"] = ctx["subagent_id"]
        return _orig_build_progress_cb(*args, **kwargs)

    dt._build_child_agent = _wrapped_build_child_agent
    dt._build_child_progress_callback = _wrapped_build_progress_cb
    dt._subagent_coder_child_wrapped = True
    logger.info(
        "subagent_coder: _build_child_agent / _build_child_progress_callback wrapped"
    )


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
