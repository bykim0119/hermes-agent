"""Tests for gateway.coder_config — env > config > default priority resolver
and pre-spawn codex auth check.

Written TDD-style for Task 12: each test below was authored before the
implementation it covers.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# coder_setting: env > config > default priority
# ---------------------------------------------------------------------------


def test_env_var_takes_priority_over_config_and_default(monkeypatch):
    from gateway.coder_config import coder_setting

    monkeypatch.setenv("HERMES_TEST_CODER_X", "999")
    with patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {"x": 7}}},
    ):
        v = coder_setting("x", env_var="HERMES_TEST_CODER_X", default=1, cast=int)

    assert v == 999


def test_config_value_wins_when_env_unset(monkeypatch):
    from gateway.coder_config import coder_setting

    monkeypatch.delenv("HERMES_TEST_CODER_Y", raising=False)
    with patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {"y": 42}}},
    ):
        v = coder_setting("y", env_var="HERMES_TEST_CODER_Y", default=1, cast=int)

    assert v == 42


def test_default_wins_when_neither_env_nor_config_set(monkeypatch):
    from gateway.coder_config import coder_setting

    monkeypatch.delenv("HERMES_TEST_CODER_Z", raising=False)
    with patch("gateway.coder_config.load_config", return_value={}):
        v = coder_setting("z", env_var="HERMES_TEST_CODER_Z", default=250, cast=int)

    assert v == 250


def test_empty_env_string_is_ignored(monkeypatch):
    """Operators clear an env var by setting it empty in a systemd drop-in.
    We treat empty as 'unset' so the config wins, matching shell semantics
    where ``${X:-default}`` falls back on empty."""
    from gateway.coder_config import coder_setting

    monkeypatch.setenv("HERMES_TEST_CODER_W", "")
    with patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {"w": 10}}},
    ):
        v = coder_setting("w", env_var="HERMES_TEST_CODER_W", default=1, cast=int)

    assert v == 10


def test_invalid_env_cast_falls_back_to_config(monkeypatch):
    """A typo in env (e.g. ``MAX=three``) must not crash the bot. We log
    and continue to the next source rather than re-raising."""
    from gateway.coder_config import coder_setting

    monkeypatch.setenv("HERMES_TEST_CODER_BAD", "not-an-int")
    with patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {"bad": 5}}},
    ):
        v = coder_setting("bad", env_var="HERMES_TEST_CODER_BAD", default=99, cast=int)

    assert v == 5


def test_config_loader_exception_does_not_crash(monkeypatch):
    """``load_config`` may raise under tests / standalone scripts — we must
    still return the default rather than propagating."""
    from gateway.coder_config import coder_setting

    monkeypatch.delenv("HERMES_TEST_CODER_Q", raising=False)
    with patch(
        "gateway.coder_config.load_config",
        side_effect=RuntimeError("config unavailable"),
    ):
        v = coder_setting("q", env_var="HERMES_TEST_CODER_Q", default=77, cast=int)

    assert v == 77


def test_cast_is_applied_to_string_config_value(monkeypatch):
    """yaml.safe_load may surface a stringly-typed value (e.g. quoted
    ``"250"`` in config). The cast must run on whichever source produced
    the value."""
    from gateway.coder_config import coder_setting

    monkeypatch.delenv("HERMES_TEST_CODER_S", raising=False)
    with patch(
        "gateway.coder_config.load_config",
        return_value={"delegation": {"coder": {"s": "250"}}},
    ):
        v = coder_setting("s", env_var="HERMES_TEST_CODER_S", default=0, cast=int)

    assert v == 250


# ---------------------------------------------------------------------------
# check_codex_auth: pre-spawn validation
# ---------------------------------------------------------------------------


def test_check_codex_auth_returns_none_when_file_present_and_valid(tmp_path, monkeypatch):
    """Happy path: a usable auth.json yields None (proceed with spawn)."""
    from gateway import coder_config

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {}}))
    monkeypatch.setenv("HOME", str(home))

    assert coder_config.check_codex_auth() is None


def test_check_codex_auth_flags_missing_file(tmp_path, monkeypatch):
    """No auth.json at all is the most common operator failure — we tell
    them what command to run, not just 'auth missing'."""
    from gateway import coder_config

    home = tmp_path / "no-codex"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    msg = coder_config.check_codex_auth()
    assert msg is not None
    assert "auth.json" in msg


def test_check_codex_auth_flags_expired_token(tmp_path, monkeypatch):
    """A past ``expires_at`` is the deterministic ``codex login again`` cue.
    Without this pre-check the user would see codex bail mid-stream with
    a returncode that doesn't say 'expired'."""
    from gateway import coder_config

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    expired = "2020-01-01T00:00:00Z"
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"tokens": {"expires_at": expired}})
    )
    monkeypatch.setenv("HOME", str(home))

    msg = coder_config.check_codex_auth()
    assert msg is not None
    assert "만료" in msg or "expired" in msg.lower()


def test_check_codex_auth_tolerates_malformed_file(tmp_path, monkeypatch):
    """A broken auth.json is suspicious but codex's own error surface is
    richer. We defer rather than misdiagnose."""
    from gateway import coder_config

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("not-json{{{")
    monkeypatch.setenv("HOME", str(home))

    assert coder_config.check_codex_auth() is None
