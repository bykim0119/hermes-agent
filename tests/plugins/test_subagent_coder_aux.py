"""subagent_coder.register(ctx)가 codex-exec를 _EXTERNAL_PROCESS_DEFAULTS에
주입해야 한다.

실제 정의 위치는 hermes_cli.auth._EXTERNAL_PROCESS_DEFAULTS (plan이 적은
agent.auxiliary_client가 아님). codex-exec 항목은 더 이상 auth.py에 하드코드되지
않고 plugin register 시점에 .update로 들어온다 — 코더 wiring을 한 plugin으로
모으는 P1 원칙과 일관 (provider 등록과 동일).
"""
from unittest.mock import MagicMock


def test_codex_exec_in_external_process_defaults_after_register():
    from hermes_cli.auth import _EXTERNAL_PROCESS_DEFAULTS

    # 격리: 다른 import로 이미 있을 수 있으니 제거 후 register만으로 복원되는지
    _EXTERNAL_PROCESS_DEFAULTS.pop("codex-exec", None)

    from plugins.subagent_coder import register

    register(MagicMock())

    assert "codex-exec" in _EXTERNAL_PROCESS_DEFAULTS, \
        "register(ctx)가 codex-exec를 _EXTERNAL_PROCESS_DEFAULTS에 주입하지 않음"
    entry = _EXTERNAL_PROCESS_DEFAULTS["codex-exec"]
    assert entry.get("default_command") == "codex"
    assert isinstance(entry.get("default_args"), list)
    assert "exec" in entry["default_args"]
    assert entry.get("args_env_var") == "HERMES_CODER_ARGS"
