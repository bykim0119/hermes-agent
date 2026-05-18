"""OpenAI Codex CLI provider profile.

codex-exec runs an external `codex exec --json` subprocess that drives an
internal agent loop and emits NDJSON events. The CodexExecFacade
(agent/codex_exec_client.py) wraps it in an OpenAI chat-completion shape so
hermes' AIAgent can treat the whole Codex turn as a single LLM call.
"""

from providers import register_provider
from providers.base import ProviderProfile


class CodexExecProfile(ProviderProfile):
    """Codex CLI — external process, no REST models endpoint."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Model listing is handled by the Codex CLI subprocess."""
        return None


codex_exec = CodexExecProfile(
    name="codex-exec",
    aliases=(),
    api_mode="chat_completions",
    env_vars=(),  # Managed by the Codex CLI subprocess (~/.codex/auth.json)
    base_url="codex-exec://local",
    auth_type="external_process",
)

register_provider(codex_exec)
